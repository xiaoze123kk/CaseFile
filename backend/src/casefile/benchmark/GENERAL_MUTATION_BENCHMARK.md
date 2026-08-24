# General Mutation Benchmark v2

## 宪法

一个 Task 必须对应真实作者目标。一个 Trial 结束后，Grader 应仅根据最终
CaseFile 状态、确定性证明和安全边界判断成功、正确阻断或安全逃逸；Plan、
reason code 与 Transcript 是诊断证据，不是成功本身。

统一证据链为：

`Suite -> Task -> Trial -> Transcript -> Outcome -> Grader -> Report`

## 分层

| 层 | 回答的问题 | 当前实现 |
|---|---|---|
| S0 Kernel Regression | 给定 Plan 后 Contract、Binder、Simulation 是否确定且失败关闭 | `general_mutation_eval.py` |
| S1 Capability Dev | 自然语言能否经真实 Provider 形成正确最终状态 | `general_mutation_capability.py` |
| S2 Safety / Abstention | 不该改时是否正确阻断且零逃逸 | 待接真实 Router/Worker |
| S3 Private Holdout | 未见分布上是否可迁移并稳定 | 待建立私有包 |
| S4 Backend Release | API、Queue、Worker、PostgreSQL、Apply 全链是否正确 | 待建立 release suite |
| S5 Fault / Recovery | stale、tamper、retry、rollback 是否保持一致 | 复用现有集成测试后独立报告 |

各层必须独立报告，S0 通过不能替代 S1，S1 单次 Dev baseline 也不能替代
Safety、Holdout 或 Backend Release。

## S1 Dev v1 契约

- Provider 输入只有冻结 CaseFile、自然语言消息和服务端 editable fields。
- Oracle 与 Reference Plan 不得进入 Provider 输入。
- Reference Plan 只证明 Task 可解，并须通过同一 Binder、Simulation 与 Grader。
- Grader 正交检查 Outcome Correctness、Verification、Safety 和 Scope。
- 不要求固定 operation key、local ref、操作顺序或推理路径。
- 顶层分类固定为 `success`、`capability_failure`、`safe_block`、
  `unsafe_escape`、`protocol_failure`、`infrastructure_failure`。

当前 7 个公开 Dev Task 是最小校准集，覆盖 existing update、multi-field、create、
cross-reference、multi-object 与 delete。它不是计划中的完整约 40-task Dev bank。

## 07a Ref Contract v2

- Plan/Binder 冻结为 `general-mutation-plan-v2`、`general-mutation-binder-v2`；
  v1 继续只读回放。首轮 7×5 暴露 Create 完整性问题后，只把 Prompt 升为
  `general-mutation-planner-v3`，Plan Contract 仍为 v2。
- Planner nested ref 只能是 `{ref_kind: "local", local_ref}` 或
  `{ref_kind: "existing", object_id}`；出现 `object_type` 必须以
  `general_mutation_ref_object_type_forbidden` 失败关闭。
- Binder 根据 Create collection 或当前 CaseFile 对象注册表推导正式
  `object_type`，并在生成 ObjectRef 前验证目标字段所允许的引用类型。
- Dev Suite fingerprint 只覆盖题目、输入和 Oracle；Reference Plan 使用独立
  `reference_fingerprint`，从而允许校准合约而不伪装成题目语义变化。
- 07a 只以干净提交上的 DeepSeek `deepseek-v4-pro` 7-task × 5 完整运行作为 Gate；
  失败时不得进入 Transport 实验。
- Prompt v3 是独立单变量校准：补充通用 Create 业务必填字段、列表保留、字段语义
  映射与作者请求逐项覆盖；不得同时修改 Binder、Suite、Reference 或 Grader。

## 指标与发布边界

Capability 主要报告 `task_macro_pass_at_1`、family macro 与多 Trial 的
`reliable_task_rate_at_k`。Safety 单独报告真实 `unsafe_escape_count`，不得再用
普通 grader failure 冒充 unsafe rate。

正式 General Mutation Suggest Ready 至少还需要：

- 完整公开 Dev bank 多次 Trial；
- 独立 Safety / Abstention 且所有 escape hard-zero；
- private holdout；
- API / Worker / PostgreSQL / Apply release suite；
- fault / concurrency / recovery 证据；
- 相同 suite、grader、prompt、policy、binder、model lineage 下的可比报告。
