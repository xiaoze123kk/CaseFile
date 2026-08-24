# N4.0 Narrative Compiler Foundation

N4.2 的确定性 NarrativeIR 契约、来源导航和执行边界见 [`narrative-compiler/narrative-ir.md`](narrative-compiler/narrative-ir.md)。

## 目的

N4.0 为 CaseFile Narrative Compiler 建立稳定、可跨语言生成的基础契约与纯领域规则。它只冻结后续编译阶段共同依赖的身份、引用、哈希和诊断语义，不生成 Narrative IR 或小说正文。

CaseFile、Compiler 与 Manuscript 保持独立：CaseFile 决定事实，Compiler 产生不可变候选产物，未来的 Manuscript 承载作者采用后的可编辑内容。

## 本阶段边界

N4.0 包含：

- `CompilerSourceRef`、`CompilerArtifactRef` 与 `ArtifactKind`。
- `CompilerDiagnostic`。
- Snapshot、Canon、Exposure 与 Profile 的冻结输入绑定及 `CompileInputManifest`。
- RFC 8785 canonical JSON + lowercase SHA-256 哈希规则。
- JSON Schema 生成的 Python/TypeScript DTO、纯领域语义校验和 Fixtures。

N4.0 不包含：

- 数据库模型、迁移、Repository、API、Worker、TaskRun 或 Provider。
- NarrativeIR、NovelPlan、ExposureSchedule、Scene、AudienceState、SceneOutcomeLedger 或 Artifact payload。
- Planner、Renderer、Fidelity Guard、增量编译与 Manuscript 生命周期。

## 契约决策

### SourceRef

`CompilerSourceRef` 使用 CaseFile `ObjectRef` 确定稳定对象身份。`field_path` 是对象内部的 RFC 6901 JSON Pointer；空字符串表示整个对象。数字路径段不合法，数组依赖必须引用整个字段，不能引用 `/items/0`。`source_fragment_hash` 绑定被引用对象或字段的原始 JSON 值。

一次冻结编译输入中，SourceRef 的逻辑键是 `(object_type, object_id, field_path)`。同一逻辑键和相同 hash 是重复引用，稳定错误码为 `compiler_source_ref_duplicate`；同一逻辑键出现不同 hash 是来源冲突，稳定错误码为 `compiler_source_ref_hash_conflict`。诊断中的 `source_refs` 保持作者顺序，不为哈希或去重自行排序。

### Artifact

`CompilerArtifactRef` 只描述不可变产物的种类、稳定 key、schema 和内容哈希。它不携带 payload，也不承担执行审计。未来 `CompileArtifact` 是领域产物，`AgentStepRun` 只记录执行、失败或复用行为；二者可以关联但不能互相替代。

Artifact kind 在 N4.0 冻结名称，不冻结各产物的 payload：

`input_manifest`、`narrative_ir`、`novel_plan`、`exposure_schedule`、`scene_plan`、`scene_context`、`scene_render`、`scene_assertions`、`validation_report`、`source_map`、`novel_candidate`、`compile_manifest`。

### Frozen input

`CompileInputManifest` 支持：

- `preview`：绑定不可变 Draft Snapshot，不绑定 CanonVersion。
- `canonical`：绑定 CanonVersion；Canon 必须指向同一 Snapshot，且内容哈希相同。
- 可空的 Exposure binding；存在时必须属于 Snapshot 的 Draft。
- 必填的 Profile binding。N4.0 只冻结 profile 身份、schema、version、payload 与 hash，不提前固定体裁、POV、字数或节奏字段。

Exposure/Profile 的冻结 payload 使用 RFC 8785 计算内容哈希。Manifest 不包含自引用哈希；调用方对完整 Manifest 调用统一哈希函数得到未来的 `input_hash`。业务数组不在哈希前排序，顺序变化必须改变哈希。

### Contract ownership

`contracts/schemas/compiler/compiler.schema.json` 是字段定义的唯一人工维护事实源，并通过现有 `editing-contracts.schema.json` 聚合生成 Python/TypeScript 类型。Domain 不重复手写 DTO，只实现 JSON Schema 无法表达或不适合表达的跨字段语义、哈希与稳定错误码。

纯领域模块不得依赖 SQLAlchemy、FastAPI、Worker、Provider SDK 或文件系统。

## N4.1 Durable CompileRun 落地

N4.1 已完成以下持久化与执行边界：

- `CompilerProfile` 保存 Project 内唯一 key 与当前版本指针；`CompilerProfileVersion` 保存不可变 payload、schema、连续版本与 RFC 8785 hash。首版本在单事务内创建，deferred constraint trigger 在提交时验证 current pointer。
- `CompileRun` 保存 Project/CaseFile/Draft、Snapshot、可选 Canon/Exposure、显式 Profile Version、TaskRun 与 `input_hash` 的精确关系绑定；它表达 Build 身份，不复制 TaskRun status、Attempt、lease、usage 或完整 Manifest。
- 完整 `CompileInputManifest` 由 `TaskRun.input_jsonb` 保存并经数据库冻结；`novel_compile` TaskRun 严格 providerless，但继续使用既有队列、Attempt、取消、恢复和事件能力。
- Worker 在 Provider 分派前执行 `compiler_input_freeze`，重新验证 Manifest hash 与所有关系绑定；成功物化唯一不可变 `compiler.input_manifest` Artifact，不创建 `AgentModelCall`。
- 租约恢复对相同 Artifact 写 `reused` StepRun；内容或 lineage 冲突失败关闭。Artifact 写入与 Task completion 分属带 fencing 复验的事务，失去租约的 Worker 不得继续写入。

N4.1 仍不包含 NarrativeIR、Planner、Prompt、LLM、正文、Benchmark 或前端。

N4.0 的契约不预设这些表、外键或队列实现。

## 完成定义

- Schema 可由现有工具生成 Python/TypeScript DTO，runtime v2 mirror 无漂移。
- 合法/非法 Fixtures 同时覆盖结构与纯领域语义。
- canonical hash 对对象键顺序稳定，对业务数组顺序和内容变化敏感。
- `scripts/check-contracts.ps1` 与统一质量门禁通过。
