# 跨语言契约与 Fixture

本文涵盖 `contracts/` 和 `fixtures/` 下所有受 Git 跟踪的文件职责。新增或删除 Schema 或 Fixture 时必须同步更新本文。

## 契约 Schema

| 路径 | 职责 |
|---|---|
| `contracts/schemas/` | 面向跨语言消费者的 CaseFile、编辑、验证、任务、推理、Benchmark、Compiler 与 API Schema 集合。 |
| `contracts/generated/python/` | 由根目录 Schema 生成的 Python 契约包，禁止手改。 |
| `contracts/generated/typescript/` | 由根目录 Schema 生成的 TypeScript workspace 包，禁止手改。 |
| `contracts/tests/` | TypeScript 契约消费者与 Fixture 往返检查。 |
| `contracts/openapi.json` | 从 FastAPI 应用导出的完整 OpenAPI 3.1 快照，包含多工作稿、Logical Mutation Preview/Apply、旧 Draft shadow/normalization、Agent debt/Undo/Redo、事件时间预览及 Exposure Plan 契约。 |

## 契约变更顺序

修改 `schemas/` → 重新生成 Python/TypeScript → 导出 OpenAPI → 跑跨语言 fixture 测试。破坏性变更必须提升 Schema 版本并提供迁移策略。

根目录 `contracts/schemas/` 是当前 CaseFile v2、Brief、Task 和编辑契约的唯一人工维护事实源。`scripts/generate-contracts.ps1` 同步生成跨语言包、后端 Pydantic 模型和 `backend/src/casefile/contracts/schemas/v2/` 当前运行时镜像；`backend/src/casefile/contracts/schemas/v1/` 作为历史只读镜像保留，生成器不得删除或覆盖。生成物禁止手改，`check:contracts` 必须拒绝漂移。

## Fixture

| 路径 | 职责 |
|---|---|
| `fixtures/casefiles/` | 合法 CaseFile 开发与契约样例；`m3_reasoning_closure.casefile.json` 固定两组竞争 Hypothesis、共享 Evidence 矩阵、Claim 依赖、两条 ReasoningPath 与一个答案 Resolution，作为 M3.1 确定性闭包 Golden。 |
| `fixtures/editing/` | ValidationIssue、PatchCandidate 与编辑冲突样例。 |
| `fixtures/invalid/` | 结构错误和语义不变量的失败样例。 |
| `fixtures/imports/` | 导入来源与预期映射样例。 |
| `fixtures/benchmark/` | 最小 `brief_to_draft` Benchmark 输入、预期与指标基线。 |
| `fixtures/validator_benchmark/` | Validator V0 规则、V1 patch/safe-gate、V2 RepairPlan/authoritative-target/repair-state 确定性 release-gate fixtures，包含可拷贝示例和扩展约定。 |
| `fixtures/closure_repair_benchmark/` | M3.3 Closure Repair v1 冻结 Golden 场景与扩展说明；覆盖三类 Claim 修复、双轮闭合和全部失败关闭安全门禁，不把 pass@k 当成发布证据。 |
| `fixtures/compiler/` | Compiler 输入、IR、Source Map 和期望产物的预留落位。 |
