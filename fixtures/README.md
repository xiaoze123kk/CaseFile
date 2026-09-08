# Fixture 用途与生命周期

按能力方向选择评测、查找执行入口和区分结果层次，请先看 [Benchmark 总目录](../benchmarks/README.md)。

这里同时保存契约样例、当前回归输入和历史评测证据。版本号较旧不等于可以删除；消费方显式选择套件，不扫描整个目录执行评测。

| 资产 | 用途与处理 |
|---|---|
| `casefiles/`、`editing/`、`invalid/`、`imports/`、`compiler/` | 契约和领域样例。优先从已有基础文档构造单个测试需要的变体，避免复制整份 JSON。 |
| `novel_plan_benchmark/v1/` | 早期 placeholder，保留历史诊断，不作为当前正式基线。 |
| `novel_plan_benchmark/v2/` | 历史冻结矩阵；`generate_v2.py` 还被 v3 生成器复用。保留重建与回放用途。 |
| `novel_plan_benchmark/v3/`、`v4/` | 现有兼容与当前规划测试仍消费这些套件；v3 包含冻结的 v1/v2 输入，v4 包含带 typed obligations 的输入。 |
| `scene_plan_benchmark/v1/`、`v2/` | v2 当前运行套件引用并绑定 v1 审计资产，不能单独删除 v1。 |
| `chat_goal_benchmark/v1/`、`v2/` | v1 历史证据，v2 当前 Goal 套件。 |
| `prose_quality_benchmark/v1/`、`v2/` | v2 descriptor 复用 v1 的公开任务正文与 Gold，两者都保留。公开数据不构成私有资格集。 |
| Closure Repair、General Mutation、其他 Prose benchmark | 完整 reference/安全矩阵在对应测试中执行；报告字段与统计分支使用小样本或已执行结果。私有 Holdout 的缺失拒绝与正式资格规则保持原样。 |

本次盘点发现 37 份字节完全相同的额外副本，主要是 Novel Plan 不同版本的参考答案，另有 Closure Repair 的独立基础文档。它们处于不同冻结套件中；仅为减少文件数而改引用、移动或重写，会改变套件身份或历史回放路径，因此本轮不改写冻结资产。

新增数据时：

- 普通单元测试优先使用小型内存对象；修改共享对象前复制，避免跨用例污染。
- 同一测试模块可共享只读的已验证套件；消费者拿独立副本。不要给生产 loader 加全局缓存来绕过文件漂移检查。
- 完整矩阵只保留必要的生产流程覆盖。测试报表分母、指纹或错误分类不必再次执行全量矩阵；合成重复行只验证聚合，不能作为模型可靠性证据。
- 历史套件停止成为默认基线后，在本表标明用途。删除前核对 loader、生成器、跨语言契约与历史报告引用；已冻结 hash/attestation 的数据保持不可变。
- 普通输入变体与 oracle/reference 继续分离，去重不能将答案混入 Provider 输入。

General Mutation 和 Closure Repair 的报告 fixture 共用 `backend/tests/benchmark_preparation.py`，仅复用相同文档与 verifier 配置的确定性检查结果；每次模拟、修复与门禁仍真实执行，不改变场景数量、trial 次数或冻结资产。

Prose Judge 默认套件在对应测试模块完整校验一次；相同 Profile、Render、Checklist 输入可在测试上下文内复用验证结果。Judge 输出与 Council 决策不缓存，suite/正文/Gold/attestation 漂移测试仍执行真实校验。
