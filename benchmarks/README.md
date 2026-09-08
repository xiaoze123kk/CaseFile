# CaseFile Benchmark 总目录

开发者从这里按能力方向选择评测，再进入对应的运行入口、套件和证据。此目录集中管理当前 CaseFile 仓库中的评测；执行器、冻结数据和历史报告保留原位置。

这是评测导航，不是通过状态看板。下表登记的是现有入口与用途，不表示已取得正式资格。其他分支或 checkout 独有的实验需要单独登记来源 revision，不能当作当前代码的结果。

## 按问题选择

| 你要验证的问题 | 方向 | 评测 ID |
|---|---|---|
| Brief 能否生成符合要求的结构化工作稿？ | 工作稿生成 | `brief-to-draft` |
| 校验器能否发现结构和逻辑问题？ | 确定性验证 | `validator` |
| Agent 能否修复推理闭包、正确拒绝不可修复请求？ | 推理修复 | `closure-repair` |
| Agent 能否完成自然语言修改、安全拒绝并正确应用修改？ | 通用修改 | `general-mutation` |
| Agent 能否选对意图和处理路径？ | 对话路由 | `chat-router` |
| Agent 能否检索到回答需要的依据？ | 对话检索 | `chat-retrieval` |
| Agent 最终交付是否满足用户请求？ | 对话结果 | `chat-outcome` |
| 长对话裁剪、压缩和续接是否可靠？ | 上下文管理 | `chat-context` |
| 对外回复是否遵守公开语言要求？ | 对外表达 | `chat-public-language` |
| 单次任务能否完成多步目标？ | 目标编排 | `chat-goal` |
| 跨消息目标能否处理 steer、follow-up 和恢复？ | 交互式目标 | `chat-goal-interactive` |
| 小说整体结构与披露规划是否满足输入约束？ | 小说规划 | `novel-plan` |
| 场景执行计划及状态传递是否正确？ | 场景规划 | `scene-plan` |
| 正文裁判的语义判断是否可靠？ | 评审器评测 | `prose-judge` |
| Writer 是否忠实执行场景计划？ | 正文生成 | `prose-writer` |
| Rewrite 是否修复问题并保留正确内容？ | 正文修订 | `prose-rewrite` |
| 质量评审与润色是否改善正文？ | 正文质量 | `prose-quality` |

## 入口与数据

链接指向当前实现。PowerShell 脚本从仓库根目录调用；Python CLI 从 `backend/` 使用 `uv run python -m casefile.benchmark.<模块> --help` 查看参数。标为函数或测试入口的模块不能假定支持 CLI。

### 工作稿、验证与修改

| ID | 运行入口与子评测 | 数据 | 结论边界 |
|---|---|---|---|
| `brief-to-draft` | [Provider benchmark](../scripts/benchmark.ps1)；[API/Worker 运行时验收](../scripts/acceptance-brief-to-draft-v8.ps1) | [生成输入](../fixtures/benchmark/) | Provider 生成质量与真实 API、Worker、持久化验收分别记录；前者不替代发布验收。 |
| `validator` | [validator_eval](../backend/src/casefile/benchmark/validator_eval.py)，也支持 `python -m casefile.benchmark validator` | [V0/V1/V2 套件和扩展说明](../fixtures/validator_benchmark/) | 确定性规则、Patch 安全门禁和 RepairPlan；不衡量模型写作质量。 |
| `closure-repair` | [回归与 live shadow](../backend/src/casefile/benchmark/closure_repair_eval.py)；[能力](../backend/src/casefile/benchmark/closure_repair_capability.py)；[运行时验收](../scripts/acceptance-closure-repair-v2.ps1)；[正式资格](../backend/src/casefile/benchmark/closure_repair_qualification.py)；[结果门禁](../backend/src/casefile/benchmark/closure_repair_gate.py) | [公开回归、Capability 与 Reference](../fixtures/closure_repair_benchmark/) | 修复成功、正确拒绝、安全性和后端交付分别计分；资格执行另有私有 Holdout 与谱系要求。 |
| `general-mutation` | [确定性内核](../backend/src/casefile/benchmark/general_mutation_eval.py)；[能力](../backend/src/casefile/benchmark/general_mutation_capability.py)；[安全与拒绝](../backend/src/casefile/benchmark/general_mutation_safety.py)；[Backend Release](../backend/src/casefile/benchmark/general_mutation_backend_release.py)；[运行时验收](../scripts/acceptance-general-mutation-v1.ps1)；[正式资格](../backend/src/casefile/benchmark/general_mutation_qualification.py) | [公开套件](../fixtures/general_mutation_benchmark/)；[详细说明](../backend/src/casefile/benchmark/GENERAL_MUTATION_BENCHMARK.md) | 内核正确、模型能力、安全拒绝和 Apply/Undo/Redo 是不同层次，不能合并成一个通过率。 |

