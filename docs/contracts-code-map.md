# 跨语言契约与 Fixture

本文涵盖 `contracts/` 和 `fixtures/` 下所有受 Git 跟踪的文件职责。新增或删除 Schema 或 Fixture 时必须同步更新本文。

## 契约 Schema

| 路径 | 职责 |
|---|---|
| `contracts/schemas/` | 面向跨语言消费者的 CaseFile、编辑、验证、任务、Chat Public DTO、推理、Benchmark、Compiler 与 API Schema 集合；`chat/chat-public.schema.json` 是 M3.6 作者侧协议事实源。 |
| `contracts/schemas/compiler/compiler.schema.json` | N4.0 Narrative Compiler 基础契约：稳定 SourceRef/ArtifactRef、Diagnostic、Profile binding、Snapshot/Canon/Exposure 冻结绑定与 CompileInputManifest；不定义具体 IR 或 Artifact payload。 |
| `contracts/schemas/compiler/narrative-ir.schema.json` | N4.2 NarrativeIR：Snapshot 无损对象 envelope、根与对象来源证明、带嵌套上下文的完整引用导航边。 |
| `contracts/schemas/compiler/novel-profile.schema.json` | N4.3 小说结构、章节/场景目标、叙述方式和 Exposure 策略契约。 |
| `contracts/schemas/compiler/planner-input.schema.json` | 仅由冻结 NarrativeIR、Exposure、Profile 与规划约束组成的 Story Planner 输入。 |
| `contracts/schemas/compiler/planner-input-v2.schema.json` | 兼容新增的 Story Planner v2 输入：保留完整冻结输入，并加入可独立复验的 PlannerView；硬约束只投影现有权威规则，因果、知识与作者备注保持规划上下文。 |
| `contracts/schemas/compiler/planner-model-view-v3.schema.json` | 显式 Provider-facing 精简视图：从完整 PlannerInput v2 确定性投影结构、Exposure precedence、Temporal rank、Resolution obligations、对象目录和规划上下文；不替代完整审计输入。 |
| `contracts/schemas/compiler/planner-input-v3.schema.json`、`planner-model-view-v4.schema.json` | 将 Exposure v2 的 participant/basis/hypothesis typed obligations 按 hard/soft 分离；hard 进入 ConstraintIR v2 与权威校验，soft 仅进入模型规划上下文。 |
| `contracts/schemas/compiler/constraint-first-planner.schema.json` | Constraint-First 的 PlanningProblem、SkeletonProposal、PlanSkeleton 与 SemanticFill 强类型契约；Fill 契约不含任何 skeleton 所有字段。 |
| `contracts/schemas/compiler/novel-plan.schema.json` | 分离模型 NovelPlanCandidate 与服务器规范化 NovelPlanIR，定义 Scene 编排、事实依据、披露、Resolution、依赖及派生索引，并提供只允许替换 ScenePurpose 的结构化局部补丁契约。 |
| `contracts/schemas/compiler/scene-plan.schema.json` | N4.4 SceneCompilerInputBundle、模型所有的 ScenePlanCandidate 与服务器规范 ScenePlanIR：候选只能为既有 Scene 填来源支持的 Beat，IR 保存稳定 ID、读者状态、显式图边、provenance、索引、诊断与计数。 |
| `contracts/schemas/compiler/scene-compiler.schema.json` | N4.4 v2 完整冻结输入、章内最多八场的 Provider-facing ModelView、跨 batch typed inbound state 与受控 SemanticFill 契约；ModelView schema v1 兼容 projection v1/v2/v3，v2 保证可见引用目录闭包，v3 以 `beat_basis_allowlist` 显式呈现顶层 Beat provenance 精确白名单；Inbound State v1 显式给出当前知识、known-fact 操作白名单和开放/已用 setup key；模型不得控制最终 ID、规划顺序、Exposure 或 Resolution 权威字段。 |
| `contracts/schemas/compiler/scene-plan-v2.schema.json` | N4.4 影子 ScenePlanIR v2：在 v1 执行结构上增加场景语义、Beat 义务与因果、知识/地点状态增量、setup/payoff、状态哈希和重放索引。 |
| `contracts/generated/python/` | 由根目录 Schema 生成的 Python 契约包，禁止手改。 |
| `contracts/generated/typescript/` | 由根目录 Schema 生成的 TypeScript workspace 包，禁止手改。 |
| `contracts/tests/` | TypeScript 契约消费者与 Fixture 往返检查。 |
| `contracts/openapi.json` | 从 FastAPI 应用导出的完整 OpenAPI 3.1 快照，包含 Chat Public response model、多工作稿、Logical Mutation Preview/Apply、旧 Draft shadow/normalization、Agent debt/Undo/Redo、事件时间预览、Exposure Plan，以及 N4.1 Compiler Profile/CompileRun 契约。 |

## 契约变更顺序

修改 `schemas/` → 重新生成 Python/TypeScript → 导出 OpenAPI → 跑跨语言 fixture 测试。破坏性变更必须提升 Schema 版本并提供迁移策略。

根目录 `contracts/schemas/` 是当前 CaseFile v2、Brief、Task 和编辑契约的唯一人工维护事实源。`scripts/generate-contracts.ps1` 同步生成跨语言包、后端 Pydantic 模型和 `backend/src/casefile/contracts/schemas/v2/` 当前运行时镜像；`backend/src/casefile/contracts/schemas/v1/` 作为历史只读镜像保留，生成器不得删除或覆盖。生成物禁止手改，`check:contracts` 必须拒绝漂移。

