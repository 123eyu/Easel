"""二维码裁剪、编码、平台拦截及 Web 登录进程的离线回归。"""
import ast
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import segno
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/shared/scripts"
sys.path.insert(0, str(SCRIPTS))
import login_qr
import login_state
import web_publisher
import xhs_publish


def qr_bytes(scale=3):
    output = io.BytesIO()
    segno.make("easel-offline-login-test", micro=False).save(
        output, kind="png", scale=scale, border=4)
    return output.getvalue()


@pytest.mark.parametrize("scale", [2, 3, 5, 8])
def test_small_qr_is_cropped_padded_and_still_detectable(tmp_path, scale):
    import cv2

    output = tmp_path / "qr.png"
    assert login_qr.save_qr(qr_bytes(scale), output)
    with Image.open(output) as image:
        assert min(image.size) >= 360
        assert image.getpixel((0, 0)) == (255, 255, 255)
    found, _ = cv2.QRCodeDetector().detect(cv2.imread(str(output)))
    assert found


def test_large_login_panel_is_not_saved_as_qr(tmp_path):
    panel = Image.new("RGB", (1000, 700), "white")
    panel.paste(Image.open(io.BytesIO(qr_bytes())), (700, 400))
    source = io.BytesIO()
    panel.save(source, format="PNG")
    output = tmp_path / "qr.png"
    assert login_qr.save_qr(source.getvalue(), output)
    with Image.open(output) as image:
        assert max(image.size) < 500


def test_non_qr_image_does_not_create_output(tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (300, 300), "red").save(image, format="PNG")
    output = tmp_path / "qr.png"
    assert not login_qr.save_qr(image.getvalue(), output)
    assert not output.exists()


def qr_element(image=None, visible=True):
    return SimpleNamespace(
        is_visible=lambda: visible,
        bounding_box=lambda: {"width": 100, "height": 100},
        screenshot=lambda **kwargs: image or qr_bytes())


def test_zhihu_strict_capture_accepts_small_qr_before_container(tmp_path):
    selectors = []

    def query(selector):
        selectors.append(selector)
        return [qr_element()] if selector == ".Qrcode-img" else []

    page = SimpleNamespace(query_selector_all=query)
    output = tmp_path / "qr.png"
    web_publisher._crop_qr(page, output, web_publisher.PLATFORMS["zhihu"])
    assert output.exists()
    assert ".Qrcode-img" in selectors


def test_zhihu_never_falls_back_to_whole_page(tmp_path):
    screenshot = Mock()
    page = SimpleNamespace(query_selector_all=lambda selector: [],
                           wait_for_timeout=Mock(), screenshot=screenshot)
    output = tmp_path / "qr.png"
    with pytest.raises(RuntimeError, match="未找到可识别"):
        web_publisher._crop_qr(page, output, web_publisher.PLATFORMS["zhihu"])
    screenshot.assert_not_called()
    assert not output.exists()