### 对话与目标编排

| ID | 运行入口与子评测 | 数据 | 结论边界 |
|---|---|---|---|
| `chat-router` | [离线基线函数](../backend/src/casefile/benchmark/chat_router_eval.py)；[Live CLI](../backend/src/casefile/benchmark/chat_live_eval.py) | 离线模块内置 fixture；[回归测试](../backend/tests/unit/test_chat_router_eval.py) | 意图、路由命中不代表最终任务完成。 |
| `chat-retrieval` | [检索评测函数](../backend/src/casefile/benchmark/chat_retrieval_eval.py)；[测试入口](../backend/tests/unit/test_chat_retrieval_eval.py) | 评测模块及测试内置样例 | 有界工具循环和检索效果；不等同于回答结果质量。 |
| `chat-outcome` | [校准入口](../backend/src/casefile/benchmark/chat_outcome_eval.py)；[DB Canned harness](../backend/src/casefile/benchmark/chat_outcome_canned.py)；[Live CLI](../backend/src/casefile/benchmark/chat_outcome_live_eval.py)；[失败归因](../backend/src/casefile/benchmark/chat_outcome_triage.py) | [冻结任务](../backend/src/casefile/benchmark/chat_outcome_fixtures.py)；[T2 难度池](../backend/src/casefile/benchmark/chat_outcome_t2.py)；[说明](../backend/src/casefile/benchmark/CHAT_OUTCOME_EVAL.md) | Grader 校准、预制回答执行与真实模型结果分开；难度池版本必须明确。 |
| `chat-context` | [综合入口](../scripts/benchmark-context.ps1)；[Baseline/Boundary](../backend/src/casefile/benchmark/chat_context_eval.py)；[多层 A/B](../backend/src/casefile/benchmark/context_tier_benchmark.py)；[v1 验收](../scripts/acceptance-chat-context-v1.ps1)；[v2 验收](../scripts/acceptance-chat-context-v2.ps1) | 模块内置场景及 [Context 测试](../backend/tests/unit/test_chat_context_eval.py) | 确定性上下文对比与 PostgreSQL、Live 运行时验收分别记录。综合脚本还会执行质量门禁，运行前查看参数。 |
| `chat-public-language` | [资格 CLI](../backend/src/casefile/benchmark/chat_public_language_qualification.py)；[生产路径执行器](../backend/src/casefile/benchmark/chat_public_language_executor.py) | [公开语言套件](../fixtures/chat_public_language_qualification/) | 衡量公开语言协议；不能用该结果推断通用任务能力。 |
| `chat-goal` | [确定性/Fake gate](../backend/src/casefile/benchmark/chat_goal_gate.py)；[生产路径资格 CLI](../backend/src/casefile/benchmark/chat_goal_qualification.py) | [Goal 套件](../fixtures/chat_goal_benchmark/) | 单 TaskRun 内目标编排；不覆盖跨消息控制的全部行为。 |
| `chat-goal-interactive` | [验收脚本](../scripts/acceptance-chat-goal-interactive-v2.ps1)；[资格 CLI](../backend/src/casefile/benchmark/chat_goal_interactive_qualification.py)；[执行器](../backend/src/casefile/benchmark/chat_goal_interactive_executor.py) | [公开开发套件](../fixtures/chat_goal_interactive_benchmark/)；[私有套件及 descriptor 校验](../backend/src/casefile/benchmark/chat_goal_interactive_suite.py) | GoalSession 跨消息与安全点控制；正式资格需要匹配的私有套件、干净 revision 和隔离测试数据库。 |

