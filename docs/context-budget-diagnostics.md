# 上下文预算诊断

`easel doctor` 会读一遍 OpenClaw 已保存的配置，把**声明的上下文窗口**和它隐含的
**压缩阈值**显示出来。诊断是只读的：不改配置、不回显凭证、也不声称运行时已加载。

```
OpenClaw 上下文预算（只读配置诊断，不计入环境就绪结果）
    └─ deepseek/deepseek-flash：声明 128,000 → 压缩阈值约 108,000
    └─ openai/mimo-v2.6-pro：未声明 contextWindow；阈值由 OpenClaw 推导
    └─ skills.limits：已设置 maxSkillsPromptChars=60,000，maxSkillsInPrompt=200
```

## 为什么声明值值得单独看一眼

`models.providers.*.models[].contextWindow` 不只是"描述模型能吃多少"。OpenClaw 用它做两件事：

**1. 推导压缩阈值。** 上下文超过阈值才触发压缩，换算公式是：

```
压缩阈值 = contextWindow − min(20_000, contextWindow × 0.25)
```

（`reserveTokens` 默认 16_384，先抬到下限 20_000，再按窗口的 25% 封顶。）

**2. 本地校验请求预算。** 声明值会被用来判断这次请求装不装得下；装不下就直接拒发，
请求根本到不了 provider：

```
Context window exceeded: estimated input 34934 leaves only 0 output tokens
within the 20000-token context.
```

所以声明值填错有两个方向的后果：

| 声明 | 后果 |
|---|---|
| 偏小 | 请求被本地拒绝，对话直接报错 |
| 偏大 | 压缩长期不触发，上下文越滚越长；provider 侧 prefix cache 频繁失效，整段尾部被重发为未缓存输入 |

第二种更隐蔽：它不报错，只是账单悄悄变贵。上下文越长，provider 的 prefix cache 越容易
失效，失效一次就要把剩余上下文整段重发（未缓存，按输入价计费）。

## 调整声明值

`setup.sh` 的 OpenAI 兼容分支支持环境变量覆盖：

```bash
OPENAI_CONTEXT_WINDOW=128000
OPENAI_MAX_TOKENS=16384
```

Gemini 分支现在同样支持 `GEMINI_CONTEXT_WINDOW` / `GEMINI_MAX_TOKENS`（默认值与
Gemini 的真实窗口一致）。

改完重跑 `setup.sh`（或直接改 `~/.openclaw-easel/openclaw.json` 的
`models.providers.*.models[].contextWindow`）并重启网关：

```bash
python -m easel gateway restart
python -m easel doctor   # 确认新阈值
```

## 技能目录预算

同一段诊断会报出 `skills.limits`。技能目录每轮都要进提示，OpenClaw 的默认预算是
150 个技能 / 18,000 字符；技能多到放不下时会降级成"只有名字没有描述"，并丢掉排在
字母序靠后的一批技能。设 `skills.limits.maxSkillsPromptChars` 和
`maxSkillsInPrompt` 可以显式控制：

```jsonc
"skills": { "limits": {
  "maxSkillsInPrompt": 200,
  "maxSkillsPromptChars": 60000
}}
```

注意 OpenClaw 在放不下时按**字母序**砍，不是按相关性——所以技能名首字母靠后的
（比如 `video-*`、`tts-*`）比 `card-*` 更容易被丢掉。诊断里看到"未声明"或预算偏小时，
按实际需要的技能数显式设一个值。

## 与工具结果裁剪的关系

OpenClaw 还有一个 `agents.defaults.contextPruning`（`mode: "cache-ttl"`），到 TTL 后
会把旧的工具结果裁掉以控制上下文。实测上它与压缩是配套的：TTL 设长，裁剪就少，
上下文涨得快，更容易撞上压缩阈值——而压缩本身要重写前缀，产生一次额外的未缓存重传。
默认 5 分钟能保持上下文稳在阈值之下。

相关： [OpenClaw 循环保护诊断](tool-loop-diagnostics.md)