def test_qr_capture_retries_unloaded_image_and_skips_hidden(tmp_path):
    attempts = []

    def screenshot(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("image not loaded")
        return qr_bytes()

    visible = qr_element()
    visible.screenshot = screenshot
    hidden = qr_element(visible=False)
    page = SimpleNamespace(
        query_selector_all=lambda selector: [hidden, visible] if selector == ".qrcode-img" else [],
        wait_for_timeout=Mock())
    login_qr.capture_qr(page, tmp_path / "qr.png", ".qrcode-img")
    assert len(attempts) == 2
    page.wait_for_timeout.assert_called_once_with(800)


@pytest.mark.parametrize("script", ["xhs_publish.py", "web_publisher.py"])
def test_gbk_stdio_does_not_crash_runner(tmp_path, script):
    env = dict(os.environ, PYTHONIOENCODING="gbk:strict", PYTHONUTF8="0")
    result = subprocess.run([sys.executable, str(SCRIPTS / script), "selftest"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert "✅" in result.stdout.decode("utf-8")


def test_configure_stdio_handles_captured_or_closed_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    login_state.configure_stdio()


def test_window_login_is_valid_status(tmp_path):
    status = str(tmp_path / "status.json")
    login_state.write_status(status, "window_login", "请在窗口登录")
    assert login_state.read_status(status)["state"] == "window_login"


def xhs_page(url=xhs_publish.EXPLORE_URL, title="", body=""):
    page = SimpleNamespace(
        url=url, title=lambda: title, inner_text=lambda *args, **kwargs: body,
        query_selector_all=lambda selector: [], wait_for_timeout=Mock(),
        wait_for_selector=Mock())

    def goto(destination, **kwargs):
        page.url = destination

    page.goto = Mock(side_effect=goto)
    return page


@pytest.mark.parametrize("url,title,body,risk", [
    ("https://www.xiaohongshu.com/website-login/error?error_code=300012", "", "", True),
    ("https://www.xiaohongshu.com/website-login/error", "安全限制", "IP 存在风险", True),
    ("https://www.xiaohongshu.com/website-login/error?error_code=300013", "安全限制", "", False),
    ("https://www.xiaohongshu.com/website-login/error", "安全限制", "请完成验证", False),
    ("https://www.xiaohongshu.com/explore?next=error_code=300012", "", "", False),
])
def test_only_explicit_ip_risk_is_classified_as_ip(url, title, body, risk):
    page = xhs_page(url, title, body)
    assert xhs_publish._risk_blocked(page) is risk
    reason = xhs_publish._login_error(page)
    if "300013" in url:
        assert "300013" in reason
        assert "不一定是 IP" in reason


def test_error_message_does_not_leak_url_login_tokens():
    page = xhs_page("https://www.xiaohongshu.com/website-login/error?error_code=300013&token=private")
    assert "private" not in xhs_publish._login_error(page)


@pytest.mark.parametrize("url,visible,logged", [
    (xhs_publish.EXPLORE_URL, False, False),
    (xhs_publish.EXPLORE_URL, True, True),
    (xhs_publish.CREATOR_LOGIN_URL, False, False),
    ("https://creator.xiaohongshu.com/website-login/error", False, False),
    ("https://creator.xiaohongshu.com/publish/publish", False, True),
    ("https://creator.xiaohongshu.com/publish/publish?error_code=300012", False, False),
    ("https://creator.xiaohongshu.com/home-error", False, False),
    ("https://other.example/?next=creator.xiaohongshu.com/home", False, False),
])
def test_xhs_requires_visible_anchor_or_creator_authenticated_route(url, visible, logged):
    page = xhs_page(url)
    page.query_selector_all = lambda selector: [SimpleNamespace(is_visible=lambda: visible)]
    assert xhs_publish._is_logged_in(page) is logged


def test_xhs_headless_risk_falls_back_to_creator_once(tmp_path, monkeypatch):
    page = xhs_page("https://www.xiaohongshu.com/website-login/error?error_code=300012")
    capture = Mock()
    monkeypatch.setattr(login_qr, "capture_qr", capture)
    assert xhs_publish._capture_login_qr(page, tmp_path / "qr.png")
    page.goto.assert_called_once_with(xhs_publish.CREATOR_LOGIN_URL,
                                     wait_until="domcontentloaded", timeout=45000)
    capture.assert_called_once()


def test_xhs_missing_qr_also_tries_official_creator(tmp_path, monkeypatch):
    page = xhs_page()
    capture = Mock(side_effect=[RuntimeError("not ready"), None])
    monkeypatch.setattr(login_qr, "capture_qr", capture)
    assert xhs_publish._capture_login_qr(page, tmp_path / "qr.png")
    assert capture.call_count == 2
    assert page.goto.call_count == 1


def test_xhs_both_entries_blocked_report_actual_risk(tmp_path, monkeypatch):
    page = xhs_page("https://www.xiaohongshu.com/website-login/error?error_code=300012")
    page.goto.side_effect = lambda *args, **kwargs: None
    capture = Mock()
    monkeypatch.setattr(login_qr, "capture_qr", capture)
    with pytest.raises(RuntimeError, match="300012"):
        xhs_publish._capture_login_qr(page, tmp_path / "qr.png")
    assert page.goto.call_count == 1
    capture.assert_not_called()


def test_creator_existing_login_does_not_request_qr(tmp_path, monkeypatch):
    page = xhs_page("https://creator.xiaohongshu.com/home")
    capture = Mock()
    monkeypatch.setattr(login_qr, "capture_qr", capture)
    assert not xhs_publish._capture_login_qr(page, tmp_path / "qr.png")
    capture.assert_not_called()


@pytest.mark.parametrize("capture_result,expected", [
    (RuntimeError("平台拦截（300013）"), "error"),
    (False, "success"),
    (True, "expired"),
])
def test_xhs_runner_persists_terminal_status_and_closes_browser(tmp_path, monkeypatch,
                                                              capture_result, expected):
    from playwright import sync_api

    page = xhs_page()
    context = SimpleNamespace(pages=[page], close=Mock())

    class Manager:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(sync_api, "sync_playwright", Manager)
    monkeypatch.setattr(xhs_publish, "_launch", lambda *args, **kwargs: context)
    capture = Mock(side_effect=capture_result) if isinstance(capture_result, Exception) \
        else Mock(return_value=capture_result)
    monkeypatch.setattr(xhs_publish, "_capture_login_qr", capture)
    args = SimpleNamespace(qr_out=str(tmp_path / "qr.png"), timeout=-1,
                           status_file=str(tmp_path / "status.json"),
                           profile_base=str(tmp_path / "profiles"), headed=False,
                           proxy=None, no_proxy=True)
    assert xhs_publish.cmd_login(args) == (0 if expected == "success" else 1)
    status = login_state.read_status(args.status_file)
    assert status["state"] == expected
    if expected == "error":
        assert "300013" in status["message"]
    context.close.assert_called_once()


def web_functions():
    tree = ast.parse((ROOT / "web/app.py").read_text(encoding="utf-8"))
    names = {"api_login_start", "_login_status"}
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in nodes:
        node.decorator_list = []
    env = {"json": json, "subprocess": subprocess, "sys": sys, "asyncio": asyncio}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "web/app.py", "exec"), env)
    return env


@pytest.mark.parametrize("state", ["starting", "qr_ready", "scanned", "window_login", "verifying"])
def test_web_detects_exited_login_even_after_qr_ready(tmp_path, state):
    env = web_functions()
    login_state.write_status(str(tmp_path / "zhihu.json"), state)
    (tmp_path / "zhihu.png").write_bytes(b"stale")
    env.update(LOGIN_DIR=tmp_path, LOGIN_PROCESSES={"zhihu": SimpleNamespace(poll=lambda: 1)})
    status = env["_login_status"]("zhihu")
    assert status["state"] == "error"
    assert status["qr"] == ""


@pytest.mark.parametrize("state", ["success", "expired", "error"])
def test_web_preserves_terminal_status_and_hides_stale_qr(tmp_path, state):
    env = web_functions()
    login_state.write_status(str(tmp_path / "zhihu.json"), state)
    (tmp_path / "zhihu.png").write_bytes(b"stale")
    env.update(LOGIN_DIR=tmp_path, LOGIN_PROCESSES={"zhihu": SimpleNamespace(poll=lambda: 0)})
    status = env["_login_status"]("zhihu")
    assert status["state"] == state
    assert status["qr"] == ""


def test_repeated_web_login_preserves_existing_process_and_qr():
    env = web_functions()
    status = {"state": "qr_ready", "qr": "_login/zhihu.png"}
    env.update(LOGIN_RUNNERS={"zhihu": {"backend": "web", "wp": "zhihu"}},
               LOGIN_PROCESSES={"zhihu": SimpleNamespace(poll=lambda: None)},
               _login_status=lambda platform: status)
    assert asyncio.run(env["api_login_start"]("zhihu")) == {"mode": "qr", **status}


def test_web_login_sets_child_utf8_without_changing_parent_env(tmp_path, monkeypatch):
    env = web_functions()
    original = {"PYTHONIOENCODING": "gbk", "https_proxy": "test-proxy"}
    popen = Mock(return_value=SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(subprocess, "Popen", popen)

    async def sleep(duration):
        return None

    env.update(LOGIN_RUNNERS={"zhihu": {"backend": "web", "wp": "zhihu"}},
               LOGIN_PROCESSES={}, LOGIN_DIR=tmp_path, SHARED_SCRIPTS=SCRIPTS,
               PROJECT_ROOT=ROOT, LOGIN_TIMEOUT=180, _WHOAMI_LOCK=threading.Lock(),
               _WHOAMI_CACHE={}, _proxy_env=lambda: original.copy(),
               _login_status=lambda platform: {"state": "qr_ready", "qr": ""},
               asyncio=SimpleNamespace(sleep=sleep))
    asyncio.run(env["api_login_start"]("zhihu"))
    child_env = popen.call_args.kwargs["env"]
    assert child_env["PYTHONIOENCODING"] == "utf-8"
    assert child_env["PYTHONUTF8"] == "1"
    assert original["PYTHONIOENCODING"] == "gbk"
    assert child_env["https_proxy"] == "test-proxy"
    assert popen.call_args.kwargs["stdout"].closed
