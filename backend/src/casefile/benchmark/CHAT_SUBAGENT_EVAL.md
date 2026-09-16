# CaseFile Chat Subagent 评测

`fixtures/chat_subagent_benchmark/v1/suite.json` 冻结 24 个案例：简单查询、跨对象查证、专项审计各 8 个，每例固定运行 3 次。三组分别是当前 v6、扩大到同等工具预算的单 Agent、实验 v8 Subagent 软门控。

真实运行固定使用当前正式的 `deepseek-flash`，必须显式提供已验证的人民币价格、总预算和单 Trial 输入/输出 token 预留上限。运行器在每个 Trial 前预留费用，余额不足时停止并把报告标记为 `incomplete_budget_exhausted`；不会自动续跑。凭据只从本地数据库读取。

当前实验改为 `casefile-chat-subagents-v2`：关闭按问题关键词/复杂度自动预取。
父 Agent 先读取对象，再提交 1–3 个已读取对象 ID 和一个明确的 `evidence_gap`。
子 Agent 直接获得绑定到冻结卷宗的原始对象记录，结果携带引用对应的原始记录供父 Agent 核对。
无引用的 verified_facts / confirmed_conflict 被拒绝；partial、疑点和未知项仍保持原分类。
工具入口仍使用实验 v7/v8 名称，但策略、Prompt 和契约哈希已变化，旧报告不能作为本策略的结果；
历史输入/输出与评测资产不改写，已有冻结旧策略任务不得作为新策略续跑或重放证据。

此前 v8 单次探索运行共 22 个子任务（20 partial、2 failed、0 completed）。
复杂组同次序样本从 12/16 到 10/16，不能据此断言稳定退化或模型能力不足；
简单组没有委派，其差异也不能归因于 Subagent。只加长问题而不扩充卷宗和重新标注答案的
“高复杂度组”仅用于探索，不作为严格质量评测。后续应冻结新策略、真实扩充的卷宗及标注，
重复运行配对样本，并分别报告局部任务完成、引用完整性、审计误报、成本和最终答案质量。
本次离线回归不构成真实模型资格。

`casefile-chat-subagents-v3` 在此基础上增加一轮和两次硬保护工具额度，但要求模型在第 6 次
工具调用后停止扩查并返回；同时移除全局数量提示与宽泛检索工具。无引用的权威主张被移除
并降级为 partial。v3 必须重新执行三组完整对照，不能只重跑 Subagent 组；v1/v2 报告只作
历史比较，不能与 v3 的单组结果拼成资格证据。

2026-09-16 的 v3 开发评测完整重跑三组 24×3：baseline 60/72、matched single 61/72、
v3 62/72。v3 仅一个试次发生委派，两个子任务为 1 completed、1 partial、0 failed；该父任务
仍未通过。复杂任务相对 baseline 的通过率差为 +4.17pp，配对 bootstrap 95% 区间
[-6.25pp, +16.67pp]；相对 matched single 为 +2.08pp，区间 [-8.33pp, +12.50pp]。
`qualified=false`，默认委派继续关闭。独立局部组件在完整锚定答案所需对象后为 9/9
completed，但它只证明子任务收尾边界，不证明自然委派或最终答案收益。

同日使用 benchmark-only 旧策略适配层补跑原始 v8 自动门控 24×3。复杂任务 42/48 发生
委派，共 71 个子任务：0 completed、39 partial、32 failed；最终答案 62/72，Token 总量
2,109,973。它与 v3 的最终通过数相同，但比 v2 多用 80.6% Token、比 v3 多用 39.8%。
自动提高委派率没有转化为更高完成度。并行仅用于三个互不重叠的案例分片，不改变每例上下文。

在完整三组评测前，可先用 `--component-smoke` 直接并发执行两个独立只读子任务。该模式验证子任务上下文、只读工具、引用绑定、独立账本、结果顺序和用量合并：

```powershell
uv run --extra dev python -m casefile.benchmark.chat_subagent_live_eval `
  --output-dir var/benchmark/chat-subagents-v1-component-smoke `
  --prices src/casefile/benchmark/prices/deepseek-flash-20260916.json `
  --budget-cny 1 `
  --database-url $env:CASEFILE_TEST_DATABASE_URL `
  --actor-id 1 `
  --max-input-tokens-per-trial 200000 `
  --max-output-tokens-per-trial 32000 `
  --case-ids investigation-04 `
  --component-smoke
```

```powershell
scripts/chat-subagent-eval.ps1 `
  -DatabaseUrl $env:CASEFILE_TEST_DATABASE_URL `
  -Prices backend/src/casefile/benchmark/prices/deepseek-flash-20260916.json `
  -BudgetCny 20 `
  -MaxInputTokensPerTrial 100000 `
  -MaxOutputTokensPerTrial 16000
```

完整得到 `baseline.json`、`matched_single.json`、`subagents.json` 后运行资格判定：

```powershell
uv run --extra dev python -m casefile.benchmark.chat_subagent_qualification `
  --baseline var/benchmark/chat-subagents-v1/baseline.json `
  --matched-single var/benchmark/chat-subagents-v1/matched_single.json `
  --subagents var/benchmark/chat-subagents-v1/subagents.json `
  --input-price 2 --output-price 8 `
  --output var/benchmark/chat-subagents-v1/qualification.json
```

只有完整 24×3×3 报告满足质量、误报、证据、成本、P95 和配对区间门槛，资格报告才输出 `qualified=true`。否则 `default_rollout=disabled`，不会修改 Prompt Registry 或产品默认工具集。

## v4 单组并行历史比较

v4 的 `target-first-repair-v1` 修订优先定位显式 ID 与 focus，初始原文最多 16 条、
12000 字符；规划器可按 ID 补读最多 6 次（最多 3 轮），结构或锚点校验失败后仅允许
一次无工具修正。锚点以冻结卷宗真实对象校验并绑定原文，不再受初始定位列表限制。
新的运行 manifest 同时冻结 planner_revision、Skill 与源码哈希，不能续跑旧实验。
运行 `--suite-version v3 --workers 6` 可将 24×3 分到 6 个独立进程，各运行 4 题×3次。

修订题集使用 `--suite-version v2`，详见 `fixtures/chat_subagent_benchmark/v2/README.md`。
该模式运行前校准 24 个参考答案与负例，保留最终候选；v2 与 v1 只标明版本差异，不计算
跨题集质量增益。Agent policy v4 保持原实现，避免将题集修复与运行时改动混为同一实验。

用户明确选择只运行 v4 的 72 次时，在 backend 目录运行：

```powershell
uv run python -m casefile.benchmark.chat_subagent_v4_parallel `
  --output-dir var/benchmark/chat-subagents-v4-24x3-UNIQUE `
  --budget-cny 12
```

固定 24 题 × 3 次，3 个独立进程各运行 8 题；子任务最多再并发 2 项。
输出目录必须不存在，防止覆盖。冻结源码、领域 Skill、fixture 和依赖锁文件；
合并验证全部 72 个唯一试次、tools-v9 及执行前后哈希。密钥仅从本地配置解密。
按价格快照的未缓存输入价格保守估算费用；分进程预留预算并在试次之间停止，
这不是 Provider 端硬扣费上限，超出单次预估或网络失败时可能有未报告用量。
报告保留规划决定、子任务范围和证据台账，以区分已委派、部分完成、失败和正确率。
只与历史 v2/v3/v8 及单 Agent 组描述性比较，不输出同期三组资格结论。
现有“复杂”题主要增加要求，卷宗较小，不代表大卷宗的严格高复杂度评测。
