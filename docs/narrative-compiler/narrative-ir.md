# N4.2 Deterministic NarrativeIR

N4.2 将 CompileRun 精确绑定的 DraftSnapshot 投影成 `compiler.narrative-ir.v1`。投影是纯领域函数，不读取数据库、Provider、Profile、Exposure 或运行身份，也不生成摘要、时间推断、故事规划、场景、POV 或正文。

## 确定性边界

NarrativeIR 的唯一语义输入是 CaseFile Snapshot 内容与 `compiler.narrative-ir-projection.v1`。相同 Snapshot 内容在不同 Profile、Exposure、Compile mode、Run 或 Task 下必须产生完全相同的 JSON 与 RFC 8785 SHA-256。

IR 保存 Case metadata、11 类 CaseFile narrative object envelope、可选 spatial scenes、content notices、extensions 和引用导航边。对象 `value` 原样使用 CaseFile Schema；envelope 只增加稳定 `object_ref` 和 whole-object `CompilerSourceRef`。

## 来源与引用导航

`CompilerSourceRef` 使用对象内 JSON Pointer 和 fragment hash，禁止数值路径段。数组内引用指向最近的稳定容器，`ReferenceEdge.ordinal` 使用一基顺序；嵌套 knowledge state、reasoning step、resolution value、evidence assessment 和 travel time 另外保存稳定 key、容器 ordinal 或 anchor ref。

引用投影由声明式 field spec 驱动。Validator 重新遍历原始 ObjectRef，拒绝未映射引用、重复对象、非 SourceFragment 的悬空目标、来源 hash 不符以及与源文档不一致的 IR。

## 执行与持久化

一个 `novel_compile` Attempt 顺序执行：

```text
compiler_input_freeze → CompileArtifact(input_manifest)
narrative_ir_projection → CompileArtifact(narrative_ir)
```

两个 Artifact 都是 Run 内唯一、不可变且内容哈希化的产物。恢复会新增 `reused` AgentStepRun 并指向原生产 Step，已有 Artifact 不改写。Worker 在每次写入前重新锁定 TaskRun/Attempt 并校验 lease 与 cancellation；整个任务仍为 providerless，必须保持零 AgentModelCall。

N4.2 不实现跨 Run Artifact 复用。跨 Run 的 component-local cache 由后续增量编译里程碑负责。
