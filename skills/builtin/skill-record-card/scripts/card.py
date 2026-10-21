#!/usr/bin/env python3
"""card.py — 记录卡片的确定性落盘与版本管理（纯标准库）。

设计约定：卡片以用户亲口表达为底稿，AI 可补充观点、提炼拔高、指出盲点，
但所有 AI 贡献显式记录于"AI 补充与提炼"区、经用户确认采纳后才落盘。
本脚本不生成内容，只负责编号、落盘、版本演进与查询。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared" / "scripts"))
from output_paths import validate_output_path  # noqa: E402

FIELDS = ("point", "mechanism", "method", "scope_use", "scope_nouse")
HEADERS = {
    "point": "核心要点",
    "mechanism": "机理",
    "method": "具体方法",
    "scope_use": "使用范围·适用",
    "scope_nouse": "使用范围·不适用",
}
HEADER_TO_FIELD = {v: k for k, v in HEADERS.items()}
CARD_RE = re.compile(r"^card-(\d{3})\.md$")
VERSION_RE = re.compile(r"^## v(\d+) · (\d{4}-\d{2}-\d{2})\s*$")


# --------------------------------------------------------------------------- #
# 纯函数：渲染与解析（不碰 outputs/ 规约，便于 selftest）
# --------------------------------------------------------------------------- #
def render_version(num: int, day: str, fields: dict[str, str],
                   quotes: list[str], change: str | None,
                   ai_notes: list[str]) -> str:
    lines = [f"## v{num} · {day}", ""]
    if change:
        lines += [f"> 变更：{change}", ""]
    for field in FIELDS:
        lines += [f"### {HEADERS[field]}", fields[field].strip(), ""]
    lines.append("### 原话摘录")
    if quotes:
        lines += [f"> {q.strip()}" for q in quotes if q.strip()]
    else:
        lines.append("> （本次访谈无摘录）")
    lines.append("")
    lines.append("### AI 补充与提炼")
    if ai_notes:
        lines += [f"> {n.strip()}" for n in ai_notes if n.strip()]
    else:
        lines.append("> （本版无 AI 补充）")
    lines.append("")
    return "\n".join(lines)


def render_card(card_id: str, title: str, did: str, created: str,
                versions: list[str]) -> str:
    head = (f"# 记录卡片 {card_id}：{title}\n\n"
            f"- 做的事：{did}\n- 创建：{created}\n- 当前版本：v{len(versions)}\n\n---\n\n")
    return head + "\n".join(versions)


def parse_versions(text: str) -> list[dict]:
    """把卡片文件解析为版本列表：{num, day, change, fields{}, quotes[], ai_notes[]}。"""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if VERSION_RE.match(ln)]
    versions: list[dict] = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = lines[start:end]
        num, day = VERSION_RE.match(block[0]).groups()
        change = None
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for ln in block[1:]:
            if ln.startswith("> 变更："):
                change = ln.removeprefix("> 变更：").strip()
                continue
            if ln.startswith("### "):
                current = ln[4:].strip()
                sections[current] = []
                continue
            if current is not None:
                sections[current].append(ln)
        fields: dict[str, str] = {}
        quotes: list[str] = []
        ai_notes: list[str] = []
        for header, body in sections.items():
            content = "\n".join(body).strip()
            if header == "原话摘录":
                quotes = [q[2:].strip() for q in content.splitlines()
                          if q.startswith("> ") and q[2:].strip() != "（本次访谈无摘录）"]
            elif header == "AI 补充与提炼":
                ai_notes = [q[2:].strip() for q in content.splitlines()
                            if q.startswith("> ") and q[2:].strip() != "（本版无 AI 补充）"]
            elif header in HEADER_TO_FIELD:
                fields[HEADER_TO_FIELD[header]] = content
        versions.append({"num": int(num), "day": day, "change": change,
                         "fields": fields, "quotes": quotes,
                         "ai_notes": ai_notes})
    return versions


def parse_header(text: str) -> dict[str, str]:
    info = {"title": "", "did": "", "created": ""}
    m = re.search(r"^# 记录卡片 (card-\d{3})：(.+)$", text, re.M)
    if m:
        info["title"] = m.group(2).strip()
    for key, pat in (("did", r"^- 做的事：(.+)$"), ("created", r"^- 创建：(\S+)")):
        m = re.search(pat, text, re.M)
        if m:
            info[key] = m.group(1).strip()
    return info


# --------------------------------------------------------------------------- #
# 目录级操作（selftest 直接调用；CLI 经 outputs 规约解析目录后调用）
# --------------------------------------------------------------------------- #
def _next_id(cards_dir: Path) -> str:
    nums = [int(m.group(1)) for p in cards_dir.glob("card-*.md")
            if (m := CARD_RE.match(p.name))]
    return f"card-{(max(nums) + 1) if nums else 1:03d}"


def _find_card(cards_dir: Path, card: str) -> Path:
    num = re.sub(r"\D", "", card)
    path = cards_dir / f"card-{int(num):03d}.md" if num else cards_dir / card
    if not path.is_file():
        raise SystemExit(f"找不到卡片 {card}（{cards_dir}）；用 list 查看现有卡片")
    return path


def create_card(cards_dir: Path, *, title: str, did: str,
                fields: dict[str, str], quotes: list[str],
                ai_notes: list[str], day: str) -> tuple[Path, str]:
    missing = [HEADERS[f] for f in FIELDS if not fields.get(f, "").strip()]
    if missing:
        raise SystemExit(f"缺少已确认的字段：{'、'.join(missing)}；"
                         "内容须经访谈并由用户确认，不能留空凑数")
    cards_dir.mkdir(parents=True, exist_ok=True)
    card_id = _next_id(cards_dir)
    version = render_version(1, day, fields, quotes, None, ai_notes)
    path = cards_dir / f"{card_id}.md"
    path.write_text(render_card(card_id, title, did, day, [version]), encoding="utf-8")
    return path, card_id


def revise_card(cards_dir: Path, card: str, *, change: str,
                overrides: dict[str, str], new_quotes: list[str],
                new_ai_notes: list[str], day: str) -> tuple[Path, int]:
    path = _find_card(cards_dir, card)
    text = path.read_text(encoding="utf-8")
    versions = parse_versions(text)
    if not versions:
        raise SystemExit(f"{path.name} 中没有可解析的版本块，无法升级")
    latest = versions[-1]
    fields = dict(latest["fields"])
    fields.update({k: v for k, v in overrides.items() if v and v.strip()})
    missing = [HEADERS[f] for f in FIELDS if not fields.get(f, "").strip()]
    if missing:
        raise SystemExit(f"升级后字段不完整：{'、'.join(missing)}；请补访这些段落")
    quotes = latest["quotes"] + [q for q in new_quotes if q.strip()]
    ai_notes = latest.get("ai_notes", []) + [n for n in new_ai_notes if n.strip()]
    num = latest["num"] + 1
    header_end = text.index(f"## v{versions[0]['num']} ·")
    head = re.sub(r"^- 当前版本：v\d+$", f"- 当前版本：v{num}",
                  text[:header_end], flags=re.M)
    body = "\n\n".join(v for v in
                       [text[header_end:].rstrip(),
                        render_version(num, day, fields, quotes, change, ai_notes)])
    path.write_text(head + body + "\n", encoding="utf-8")
    return path, num


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cards_dir(topic: str) -> Path:
    probe = validate_output_path(f"outputs/{topic}/assets/cards/.keep",
                                 create_parent=True)
    return probe.parent


def _merge_data(args) -> dict[str, object]:
    data: dict[str, object] = {}
    if getattr(args, "data", None):
        data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    merged = {
        "title": args.title or data.get("title", ""),
        "did": args.did or data.get("did", ""),
        "quotes": list(data.get("quotes", [])) + (args.quote or []),
        "ai_notes": list(data.get("ai_notes", [])) + (args.ai_note or []),
    }
    for field in FIELDS:
        cli_val = getattr(args, field, None)
        merged[field] = cli_val or data.get(field, "")
    return merged


def cmd_new(args) -> None:
    merged = _merge_data(args)
    if not merged["title"] or not merged["did"]:
        raise SystemExit("--title 与 --did 必填（用户确认后的文字）")
    fields = {f: str(merged[f]) for f in FIELDS}
    path, card_id = create_card(_cards_dir(args.topic), title=str(merged["title"]),
                                did=str(merged["did"]), fields=fields,
                                quotes=list(merged["quotes"]),
                                ai_notes=list(merged["ai_notes"]), day=args.date)
    print(json.dumps({"ok": True, "card": card_id, "version": 1,
                      "path": str(path)}, ensure_ascii=False))


def cmd_revise(args) -> None:
    overrides = {f: getattr(args, f) for f in FIELDS if getattr(args, f)}
    if not overrides and not args.quote and not args.ai_note:
        raise SystemExit("revise 至少携带一个更新字段、--quote 新摘录或 --ai-note 补充说明")
    path, num = revise_card(_cards_dir(args.topic), args.card,
                            change=args.change, overrides=overrides,
                            new_quotes=args.quote or [],
                            new_ai_notes=args.ai_note or [], day=args.date)
    print(json.dumps({"ok": True, "card": path.stem, "version": num,
                      "path": str(path)}, ensure_ascii=False))


def cmd_list(args) -> None:
    cards_dir = _cards_dir(args.topic)
    rows = []
    for path in sorted(cards_dir.glob("card-*.md")):
        text = path.read_text(encoding="utf-8")
        info = parse_header(text)
        versions = parse_versions(text)
        rows.append((path.stem, info["title"], f"v{len(versions)}",
                     versions[-1]["day"] if versions else "-",
                     info["did"][:24]))
    if not rows:
        print(f"outputs/{args.topic}/assets/cards/ 下还没有卡片")
        return
    print(f"{'卡片':<10} {'版本':<5} {'最近更新':<12} 标题 / 做的事")
    for stem, title, ver, day, did in rows:
        print(f"{stem:<10} {ver:<5} {day:<12} {title} — {did}")


def cmd_show(args) -> None:
    print(_find_card(_cards_dir(args.topic), args.card).read_text(encoding="utf-8"))


def _selftest() -> int:
    day = "2026-10-21"
    with tempfile.TemporaryDirectory() as tmp:
        cards = Path(tmp)
        fields = {"point": "先跑通再优化", "mechanism": "因为早期反馈比完美方案值钱",
                  "method": "1. 写最小版本 2. 发给三个人 3. 收反馈",
                  "scope_use": "从零到一的新事", "scope_nouse": "强合规场景"}
        path, card_id = create_card(cards, title="最小可行验证", did="上线 demo",
                                    fields=fields, quotes=["先跑通再优化"],
                                    ai_notes=["补充盲点：样本量太小时反馈有偏"],
                                    day=day)
        assert card_id == "card-001" and path.is_file()
        text = path.read_text(encoding="utf-8")
        parsed = parse_versions(text)
        assert len(parsed) == 1 and parsed[0]["fields"]["point"] == "先跑通再优化"
        assert parsed[0]["quotes"] == ["先跑通再优化"]
        assert parsed[0]["ai_notes"] == ["补充盲点：样本量太小时反馈有偏"]

        path2, num = revise_card(cards, "1", change="补充机理的因果链",
                                 overrides={"mechanism": "反馈→修正→再反馈，循环成本低"},
                                 new_quotes=["反馈是燃料"],
                                 new_ai_notes=["提炼：反馈循环即复利"],
                                 day="2026-10-22")
        assert num == 2 and path2 == path
        versions = parse_versions(path.read_text(encoding="utf-8"))
        assert len(versions) == 2 and versions[1]["change"] == "补充机理的因果链"
        assert versions[1]["fields"]["point"] == "先跑通再优化"  # 未改的继承
        assert versions[1]["fields"]["mechanism"] == "反馈→修正→再反馈，循环成本低"
        assert versions[1]["quotes"] == ["先跑通再优化", "反馈是燃料"]
        assert versions[1]["ai_notes"] == ["补充盲点：样本量太小时反馈有偏",
                                           "提炼：反馈循环即复利"]
        assert "当前版本：v2" in path.read_text(encoding="utf-8")

        _, card_id2 = create_card(cards, title="第二张", did="又做了一件事",
                                  fields=fields, quotes=[], ai_notes=[], day=day)
        assert card_id2 == "card-002"

        try:
            create_card(cards, title="空卡片", did="x",
                        fields={f: "" for f in FIELDS}, quotes=[], ai_notes=[],
                        day=day)
        except SystemExit:
            pass
        else:
            raise AssertionError("空字段必须被拒绝")
    print("card.py selftest: OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="记录卡片确定性存档与版本管理")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_fields(sp) -> None:
        sp.add_argument("--point", help="核心要点（用户确认稿）")
        sp.add_argument("--mechanism", help="机理（用户确认稿）")
        sp.add_argument("--method", help="具体方法（用户确认稿）")
        sp.add_argument("--scope-use", help="使用范围·适用（用户确认稿）")
        sp.add_argument("--scope-nouse", help="使用范围·不适用（用户确认稿）")
        sp.add_argument("--quote", action="append", help="用户原话摘录，可多次")
        sp.add_argument("--ai-note", action="append",
                        help="AI 补充与提炼说明（经用户确认采纳的），可多次")
        sp.add_argument("--date", default=date.today().isoformat(),
                        help="版本日期，默认今天")

    n = sub.add_parser("new", help="新建卡片（v1）")
    n.add_argument("--topic", required=True, help="主题名（决定落盘目录）")
    n.add_argument("--title", help="卡片标题")
    n.add_argument("--did", help="用户口述今天做的事")
    n.add_argument("--data", help="字段 JSON 文件；命令行参数覆盖同名字段")
    add_fields(n)
    n.set_defaults(func=cmd_new)

    r = sub.add_parser("revise", help="升级卡片（追加新版本，旧版保留）")
    r.add_argument("--topic", required=True)
    r.add_argument("--card", required=True, help="卡片编号，如 001")
    r.add_argument("--change", required=True, help="本次改了什么（用户原话）")
    add_fields(r)
    r.set_defaults(func=cmd_revise)

    l = sub.add_parser("list", help="列出主题下全部卡片")
    l.add_argument("--topic", required=True)
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="打印卡片全文（含历史版本）")
    s.add_argument("--topic", required=True)
    s.add_argument("--card", required=True)
    s.set_defaults(func=cmd_show)

    sub.add_parser("selftest", help="运行自测")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if args.cmd == "selftest":
        sys.exit(_selftest())
    args.func(args)


if __name__ == "__main__":
    main()