### 小说编译与正文

| ID | 运行入口与子评测 | 数据 | 结论边界 |
|---|---|---|---|
| `novel-plan` | [novel_plan_eval](../backend/src/casefile/benchmark/novel_plan_eval.py)，也支持 `python -m casefile.benchmark novel-plan` | [版本化 Benchmark](../fixtures/novel_plan_benchmark/)；[Solver 资产](../fixtures/novel_plan_solver/) | 整体规划回归、安全性和能力；不代表最终小说质量。 |
| `scene-plan` | [scene_plan_eval](../backend/src/casefile/benchmark/scene_plan_eval.py)，也支持 `python -m casefile.benchmark scene-plan` | [场景套件](../fixtures/scene_plan_benchmark/) | ScenePlan 执行结构、状态与约束；不等同于正文的文学连续性判断。 |
| `prose-judge` | [脚本](../scripts/prose-judge-benchmark.ps1)；[公开 B0 消融及语义 smoke](../backend/src/casefile/benchmark/prose_judge_eval.py) | [公开 Judge 套件](../fixtures/prose_judge_benchmark/) | 评测裁判，不是评测 Writer；公开开发结果不能替代私有资格证据。 |
| `prose-writer` | [Fake 脚本](../scripts/prose-writer-benchmark.ps1)；[Writer 开发基线](../backend/src/casefile/benchmark/prose_writer_eval.py) | [Writer 套件](../fixtures/prose_writer_benchmark/) | 场景正文生成与 Fidelity；Fake 基线不代表真实模型能力。 |
| `prose-rewrite` | [脚本](../scripts/prose-rewrite-benchmark.ps1)；[公开开发基线](../backend/src/casefile/benchmark/prose_rewrite_eval.py)；[独立资格 CLI](../backend/src/casefile/benchmark/prose_rewrite_qualification.py) | [Rewrite 套件](../fixtures/prose_rewrite_benchmark/) | 有界修订效果与语义保留；开发基线和私有资格分别记录。 |
| `prose-quality` | [脚本](../scripts/prose-quality-benchmark.ps1)；[开发基线](../backend/src/casefile/benchmark/prose_quality_eval.py)；[诊断](../backend/src/casefile/benchmark/prose_quality_diagnostic.py)；[重评分](../backend/src/casefile/benchmark/prose_quality_rescore.py)；[资格 CLI](../backend/src/casefile/benchmark/prose_quality_qualification.py) | [Quality 套件](../fixtures/prose_quality_benchmark/) | 质量评审、润色收益与语义保留分别报告；重评分不算一次新的生成实验。 |

小说链路中，产品完成率与严格语义通过率分别统计。产品接受非致命瑕疵不改写原始 Judge 结论，也不自动获得严格评测资格。

### 数据回流与辅助工具

这些工具支撑评测，不独立表示模型能力通过。

| 工具 | 用途 |
|---|---|
| [feedback_export](../backend/src/casefile/benchmark/feedback_export.py) | 将路由反馈导出成候选 fixture。 |
| [audit_feedback_export](../backend/src/casefile/benchmark/audit_feedback_export.py) | 审计反馈导出。 |
| [chat_feedback_metrics](../backend/src/casefile/benchmark/chat_feedback_metrics.py) | 只读反馈统计。 |
| [eval_core](../backend/src/casefile/benchmark/eval_core.py) | 共享 Eval 数据结构与评分基础设施。 |
| [policies](../backend/src/casefile/benchmark/policies/) | 版本化策略与资格 descriptor；实际生效项由对应 runner 选择。 |