## Fixture

| 路径 | 职责 |
|---|---|
| `fixtures/casefiles/` | 合法 CaseFile 开发与契约样例；`m3_reasoning_closure.casefile.json` 固定两组竞争 Hypothesis、共享 Evidence 矩阵、Claim 依赖、两条 ReasoningPath 与一个答案 Resolution，作为 M3.1 确定性闭包 Golden。 |
| `fixtures/editing/` | ValidationIssue、PatchCandidate、Chat Public DTO 与编辑冲突样例。 |
| `fixtures/invalid/` | 结构错误和语义不变量的失败样例。 |
| `fixtures/imports/` | 导入来源与预期映射样例。 |
| `fixtures/benchmark/` | 最小 `brief_to_draft` Benchmark 输入、预期与指标基线。 |
| `fixtures/validator_benchmark/` | Validator V0 规则、V1 patch/safe-gate、V2 RepairPlan/authoritative-target/repair-state 确定性 release-gate fixtures，包含可拷贝示例和扩展约定。 |
| `fixtures/closure_repair_benchmark/` | M3.3 Closure Repair Benchmark v2：保留 24 个 FakeProvider Regression/Safety Golden；`capability/v1/` 冻结 61 个 input/oracle 分离 Task、真实文档、Policy finding catalog 与逐 Task Reference，覆盖 `closure-repair-v1` 全部 52 个策略项。12 个 agent Task 与 49 个正确拒绝 Task 分开计分。 |
| `fixtures/general_mutation_benchmark/` | General Mutation Capability Dev、Safety / Abstention 与 Backend Release：Capability 使用自然语言输入、最终状态 Oracle 与隔离 Reference Plan；Safety v2 冻结危险请求、隐式歧义和合法近邻；Release v1 冻结 15 题真实 Apply/Undo/Redo cohort。私有 Holdout 只在 `backend/var/benchmark/private/` 保存，仓库仅保存 descriptor fingerprints。 |
| `fixtures/chat_goal_benchmark/` | M3.7 Goal 资格套件；v1 保留首轮历史证据，v2 将 24 题目标与冻结 General Mutation CaseFile 对齐，并为所有预期 Patch 任务声明最终状态 Oracle。 |
| `fixtures/compiler/foundation/` | N4.0 Compiler 基础合法/非法样例：Preview/Canonical 冻结输入、Exposure/Profile hash、SourceRef、ArtifactRef、Diagnostic 与结构/语义失败场景。 |
| `fixtures/compiler/narrative_ir/v1/` | N4.2 现有 CaseFile Golden 的 IR hash、component fingerprint 和引用边数量，冻结 projection version 行为。 |
| `fixtures/compiler/scene_plan/v1/` | N4.4 SceneCompilerInputBundle 与 ScenePlanIR 最小跨语言往返样例，覆盖 NovelPlanScene 原生 Schema、稳定执行节点、显式图边、来源证明与空揭露状态。 |
| `fixtures/novel_plan_benchmark/v1/` | N4.3 早期 placeholder Capability 样例，仅保留历史诊断，不得用于正式基线。 |
| `fixtures/novel_plan_benchmark/v2/` | N4.3 正式 8 能力 × basic/decoy/dense 矩阵；逐 Task 冻结 PlannerInput hash、声明式 Outcome invariants 和经生产 Validator/G2 双重验证的 Reference Solution。`generate_v2.py` 从稳定 CaseFile 资产确定性重建这些 fixtures。 |
| `fixtures/novel_plan_benchmark/v3/` | N4.3 审计后的 24 Task 矩阵：每项 G2 invariant 冻结 expectation class 与 Planner 可见 evidence pointer，同时保存 v1/v2 PlannerInput，精确限定正式 Pro 模型并冻结候选晋级门禁；`generate_v3.py` 确定性重建。 |
| `fixtures/novel_plan_benchmark/v3/constraint_first_diagnostic_v1.json` | Constraint-First 实验的六 Task × 三 Trial 定向诊断集合及 `ea0bc...` 基线失败分布；只用于开发诊断，不构成正式晋级证据。 |
| `fixtures/scene_plan_benchmark/v1/` | N4.4 Narrative Execution Benchmark 历史审计源：冻结 8 能力×basic/decoy/dense 的 NovelPlan/NarrativeIR 输入、人工审阅 Reference、每能力一个合法 Alternative、11 个面向 v2 SemanticFill/State Engine 的确定性 Safety Mutation，以及早期 contract-only G3 rubric。 |
| `fixtures/scene_plan_benchmark/v2/` | 当前 N4.4 G3/G4 资格套件：复用并 hash 绑定 v1 审计输入，为 24 Task 冻结由正式 v2 State Engine 生成的 runtime reference；G3 固定 `deepseek-v4-flash` 盲位 pairwise 协议，正常单调用、仅空响应额外重试一次，并冻结五维 rubric、同源偏差声明与 Task-cluster bootstrap 阈值；G4 固定 v2 语义签名。前瞻门槛以既有 71/72 完整基线冻结，但必须由全新 24×3 Pro 生成 + Flash Judge 报告计算资格。 |
