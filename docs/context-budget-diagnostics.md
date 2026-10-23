# 上下文预算诊断

`easel doctor` 会读一遍 OpenClaw 已保存的配置，把**声明的上下文窗口**和它隐含的
**默认参数下的参考压缩阈值**显示出来。诊断是只读的：不改配置、不回显凭证、也不声称运行时已加载。

```
OpenClaw 上下文预算（只读配置诊断，不计入环境就绪结果）
    └─ deepseek/deepseek-flash：声明 128,000 → 默认参数参考压缩阈值约 108,000
    └─ openai/mimo-v2.6-pro：未声明 contextWindow；阈值由 OpenClaw 推导
    └─ skills.limits：已设置 maxSkillsPromptChars=60,000，maxSkillsInPrompt=200
```

## 为什么声明值值得单独看一眼

`models.providers.*.models[].contextWindow` 不只是"描述模型能吃多少"。OpenClaw 用它做两件事：

**1. 影响压缩阈值。** 在常见默认预留参数下，参考换算公式是：

```
压缩阈值 = contextWindow − min(20_000, contextWindow × 0.25)
```

（`reserveTokens` 默认 16_384，先抬到下限 20_000，再按窗口的 25% 封顶。）

实际行为还依赖所装 OpenClaw 版本、当前预留参数、`agents.defaults.contextTokens`
上限与 `compaction.enabled` 开关。诊断会考虑已保存的全局 contextTokens 上限并提示
显式关闭压缩，但不会据此保证网关何时压缩；Agent 单独覆盖、运行时模型和上下文引擎
也可能改变结果。以上不是所有模型或版本的统一保证。

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
| 偏大 | 可能延后压缩、增加上下文负担；缓存失效时可能增加未缓存输入成本 |

缓存命中与成本取决于供应商、缓存 TTL、提示前缀是否稳定及请求形态；不能仅凭窗口
大小断言缓存必然失效，或宣称调整后一定省钱。Issue #156 的表现是特定环境的实测，
不是所有后端的通用规律。

## 调整声明值

`setup.sh` 的 OpenAI 兼容分支支持环境变量覆盖：

```bash
OPENAI_CONTEXT_WINDOW=128000
OPENAI_MAX_TOKENS=16384
```

Gemini 分支现在同样支持 `GEMINI_CONTEXT_WINDOW` / `GEMINI_MAX_TOKENS`，默认值仍为
1048576 / 65535，不改变已有安装行为；它们不是所有 Gemini-compatible 模型的统一上限。
两项必须为正整数且输出上限不能超过声明窗口；按实际选用模型能力填写，不要虚报上限。

改完重跑 `setup.sh`（或直接改 `~/.openclaw-easel/openclaw.json` 的
`models.providers.*.models[].contextWindow`）并重启网关：

```bash
python -m easel gateway restart
python -m easel doctor   # 确认新阈值
```

## 技能目录预算

同一段诊断会报出 `skills.limits`。技能目录参与提示构建；部分版本使用
150 个技能 / 18,000 字符的默认预算。放不下时可能精简描述或截断技能清单，具体排序
与截断规则以所装版本为准。设 `skills.limits.maxSkillsPromptChars` 和
`maxSkillsInPrompt` 可以显式控制：

```jsonc
"skills": { "limits": {
  "maxSkillsInPrompt": 200,
  "maxSkillsPromptChars": 60000
}}
```

按字母序截断的版本会更容易遗漏靠后的技能（比如 `video-*`、`tts-*`）。诊断里看到
"未声明"不代表必然有问题；结合实际技能数、提示长度和网关日志决定是否调整。

## 与工具结果裁剪的关系

OpenClaw 的 `agents.defaults.contextPruning`（如 `mode: "cache-ttl"`）可在受支持后端
裁剪旧工具结果。其效果取决于运行时支持、请求内容与配置；较长 TTL 可能保留更多上下文，
但不能保证某个 TTL（包括 5 分钟）让所有会话始终低于压缩阈值。

相关： [OpenClaw 循环保护诊断](tool-loop-diagnostics.md)