## 如何运行

先选方向，再确认评测层次、套件版本及依赖。不要扫描所有 fixture 后全量执行：目录中同时存在历史证据、Reference、契约样例和私有套件描述。

查看单个入口参数（从仓库根目录）：

```powershell
Push-Location backend
try {
    uv run python -m casefile.benchmark.validator_eval --help
} finally {
    Pop-Location
}
```

运行不调用模型的 Validator，并为本次运行保留独立报告：

```powershell
$benchmarkAttempt = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssfffZ")
$benchmarkReport = Join-Path (Get-Location).Path "var/benchmark/validator/$benchmarkAttempt/report.json"
Push-Location backend
try {
    uv run python -m casefile.benchmark.validator_eval --report-path $benchmarkReport
    if ($LASTEXITCODE -ne 0) { throw "Validator benchmark failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}
```

其他入口的 `--mode`、`--suite`、`--suite-path`、`--output-dir` 参数并不统一，以上示例不能直接替换模块名后套用。Live、数据库验收与正式资格按各自入口准备环境，并遵守该套件的模型、预算、审签和 revision 门禁。

## 证据与版本管理

| 资产 | 管理位置与规则 |
|---|---|
| 公开输入、Oracle、Reference | 原有 `fixtures/` 或模块内置套件；输入与答案隔离。生命周期见 [fixtures/README](../fixtures/README.md)。 |
| 执行与评分代码 | 原有 `backend/src/casefile/benchmark/`；本目录只维护导航。 |
| 本机报告和原始响应 | 现有入口使用 `var/benchmark/`、`backend/var/benchmark/`，部分旧入口使用 `tmp/`。这些均被 Git 忽略；按实际输出路径查找，不按目录名推断最新有效结果。 |
| 私有 Holdout | 保持对应 runner 的私有路径，常见为 `backend/var/benchmark/private/`；只在公开位置登记 descriptor、指纹和审签摘要。 |
| 已冻结结果 | 保持原路径、suite/hash、Prompt、模型及 revision 绑定；不能为了整理目录移动或覆盖。 |

新增运行建议使用 `<benchmark-id>/<suite-version>/<attempt-id>/` 作为独立输出目录（在入口支持指定输出路径时）。每份可对比结果至少记录：评测 ID、suite 路径与 hash、代码 revision、Prompt/策略版本、精确 Provider/model ID、运行模式、尝试 ID、样本数与重复次数、指标分母、结论、报告和原始证据路径。

比较结果时，确认输入和指标口径一致，并明确本次有意改变的变量。语义失败、基础设施失败、Oracle 问题与协议失败分别保留；未运行、blocked、inconclusive 均不算通过。重试和修复保留各次尝试，不覆盖失败，也不选择性重跑后拼成同一次冻结结果。

## 新增、升级与退役

1. **新增方向**：在“按问题选择”和对应入口表登记稳定 ID、评测问题、执行入口、数据位置和结论边界。优先使用能力名称；阶段编号作为辅助信息。
2. **新增子评测**：同一能力下登记 regression、capability、safety、runtime acceptance 或 qualification，说明它与已有结果的关系。
3. **升级版本**：创建新套件版本，保留原冻结资产；明确由哪个 runner/参数选择，不能把目录中最高版本号直接认定为当前正式基线。
4. **登记结论**：附对应 revision 与不可变报告引用；仅有运行入口或公开开发集不算已通过。私有数据不可复制进本目录。
5. **退役**：标注替代入口及保留原因，先核对 loader、生成器、跨版本依赖和历史报告引用，再决定是否删除。

维护范围包括当前仓库的 Benchmark 家族、资格编排和关联验收入口；普通单元测试仍属于各模块测试体系，不逐项复制到本目录。新评测应与本目录更新放在同一变更中。
