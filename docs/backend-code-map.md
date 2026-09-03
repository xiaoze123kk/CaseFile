# 后端代码职责地图

## M3.8 Persistent GoalSession 规划边界

`docs/m3.8-goal-session-runtime.md` 是 M3.8 冻结 ADR。实现保持三层职责：`agent_runtime/goal/` 定义无数据库的 Goal 语义契约、可校验 Checkpoint 与单 TaskRun bounded execution；`application/goal_session_state.py`、`goal_session_repository.py` 和 `application/workflow/goal_session.py` 拥有跨 TaskRun 状态矩阵、预算、obligation/observation 持久化及 continuation 原子事务；`worker/handlers/` 只轮询安全点并委派当前 TaskRun 收敛与下一执行切片排队。

M3.8 不把 SQLAlchemy、FastAPI、Session 或 TaskRun lease 引入 `agent_runtime`，不在 API route 中直接读写 Goal 表，也不创建第二条 Patch/Apply 路径。新增、删除或重命名实际源码后，必须在对应阶段把精确路径补入本文和静态架构检查。

## M3.7 Bounded Goal Harness

- `backend/src/casefile/agent_runtime/goal/contracts.py`：Goal Interpreter、冻结义务、单步决策、Observation 与 Completion 的严格内部契约；不属于 Public Chat Schema。
- `backend/src/casefile/agent_runtime/goal/policy.py`：稳定哈希、DAG、资格、Capability Registry、预算与 Completion Gate 的纯确定性规则；v5 补齐明确请求的义务、规范化 review-gated grounding、显式 replace/abandonment、未解析指代、无修改 clarification 及 constraint/refine 结构，并拒绝重复执行已完成义务。
- `backend/src/casefile/agent_runtime/goal/execution.py`：单个 `TaskRun` 内的 bounded next-action loop；在 Controller 前、完整 capability 后和 Finalizer 前产生可哈希 Checkpoint，并可从 obligations-hash 匹配的 Checkpoint 恢复；不持有数据库 Session。
- `backend/src/casefile/agent_runtime/goal/filter.py`：位于显式 preset/issue-action 之后的保守 free-text 候选过滤器。
- `backend/src/casefile/agent_runtime/goal/provider.py`：Interpreter、Controller、Evidence 与 Finalizer 的 provider-neutral 端口。
- `backend/src/casefile/worker/handlers/chat.py`：rollout/资格分流、能力适配、取消观察、Goal safe-point 轮询、Checkpoint continuation 委派和最终持久化；Mutation 后 steer 先形成带 PatchSet identity 的待审 slice，再由 post-apply continuation 在新 capability 前消费；Provider/Binder/Simulation/Verification 内不轮询。
- `backend/src/casefile/benchmark/chat_goal_suite.py`、`chat_goal_qualification.py`：M3.7 24×3 正式资格套件、生产路径评分、逐题追踪与聚合门禁；v2 题目必须与冻结 CaseFile fixture 可解，并用最终状态 Oracle 评分 mutation。
- `backend/src/casefile/benchmark/chat_goal_interactive_suite.py`：M3.8-07 Interactive Goal v2 的严格私有 Holdout loader、8-family typed oracle/reference、RFC 8785 指纹、路径/泄漏/独立双 attestation 校验；正式 coverage matrix 强制 add/remove obligation、三类 safe point/capability、混合 FIFO、提前 follow-up 拒绝、三类 PatchOperation、至少三个 fixture、Patch/stale/cancel 生命周期。仓库只跟踪 fail-closed descriptor 与 8 题公开 Fake dev suite。
- `backend/src/casefile/benchmark/chat_goal_interactive_executor.py`：以真实公共 HTTP、后台 Worker、PostgreSQL、continuation 与 Patch Review 路径执行交互 trial；通过默认关闭的 Worker 组合端口在三类 safe point 建立确定性 barrier，并使用已知 TaskRun 的真实 lease claim 隔离同一数据库中的 trial，不新增公共 API 或运行时配置。
- `backend/src/casefile/benchmark/chat_goal_interactive_qualification.py`：M3.8-07 clean source/独立 `_test` 数据库/精确 Pro 与 Prompt preflight、24×3 逐 trial 编排、证据指纹及 Intent Adherence Under Intervention 聚合门禁。

Goal 运行不得绕过 `general_mutation` Binder/Simulation/Closure Repair，也不得直接调用 Apply。Goal Finalizer 只负责作者可见正文，模型生成的引用、finding 与 suggestion 不构成权威结果；结构化结果由 Observation、Completion proof 与 Mutation proof 决定。任何 Goal 失败均在 `_complete_chat()` 前关闭，因此不产生 `AgentPatchSet`。恢复只复用精确 input/upstream/output hash 匹配的 bounded artifact；Mutation 只复用 Planner artifact并重新绑定、模拟。

本文涵盖 `backend/` 下所有受 Git 跟踪的源码文件职责。新增、删除、重命名后端源文件时必须同步更新本文。

TaskRun 失败由 `worker/finalization.py` 在 lease/Attempt fencing 后委派 `WorkflowService.finalize_agent_goal_task_failure()`，在同一事务关闭当前 Goal、slice 和待处理 control；残留 running capability step 同步终止。Interactive executor 将持久化生产失败与真正的 harness/transport 故障分离，保留原始 Binder reason 与审计，但不放宽 Oracle 或资格门槛。

## 数据库代码

| 路径 | 职责 |
|---|---|
| `backend/pyproject.toml` | Python 3.12、FastAPI/Pydantic、JSON Schema/RFC 8785、SQLAlchemy/Alembic/psycopg 与测试、静态检查配置。 |
| `backend/alembic.ini` | Alembic 路径、连接默认值与 `V%%(rev)s__%%(slug)s` 文件模板。 |
| `backend/migrations/env.py` | 加载 `DATABASE_URL`、完整 SQLAlchemy metadata 和在线/离线迁移上下文。 |
| `backend/migrations/script.py.mako` | 新迁移模板；revision 和 `down_revision` 必须带类型标注。 |
| `backend/migrations/versions/` | 正式 PostgreSQL 迁移，保持真实时间戳单调单链。 |
| `backend/migrations/README.md` | 受 Git 跟踪的逐表职责、关系、约束、触发器、写入方和生命周期说明。 |
| `backend/src/casefile/__init__.py` | CaseFile 后端 Python 包入口，不承载数据库或业务实现。 |
| `backend/src/casefile/data_postgres/__init__.py` | PostgreSQL 持久化包入口，仅导出共享 `Base`。 |
| `backend/src/casefile/data_postgres/base.py` | SQLAlchemy Base、约束命名规范、BIGINT Identity 主键和时间戳 Mixin。 |
| `backend/src/casefile/data_postgres/session.py` | 同步 Engine/Session 工厂、应用支持的唯一数据库 revision 和 API 启动门禁。 |
| `backend/src/casefile/data_postgres/repositories.py` | 按 Project/CaseFile/Draft/Snapshot 聚合封装所有者过滤、Current Draft 锁定与切换、全部工作稿枚举、Draft 隔离的 Operation/语义引用和安全软删。 |
| `backend/src/casefile/data_postgres/exposure_repository.py` | 按 Draft 读取/锁定单一 Exposure Plan，解析同 Draft 稳定对象引用，并原子追加完整计划修订、线性条目与引用。 |
| `backend/src/casefile/data_postgres/compiler_repository.py` | Profile、精确 Exposure revision、CompileRun、Artifact metadata/content 与执行链的所有者过滤和持久化查询；不读取或创建隐式 current Profile/Exposure。 |
| `backend/src/casefile/data_postgres/models/identity.py` | `users`、单一所有者 `projects` 与用户级密文 `user_provider_settings` ORM。 |
| `backend/src/casefile/data_postgres/models/casefile.py` | `casefiles` 的非空 Current Draft 复合指针、多份 `drafts`、Draft 内唯一的轻量 `casefile_objects` 注册表、旧语义边 `casefile_refs`、v1 `casefile_contract_refs` 和 `draft_operations` ORM。 |
| `backend/src/casefile/data_postgres/models/content.py` | 旧 Narrative Phase 兼容存储、Entity/Person、v1 Relationship/Location、Event、Information Unit/Evidence/Testimony、Claim 与 Knowledge State ORM。 |
| `backend/src/casefile/data_postgres/models/reasoning.py` | Hypothesis、Reasoning Path/Node/Edge、Resolution Spec/Slot、Resolution 当前结论及作者确认元数据、Constraint 与 Structure Lock ORM。 |
| `backend/src/casefile/data_postgres/models/versioning.py` | `draft_snapshots`、`canon_versions`、`audit_events` ORM。 |
| `backend/src/casefile/data_postgres/models/exposure.py` | `exposure_plans`、带 v1/v2 payload schema 的不可变 revisions、线性 entries、规范化 entry refs、typed obligations 与 obligation refs ORM。 |
| `backend/src/casefile/data_postgres/models/workflow.py` | `briefs`、不可变 `brief_versions`、不可变 `source_records`、三类 `task_runs`、`task_attempts` 与不可变 `task_events` ORM。 |
| `backend/src/casefile/data_postgres/models/agent_execution.py` | 组件化 v8–v15 `agent_step_runs` 与 `agent_model_calls` 的产物、哈希复用、结构化诊断、失败原文保留策略和终态审计 ORM。 |
| `backend/src/casefile/data_postgres/models/compiler.py` | N4.1 `compiler_profiles`、不可变 `compiler_profile_versions`、关系型 `compile_runs` 与不可变 `compile_artifacts` ORM；CompileRun 只保存 Build 身份和冻结绑定，不复制执行状态或 Manifest。 |
| `backend/src/casefile/data_postgres/models/context_states.py` | 追加式不可变 `agent_thread_context_states` ORM：按 thread 冻结 policy/state_kind/消息区间/state_jsonb/输入哈希，供 Rolling Thread Memory 压缩回放与 `context_state` 冻结引用。 |
| `backend/src/casefile/data_postgres/models/goal_session.py` | M3.8 GoalSession、Revision、Obligation DAG、FIFO Delivery、Observation、TaskRun slice 绑定与 Transition 的关系型 ORM；状态、归属、预算和不可变性由正式列、复合外键、约束与触发器承载。 |
| `backend/src/casefile/data_postgres/models/verification.py` | `verification_runs`、`verification_findings`、规范化 finding refs、作者 reviews 与 patch-operation lineage ORM；VerificationRun 是领域 observation，不承载 TaskRun 调度字段。 |
| `backend/src/casefile/data_postgres/models/reverse_parse.py` | 路径 C 反向解析的 `imported_documents` 与 `parse_items` ORM：上传文档与提取文本、解析状态、逐项确认结果、grading/field_sources 与来源片段引用。 |
| `backend/src/casefile/data_postgres/models/__init__.py` | 汇总导入全部 ORM，供 Alembic metadata 发现。 |
| `backend/src/casefile/data_postgres/models/benchmark.py` | Benchmark 持久化模型的预留落位；当前不定义或导出 ORM。 |

## 核心业务与应用层

| 路径 | 职责 |
|---|---|
| `backend/src/casefile/contracts/` | 加载根目录 v1 Schema 的运行时镜像，执行结构、稳定 ID 引用和确定性语义校验。 |
| `backend/src/casefile/contracts/semantic_validation.py` | 只读的确定性叙事语义检查：解析事件时间（exact/approximate/range/relative/unknown）并报告知识状态冲突（`knowledge_state_available_before_source`）与时间排他冲突（`temporal_exclusivity_violation`）；产出 severity/evidence_refs/impact_refs/fix_hint/explanation 富 issue，不进生成门禁。 |
| `backend/src/casefile_contracts/` | 从根目录 Schema 生成、供后端运行时使用的 Pydantic 契约模型；禁止手改。 |
| `backend/src/casefile/application/commands.py` | 与 HTTP 解耦的 Project、Entity 和 Event 类型化写入命令。 |
| `backend/src/casefile/application/errors.py` | 应用层稳定错误码、公开消息和传输无关的错误详情。 |
| `backend/src/casefile/application/goal_session_state.py` | M3.8 GoalSession 的纯确定性状态转换矩阵、revision 并发门禁和会话级预算规则；不依赖数据库、API、Worker 或 Provider。 |
| `backend/src/casefile/application/goal_session_repository.py` | M3.8 GoalSession 聚合的 SQLAlchemy 锁与持久化边界：创建 interpreting 会话、原子追加 Revision/Obligation DAG/Transition、绑定 TaskRun slice，并以 FIFO 锁完成 delivery claim、过期回收和当前 TaskAttempt fencing。 |
| `backend/src/casefile/application/workflow/goal_session.py` | M3.8 GoalSession 应用用例：按 Project/Draft/Thread 归属解析显式投递、创建/查询/取消 Goal 与公共投影；拥有初始 Revision、HITL/Patch Review、steer/replace/follow-up、safe-point observation、delivery claim lease，以及“旧 TaskRun/Attempt 成功收敛、Checkpoint 哈希、单条 control 消费、新 Revision/Goal、唯一 queued TaskRun”的原子事务。 |
| `backend/src/casefile/application/snapshot.py` | 从全部规范化当前态投影 CaseFile JSON，稳定排序、契约校验并计算 RFC 8785 SHA-256。 |
| `backend/src/casefile/application/services.py` | Project、工作稿列表/原子激活、Current Draft 对象/引用编辑和 Snapshot 的事务边界、Draft ID + revision 并发控制及应用规则。 |
| `backend/src/casefile/application/casefile_v1.py` | 在目标无关的 CaseFile v2 JSON（v1 仅历史读取兼容）与规范化当前态之间执行原子写入、增量对象创建、完整投影、契约引用映射和规范哈希。 |
| `backend/src/casefile/application/v1_editing.py` | 唯一 Logical Mutation 物化边界：锁内复验、CREATE/UPDATE/DELETE、完整 projection/hash proof、一次 Draft revision 原子提交，并为旧字段 Patch 提供兼容适配。 |
| `backend/src/casefile/application/agent_mutation.py` | M3.4 General Mutation 的确定性 Binder：校验 capability/字段边界，拓扑排序 Plan，解析 existing/local refs，分配稳定对象与 operation ID，冻结旧值/revision，并生成 MutationSet、plan hash 与 impact hash。 |
| `backend/src/casefile/application/agent_patch_mutation.py` | PatchSet 与 Logical Mutation 的转换、历史 before/after document 语义重建、Atomic 选择规则和 finding 目标解析；供 Apply/Undo/Redo 共用。 |
| `backend/src/casefile/application/logical_mutation_service.py` | 所有产品写入口可复用的通用 Mutation Preview/Apply 事务门面；只转换 DTO、锁定 Current Draft 并调用领域 Simulation 与唯一物化边界。 |
| `backend/src/casefile/application/logical_mutation_rollout.py` | 旧 Current Draft 的只读 shadow scanner 与显式 system mechanical normalization；只修复双向投影并保留 before/after hash，不自动改写语义状态。 |
| `backend/src/casefile/application/workflow_service.py` | `WorkflowService(session)` 稳定门面，只初始化事务依赖并组合内部工作流用例；保留既有公开方法和兼容 helper 导出，不再承载具体规则。 |
| `backend/src/casefile/application/workflow/` | Workflow 内部用例实现：`agent.py` 拥有 Thread/Message、Chat Task、Finding 与 Mutation review/simulate/apply/undo，`goal_session.py` 拥有 Goal 投递、公共生命周期和 TaskRun slice continuation 事务，`mutation_history.py` 拥有严格栈顶 Redo，`content.py` 拥有 Provider 设置、Source/Brief、润色/拆解/生成任务和候选查询。API 与 Worker 不直接依赖这些 mixin。 |
| `backend/src/casefile/application/chat_public_contracts.py` | CaseFile Chat 公共 DTO 的 allowlist 投影边界；不得输出 TaskRun、TaskEvent、原始结果或 Patch 内部字段。 |
| `backend/src/casefile/application/chat_public_events.py` | 将内部 TaskEvent 严格映射为 Chat Public Event；未知事件必须丢弃，内部 sequence 只作为恢复游标保留。 |
| `backend/src/casefile/application/workflow_common.py` | Workflow 用例共享的稳定默认配置、TaskRun 创建、冻结输入和小型事务 helper；不拥有 HTTP DTO 或 Worker 编排。 |
| `backend/src/casefile/application/workflow_brief_validation.py` | Workflow 使用的 Brief 契约、语义与已确认原子项门禁。 |
| `backend/src/casefile/application/workflow_views.py` | Workflow 实体、部件步骤与公开失败信息的稳定 HTTP 读模型序列化。 |
| `backend/src/casefile/application/task_events.py` | 在调用方事务中追加单调序号的不可变 TaskEvent。 |
| `backend/src/casefile/application/task_cancellation.py` | 统一 queued/running TaskRun 的取消终态、Attempt 收敛与 CaseFile Chat pending 消息失败回填。 |
| `backend/src/casefile/application/workbench_read_model.py` | 按当前 Draft 只读汇总 CaseFile 结构验证与叙事语义验证（`validate_casefile_semantics`）、冻结 Brief 所引用的 SourceRecord 正文与可追溯标识，以及 `audit_events`/`draft_operations` 审计事实。 |
| `backend/src/casefile/application/timeline.py` | 对 Current Draft 事件时间修改执行只读影响预览，报告事实顺序跨越、相对时间依赖和完整契约验证结果；不得写入 Draft。 |
| `backend/src/casefile/application/exposure_plan.py` | 读取与修订 Current Draft 的单一线性 Exposure Plan，执行独立 revision 门禁、同 Draft 引用校验和审计；不得推进 Draft revision 或写入 Canon/Event.time。 |
| `backend/src/casefile/application/compiler/` | Profile 版本化与 CompileRun 事务边界：锁定 Current Draft、创建/复验 Snapshot、读取 exact Canon/Exposure/Profile、冻结 CompileInputManifest、创建 providerless TaskRun，并提供 Run/Artifact 所有者过滤读模型。 |
| `backend/src/casefile/application/a_path_metrics.py` | 只读地从 Brief-to-Draft `AgentModelCall`/`TaskAttempt`/`TaskRun` 分层用量、`TaskEvent` 与采用后的 `draft_operations` 推导 A 路径漏斗、完整重试用量和人工续编指标；同一 Attempt 只消费一个权威层级，不新增分析表。 |
| `backend/src/casefile/application/reverse_parse_service.py` | 路径 C 服务层事务边界：上传提取、解析块与逐项确认/拒绝、失败文档保留与重试重建、高风险项门禁，以及仅由 confirmed 项拼装目标无关 Brief 候选。 |
| `backend/src/casefile/application/verification_engine.py` | VerificationEngine 的兼容导入门面；稳定 re-export 既有公开类型，纯规则实现在 domain 层。 |
| `backend/src/casefile/application/verification_service.py` | VerificationEngine 的 SQLAlchemy application adapter：VerificationRun/finding 双写、refs/reviews/patch lineage 与 Workbench 查询读模型。 |
| `backend/src/casefile/domain/verification_engine.py` | 脱离 API、数据库、Worker 和 Provider 的纯验证内核：Finding contract、确定性/LLM 合并、旧 batch simulation，以及统一 MutationSimulation 的增量 finding delta、作者债务授权与 can_apply policy。 |
| `backend/src/casefile/domain/narrative_compiler/` | Narrative Compiler 纯领域内核：N4.0 hash/SourceRef/Manifest 门禁、N4.2 CaseFile→NarrativeIR 无损投影、N4.3 Story Planner 权威门禁、N4.4 ScenePlanIR v2，以及 N4.5 Profile/正文协议与确定性 Checklist；不依赖数据库、Worker、API 或 Provider。 |
| `backend/src/casefile/domain/narrative_compiler/planner_input.py`、`planner_constraints.py`、`planning_solver.py`、`novel_plan.py` | PlannerInput v1–v3、ModelView v3/v4、ConstraintIR v1/v2、PlanningProblem/slots、纯 PlanningSolver 接口与确定性参考后端，以及 NovelPlan 权威语义门禁；PlannerInput v1–v3 兼容 NovelProfile v1/v2，v2 prose 字段保留并进入完整输入哈希，但不改变既有规划约束投影。 |
| `backend/src/casefile/domain/narrative_compiler/prose_checklist.py` | N4.5 Profile v2 跨字段门禁、ScenePlanIR v2 状态重放、前一场 accepted Render 绑定、对象来源闭包与固定 policy 的逐 Scene Checklist 构造/复验；同时把严格 SceneRenderCandidate 规范化为服务端拥有身份的 Writer 或 `rewrite_1/rewrite_2` Render，绑定 Rewrite 直接来源哈希，并校验 Unicode 字符数、Judge 全量覆盖和逐字 Evidence，不执行自然语言语义判断。 |
| `backend/src/casefile/domain/narrative_compiler/scene_plan.py` | N4.4 Scene Execution 纯领域边界：绑定完整 NovelPlanIR+NarrativeIR 输入，校验模型只为既有 Scene 填充来源支持的结构化 Beat，再由服务器生成 Beat/Edge ID、来源证明、索引、读者揭露状态与语义签名；原机械展开仅作 baseline/reference helper，不生成正文或引入 Z3。 |
| `backend/src/casefile/domain/narrative_compiler/scene_compiler_input.py` | N4.4 v2 冻结输入与最小模型视图投影：绑定 NovelPlan/NarrativeIR/Exposure/Profile，派生硬执行义务和来源状态种子，按章生成最多八场的稳定批次，并保证 Scene/State 中每个 Provider 可见 ObjectRef 都在同批对象目录闭包内；projection v3 额外把 Validator 既有 Scene/obligation basis 并集稳定投影为 `beat_basis_allowlist`，不扩大白名单。 |
| `backend/src/casefile/domain/narrative_compiler/scene_fill.py`、`scene_runtime_state.py`、`scene_state_engine.py` | N4.4 模型字段纯领域门禁、跨 batch typed inbound state 与 ScenePlanIR v2 权威状态引擎：Scene Fill 拒绝保留 batch/scene/beat/path/ref/allowlist hash 安全证据；Runtime State 只投影当前知识、known-fact 操作白名单和开放/已用 setup key，并与完整重放共享知识操作规则；State Engine 重建 Exposure，应用知识/地点增量，规范化因果和 setup/payoff，通过完整重放 Linter 失败关闭，并公开忽略模型措辞、绑定执行结构与状态的版本化 v2 语义投影/签名。 |
| `backend/src/casefile/agent_runtime/constraint_first_story_planner.py`、`constraint_first_story_planner_prompt.py` | 已晋级的 Constraint-First 双模型编排：静态 UNSAT 先于 Provider、skeleton/fill 独立 Prompt/Schema/hash、exact-hash stage 恢复、服务端组装与复验。 |
| `backend/src/casefile/agent_runtime/scene_compiler.py`、`scene_compiler_prompt.py` | N4.4 影子 Scene Fill 编排：章内最多八场、逐批 typed state/hash 链、exact-hash 恢复、单轮无工具 Prompt 与模型所有字段的确定性越权门禁；Prompt v2 冻结对象目录与 `beat_basis_allowlist` 权限分层，Prompt v3 锁定 obligation.kind 与 Beat.kind 一致并禁止跨 kind 合并，Prompt v4 默认不创建可选 setup、要求新 setup 同批闭环并把入站 open setup 作为当前批次显式 payoff 义务，Prompt v5 明确 `depends_on` 只能引用当前 Scene 严格更早的 Beat local key，Prompt v6 要求知识变化逐条服从入站 `allowed_operations` 并禁止把已知事实改为 false belief；DeepSeek Scene Fill 延续既有 `temperature=0`，Validator 语义保持不变。 |
| `backend/src/casefile/agent_runtime/prose_judge.py` | N4.5-02 Provider-neutral Semantic Council：冻结 Fidelity/Adversarial/Coherence 三策略消融、一次批量 Arbiter、无多数票 Consensus、单轮调用预算、精确 request fingerprint 恢复、原始响应审计以及 DeepSeek/Fake 适配；v5 保持服务端 Evidence ID 与公共 Judge Report 组装边界，补齐必需事件时间、逐 check 独立和真实事件因果判定，并仅对连接/超时执行一次同 fingerprint 的显式可审计重试；SDK 隐藏重试关闭，协议、鉴权、限流或重试耗尽使 attempt 失败关闭。 |
| `backend/src/casefile/agent_runtime/prose_writer.py` | N4.5-04 Provider-neutral 单轮完整 Scene Writer：在 Provider 前精确复验上游与 Checklist，构造最小不可信模型视图，冻结 Prompt/Schema/model/预算指纹，提供 DeepSeek/Fake 适配、exact-hash 恢复和脱敏 transport 审计；不依赖数据库、API 或 Worker。 |
| `backend/src/casefile/agent_runtime/prose_rewriter.py`、`backend/src/casefile/agent_runtime/prose_rewrite_supervisor.py` | N4.5-05 Provider-neutral 完整 Scene Rewrite 与服务端 bounded supervisor：精确绑定失败的 Fidelity-only Consensus、Judge 证据、当前 Render 和既有通过项，每次只允许一个无工具完整替代候选；服务端最多执行 `rewrite_1/rewrite_2` 两轮，每轮重审完整 Checklist，区分语义拒绝、协议失败和基础设施未决，并提供 DeepSeek/Fake、预算与恢复审计边界。 |
| `backend/src/casefile/agent_runtime/story_planner.py`、`story_planner_prompt.py` | Story Planner Provider-neutral 请求、最多三次结构修复和版本化无工具 Prompt 渲染；ScenePurpose 错误只允许强类型最小补丁，其他结构错误保留完整候选修复，语义错误不进入 repair。 |
| `backend/src/casefile/worker/executors/story_planner.py` | 默认 v1 Story Planner 与显式未激活 Constraint-First 版本的执行器；后者按 `skeleton_proposal`/`semantic_fill` 独立 input hash 持久化 ModelCall，崩溃后只恢复 exact-match 成功输出。 |
| `backend/src/casefile/worker/executors/scene_compiler.py` | N4.4 未默认激活的影子 Worker：复用 TaskRun Provider 绑定，逐批持久化/恢复 Scene Fill ModelCall，完成 State Engine 后仅物化 ScenePlanIR v2。 |
| `backend/src/casefile/benchmark/novel_plan_eval.py` | Novel Plan Regression/Safety/24 Task Capability Benchmark、v1/v2 输入与定向 Task 选择、部分运行禁晋级、G2 oracle evidence 审计、生产/G2 失败分层、动态 cohort、严格 fingerprint checkpoint/resume、G0–G3 分层与精确 Pro baseline/候选晋级门禁。 |
| `backend/src/casefile/benchmark/scene_plan_eval.py` | N4.4 ScenePlan v2 独立评测：把审计后的 8×3 capability fixtures 升级为完整 v2 Input/ModelView，复用正式 Shadow Scene Fill runtime、Provider adapter 与 State Engine；v3 Runner 支持 Fake regression、v2 Safety mutations、DeepSeek/OpenAI live capability、精确 Pro 3-trial、严格 checkpoint/resume、失败 batch ordinal和可选 failed-proposal 诊断。G3 通常以一次 `deepseek-v4-flash` 盲位 pairwise 调用同时评分候选与冻结 v2 reference，仅当成功响应内容为空时额外重试一次并累计 attempts/usage；结果按 Task 聚类 bootstrap 并显式记录同源偏差。G4 使用领域层版本化语义签名，并对文本替代表达执行确定性等价门禁。完整 24×3 Pro+Flash 结果才评估前瞻 promotion gate。 |
| `backend/src/casefile/benchmark/prose_judge_eval.py` | N4.5-02 公开 B0 开发集严格 loader、Fake/Mutation 门禁和三策略 DeepSeek 消融；N4.5-02R 提供三调用协议 Smoke、固定 14-call Fidelity SemanticSmoke，以及 Fidelity、Adversarial、Coherence、Gold-backed Arbiter 四调用 CouncilSmoke，均永不具备资格；v2 开发报告/raw bundle 分开统计逻辑调用、成功响应、物理 transport attempt、显式 retry 和终端失败，并 hash 绑定失败调用审计。完整消融仍按冻结门槛选择每任务中位请求数最低的合格策略，只生成不含模型正文的开发报告与 `qualified=false` policy descriptor。 |
| `backend/src/casefile/benchmark/prose_writer_eval.py` | N4.5-04 公开 Writer development suite 严格 loader 与零网络 Fake runner：校验 8×3 分布、N4.4 输入引用、previous accepted lineage、Checklist/Gold Evidence/hash/attestation，逐题执行 Writer→规范 Render→冻结 Fidelity-only Council，并只输出脱敏、永不具备资格的开发报告。 |
| `backend/src/casefile/benchmark/prose_rewrite_eval.py` | N4.5 B2 公开 8 缺陷族×3 variant Rewrite development suite 严格 loader、零网络 Fake runner与私有 qualification fail-closed 边界：重建坏 Render/Checklist/Consensus/此前通过项及两轮 Gold，聚合 Round 1 rescue、Round 2 incremental rescue、preservation、新增关键问题、调用/usage/latency 与失败类型；私有包采用 Codex 审阅、项目所有者接受的冻结策略，诚实记录 `reviewer_independence=false`，并在任何 descriptor、attestation、题目、证据或 fingerprint 漂移时于 Provider 前阻断。 |
| `backend/src/casefile/benchmark/prose_rewrite_qualification.py` | N4.5 B2 一次性 Live 资格执行器：从冻结的初始 Consensus 直接执行最多两轮完整 Rewrite，每轮使用 `fidelity-only-v1` 全量复审；严格绑定 clean/stable Git source 与私有 package、精确 DeepSeek adapter/model/Prompt/component、四调用 Scene 预算和固定 24 分母。Provider 前以不可覆盖的 attempt manifest 预占 ID；能力/协议失败不补跑，基础设施失败终止后续调用且不得拼接。报告只保存哈希、verdict、稳定错误码、usage、latency、candidate 字符/block 数与脱敏 transport attempt；CLI 必须提供精确 Live 确认文字。 |
| `backend/src/casefile/domain/logical_mutation/` | 纯 Python Logical Mutation Kernel：discriminated operations、依赖拓扑排序、机械双向投影、NetworkX 封装图、关系传播策略单一来源、Impact Cone 与显式 v1/v2 policy registry；`closure/` 预计算不可变 ClosureContext/ClosureIndex，并实现 Claim、Hypothesis assessment、ReasoningPath/Resolution、typed integration 与 Shadow travel-time 确定性规则；公开接口不泄漏 NetworkX 类型。 |
| `backend/src/casefile/domain/logical_mutation/repair/` | M3.3 纯领域修复内核：版本化 RepairPolicy、角色化 ClosureObligation、MutationSimulation 资格评估，以及确定性 RepairScope。V1/V2 context 保留历史回放；V3 Alternative Planner 只枚举服务器完整模拟证明的 Claim status 或 incompatible dependency 移除候选，以规范 hash 绑定 operation、前后 obligation 与 candidate hash。Repair Engine 默认只接受 selected alternative ID，最多两轮从同一 baseline 重放并允许 obligation 不增的 staged progress；未知、过期、篡改、无候选、scope/protected/StructureLock 越界与 rebase mismatch 全部失败关闭。本模块不接 Provider/数据库/API/UI，不执行 Apply。 |
| `backend/src/casefile/agent_runtime/transport_diagnostics.py` | 对 Provider 异常 cause chain 做 timeout/connection/rate-limit/4xx/5xx/protocol/unknown 脱敏分类，输出稳定 retry、protocol 与 fallback 诊断，不保留 URL、正文、凭据或异常文本。 |
| `backend/src/casefile/agent_runtime/chat_intent.py` | CaseFile Chat 的确定性意图规则、Route 建议权限、显式编辑目标 Manifest 与 General Mutation abstention 判定；未绑定代词、未决二选一、字段/值缺失和非唯一删除均在 Provider 前要求澄清。 |
| `backend/src/casefile/agent_runtime/public_language.py` | CaseFile Chat v16 面向作者文字的确定性公开语言门禁；统一检查保留字段、工程术语、内部 ID、JSON Pointer、原始 JSON 与当前敏感值，并向 Qualification 暴露不保留正文的稳定规则 ID 探针。 |
| `backend/src/casefile/agent_runtime/general_mutation.py`、`general_mutation_prompt.py` | M3.4 runtime-private General Mutation Planner 严格输出契约、预算/依赖 DAG/保护集合规则与不可变 Prompt Package 渲染；不访问数据库或应用服务。 |
| `backend/src/casefile/benchmark/chat_public_language_qualification.py`、`chat_public_language_executor.py` | M3.6 Public Language 正式资格：冻结 16×3 普通问答、分析、审计、Create/Update/Delete、关联修改、内部信息诱导与正常近邻任务；在 clean revision、独立 `_test` PostgreSQL、精确 `deepseek-v4-pro` 上经公共 HTTP/Worker/Simulation/Apply 验证契约、泄漏、敏感值、Patch 安全、误拦与模型能力。真实凭据只从本地加密设置读入内存，测试库仅保存非密钥 canary，报告不保存正文或凭据。 |
| `backend/src/casefile/benchmark/closure_repair_lineage.py` | 对 repair domain、Closure policy、VerificationEngine、V3 Prompt/Schema、Application/Worker 与 Provider contract 生成统一 repair runtime fingerprint。 |
| `backend/src/casefile/benchmark/general_mutation_eval.py` | M3.4 General Mutation deterministic Kernel Regression：给定 Plan 后冻结 prompt/policy/binder/closure lineage，覆盖 Update/Create/Delete 与越权、ID、引用、DAG、预算失败关闭；只报告 kernel failure/escape，不冒充 Safety 或 Pro Capability。 |
| `backend/src/casefile/benchmark/general_mutation_capability.py` | General Mutation Outcome-first Provider Dev Capability：以自然语言作者任务驱动正式 Planner、Binder、Simulation，Reference Plan 仅证明题目可解，正交评分最终 CaseFile 状态、Safety 与 Scope，并区分能力、协议和基础设施失败。 |
| `backend/src/casefile/benchmark/general_mutation_safety.py`、`general_mutation_safety_executor.py` | M3.4-07d Safety / Abstention：在隔离 PostgreSQL 上运行真实 Router/Worker/Provider，按持久化拒绝、真实澄清问题、PatchOperation Oracle、Draft revision 与调用身份区分正确阻断、安全失败关闭、错误放行和基础设施失败；不执行 Apply。 |
| `backend/src/casefile/benchmark/general_mutation_backend_release.py`、`general_mutation_backend_executor.py` | M3.4-07e Backend Release：冻结 15×3 release cohort 和 20 项 Fault Matrix，经真实 HTTP、Queue、Worker、PostgreSQL、Pending PatchSet 与显式 Apply/Undo/Redo 验证 Create/Update/Delete、Delete impact hash、并发、幂等、篡改和事务恢复；任何 Safety、生命周期或基础设施失败均失败关闭。 |
| `backend/src/casefile/benchmark/general_mutation_holdout.py`、`general_mutation_lineage.py`、`general_mutation_evidence.py`、`general_mutation_qualification.py` | M3.4-07f 正式资格：严格加载 Git 忽略的 24 题 Private Holdout 与双遍 attestation，冻结 runtime/source/schema/suite/oracle/grader/prompt/binder/policy fingerprints，按 S0→07c→Holdout→07d→07e 编排同一 clean revision，限制纯基础设施 Holdout 整轮重跑，并生成 RFC 8785 SHA-256 Evidence Index；阶段异常也以稳定 blocker 失败关闭。 |
| `backend/src/casefile/benchmark/general_mutation_progress.py` | General Mutation Benchmark 逐 Trial 可观测性：输出机器可读进度并原子保存仅供诊断的完成序号/checkpoint；明确禁止从中选择性续跑能力失败。 |
| `backend/src/casefile/benchmark/GENERAL_MUTATION_BENCHMARK.md` | General Mutation Benchmark v2 宪法、S0-S5 分层、Dev Task/Oracle/Reference 契约、指标与 Suggest Ready 发布证据边界。 |
| `backend/src/casefile/benchmark/closure_repair_evidence.py`、`closure_repair_qualification.py` | 组装 M3.3 Evidence Index v2，校验每份报告自哈希、同 revision/runtime lineage、Holdout 最多一次且仅由首轮 infra 触发的完整重跑，以及 Backend 54-trial 最终资格；正式编排在同一 clean revision 上冻结 suite/gate/grader/runtime fingerprint，并按 Clean Dev → Holdout → Backend Release 顺序失败关闭。 |

## API 与 Worker

| 路径 | 职责 |
|---|---|
| `backend/src/casefile/api/schemas.py` | FastAPI 严格请求 DTO 与应用命令转换。 |
| `backend/src/casefile/api/dependencies.py` | 请求 Session、本地开发身份头，以及 Current Draft ID + base revision 成对门禁依赖。 |
| `backend/src/casefile/api/app.py` | 应用工厂、启动数据库门禁、统一错误体、健康检查与 `/api/v1` 路由。 |
| `backend/src/casefile/api/workflow.py` | Provider、SourceRecord、Brief、润色/拆解/生成 TaskRun、取消/最近任务恢复、TaskEvent/SSE、A 路径只读指标、v1 CaseFile 读取和有限编辑的 HTTP 路由。 |
| `backend/src/casefile/api/workbench.py` | 分析师工作台验证、来源与审计只读上下文的 HTTP 路由。 |
| `backend/src/casefile/api/verification.py` | 手动验证重跑、规范化 VerificationRun/finding 查询和作者审阅 HTTP 路由；只做协议转换，不承载验证规则。 |
| `backend/src/casefile/api/reverse_parse.py` | 路径 C 反向解析 HTTP 路由：文档上传/读取、解析块与逐项查询、逐项确认、失败重试与形成 Brief 候选。 |
| `backend/src/casefile/api/compiler.py` | 显式 Profile 创建/追加版本/查询、CompileRun 创建/列表/详情及不可变 Artifact 内容读取 HTTP 路由；执行状态仅投影关联 TaskRun。 |
| `backend/src/casefile/worker/` | 基于 PostgreSQL `FOR UPDATE SKIP LOCKED` 的 TaskRun 领取、lease/Attempt 恢复、任务执行、结果/事件原子持久化；只拥有运行与持久化编排，不拥有 Agent 领域规则。 |
| `backend/src/casefile/worker/runtime.py`、`backend/src/casefile/worker/dispatch.py`、`backend/src/casefile/worker/execution.py` | `Worker`、`WorkerConfig` 与 `provider_for_task` 稳定入口；以组合组件保留 claim → resolve handler → execute → finalize 主循环。Dispatcher 对九类可执行 TaskRun 精确注册、重复/未知类型失败关闭；Execution Context 只携带冻结 TaskRun、Attempt、短事务工厂、事件端口和按需 Provider，不跨模型调用持有 Session。 |
| `backend/src/casefile/worker/provider_resolution.py` | 在 Handler 解析后校验冻结 Provider provenance、短事务解密凭据并构造 Provider；支持显式 providerless Handler，禁止凭据进入 repr、事件或持久化。 |
| `backend/src/casefile/worker/queue.py`、`backend/src/casefile/worker/finalization.py` | 组合式 TaskRun claim、lease 恢复、取消观察、Attempt 初始化，以及成功/失败/取消终态和稳定事件落库；保留既有 PostgreSQL 状态、显式恢复与租约语义。 |
| `backend/src/casefile/worker/handlers/` | 按变化原因承载辅助 Brief、Brief Intake、Brief-to-Draft、Reverse Parse、Chat 与 Compiler 六类同步 Handler；冻结输入、Provider 调用次数、完成事务和结果结构保持兼容。Compiler Handler 以 providerless dispatch 进入专用执行器，Story Planner 再按冻结绑定短事务读取凭据。 |
| `backend/src/casefile/worker/executors/`、`backend/src/casefile/worker/closure_repair.py` | `chat.py` 以独立 Request、Mutation、Context/Compaction、Completion 组件组合 Chat Worker 编排并保留 route 解析兼容入口；`completion.py` 统一非 Chat 终态物化；`compiler.py`/`story_planner.py` 执行输入冻结、NarrativeIR、可选规划器、Artifact 幂等写入、取消和租约 fencing。`CLOSURE_REPAIR_MODE` 与 General Mutation rollout 只形成可审阅 PatchSet，不决定或绕过 Apply；领域 routing/context/repair 仍由 `agent_runtime`/domain 所有。 |
| `backend/src/casefile/worker/failures.py`、`backend/src/casefile/worker/observability.py`、`backend/src/casefile/worker/generation_reuse.py`、`backend/src/casefile/worker/input_contracts.py` | 分别拥有稳定失败分类与脱敏、TaskEvent 到 Step/ModelCall 投影及终态 usage、Brief-to-Draft Attempt 复用、冻结输入读取与规范哈希；禁止重新形成无明确职责的通用 support 模块。 |

## 领域模块

| 路径 | 职责 |
|---|---|
| `backend/src/casefile/benchmark/` | `brief_to_draft` Provider 级 Fixture 运行器与指标汇总；记录 CaseFile 结构有效率、模型调用/工具协议、修复次数、延迟和结构化诊断覆盖率。`closure_repair_eval.py` 保持 24 场景 Regression/Safety 默认入口并分派 Capability/私有 Holdout CLI；`eval_core.py` 定义 benchmark-local Suite/Task/Trial/Transcript/Outcome/Grader/Report 类型；`closure_repair_capability.py` 保留 61 Task Dev v1 严格 loader/Reference replay，并为 Holdout 输出分层、all-trials-success 与条件二轮指标；`closure_repair_holdout.py` 只加载忽略提交的 42 Task 私有包，验证分布、真实 CaseFile 输入、独立双审 hash、冻结 descriptor 和 18 Task release cohort；`closure_repair_gate.py` 执行前瞻冻结的 Backend Shadow Gate；`closure_repair_backend_release.py` 定义 18×3 API/Worker/PostgreSQL Release Eval 的报告契约、生命周期证据、确定性故障矩阵、`_test` 数据库硬门禁与生产 primary mutation 能力预检。clean Dev Gate 未通过或冻结 cohort 含生产 Chat 无法表达的 mutation 时，它在任何 Trial/Provider 调用前输出结构化 blocked；只有真实 production-path executor 支持完整 cohort，且 clean Dev Gate、cohort Gate、54 Trial 与全部故障证明同时通过，才可标记 Backend Shadow/Suggest Core eligible。组件级 Capability 不验证生产持久化，不能单独作为发布验收。`chat_context_eval.py` 运行 casefile-chat 上下文策略基线：五类冻结样本经 `build_chat_context_manifest` 计量后生成 `var/benchmark/context-baseline-v1.json`，并实现阶段 3 Boundary Continuation Eval——同一 Transcript 断点分别用完整原文与压缩后 Thread Memory 继续，确定性比较 Task Success/State Recall/Action Continuity/Repeated Work/Peak & Total Tokens 六道门禁，报告写入 `var/benchmark/context-boundary-v1.json`；`--gate-boundary` 供 `scripts/check.ps1` 作 M0 门禁。`context_tier_benchmark.py` 运行阶段 4 四档策略 A/B 并执行无回退和预算门禁；`validator_eval.py` 运行不调用 Provider 的 V0/V1/V2 确定性 release gate。 |
| `backend/src/casefile/benchmark/chat_outcome_eval.py`、`chat_outcome_suite.py`、`chat_outcome_fixtures.py` | CaseFile Chat Outcome Eval 的稳定入口、确定性 Grader/Contract Gate，以及独立的 34-task 冻结 fixture assembly；精确编辑按 JSON 语义值评分，Reference 必须通过生产 patch/simulation 门禁且禁止 no-op。 |
| `backend/src/casefile/agent_runtime/` | 目标无关的版本化 Prompt、OpenAI Responses/DeepSeek Chat Completions/Fake Provider、AES-256-GCM 用户密钥，以及全部 Agent 任务的结构化结果与 Validator 指标。`structured_output.py` 统一 Pydantic Schema 编译、OpenAI 原生 Structured Output、DeepSeek Beta strict tool、正式 JSON 模式降级、有限定向重试与用量汇总；当前 `brief_to_draft` 先生成对象计划，再由独立 Temporal Planner 建立作品内时间，随后生成故事世界和证据推理；竞争矩阵版本的 Evidence 在进入 Governance 前先执行至多两次携带上一份失败输出的语义定向修复（分阶段校验竞争组、信息接地路径与矩阵格子），v10–v14 由 Evidence Drafter 直接生成比较矩阵，v15 则把矩阵格子改为程序按路径确定性计算（`brief_to_draft_v15/matrix.py`），模型只对固定格子输出判定并由程序回填，失败时只针对剩余格子定向修复；再由 v15 Governance 基于实际 Evidence IR 建议答案或诚实未定论；所有 AI 结论固定为 `proposed`，只有作者能确认。v13 明确无时区壁钟精度格式，v14 强制创作者可见自然语言为简体中文，v8–v14 历史协议保持不变。`casefile_chat/v4` 为骨架上下文执行器包（`casefile-chat-prompt-input-v2`），阶段 2 验收通过后 registry 当前版本已切换为 `casefile-chat-v4`；`casefile_chat/v5` 为阶段 3 压缩后上下文执行器包，新增 `thread_memory` 输入块；阶段 4 起 shared 指令声明 `context_dashboard` 为只读仪表（预算耗尽停止工具、不得要求放宽限制）；`casefile_chat/v6` 复用 v5 契约并绑定 `casefile-chat-tools-v3`，新增 `retrieve_thread_evidence`/`request_thread_compaction` 使用规则；`casefile_chat_context_compactor/v1` 为只含 `compact` 组件、禁用工具、输出 `ThreadMemoryDelta` 的辅助 Agent 包，供 Provider `compact_thread_memory()` 复用 `_run_auxiliary`。 |
| `backend/src/casefile/agent_runtime/closure_repair.py`、`closure_repair_prompt.py` | M3.3 Provider-facing Closure Repair V3 契约与 Prompt Package 渲染：只展示服务器预证明 alternatives，模型严格选择一个 `selected_alternative_id` 并提供审计 reason，不得生成 object/path/value/operation；服务器重新绑定 alternative ID、operation 与 candidate hash，Provider 不拥有 Scope、Simulation、Rebase Proof、债务授权或 Apply 决策。 |
| `backend/src/casefile/application/closure_repair.py` | M3.3 Repair 生命周期边界：确定性构造 Chat primary MutationSet，序列化 Worker repair envelope，并在 PatchSet 持久化前从冻结 Draft 重放每轮 proposal；policy/hash/context/result 任一漂移即拒绝 companion operation。`shadow` 不改变 PatchSet，`suggest` 只返回已证明的 UPDATE provenance。 |
| `backend/src/casefile/agent_runtime/chat_execution.py` | Worker 与 M2 共用的纯执行内核：对冻结 Chat Request 调用 Provider、执行完成前引用与 audit finding 证据校验、通用 suggestion 字段和值门禁、路由 suggestion 权限收束、v15 Finalizer 后服务器补丁门禁、能力完整性 repair 和 usage/tool metrics 合并；最多执行三次有进展的 semantic repair，最终救援只允许服务器唯一 target-locked，耗尽后 fail-closed；不依赖 SQLAlchemy、FastAPI 或持久化。 |
| `backend/src/casefile/agent_runtime/chat_preparation.py` | Chat 请求 artifact 准备、上下文绑定、冻结输入与审计/编辑目标装配；不调用 Provider。 |
| `backend/src/casefile/agent_runtime/chat_validation.py` | CaseFile Chat 纯验证编排契约：稳定 ValidationIssue、ValidationReport、RepairPlan、authoritative target resolution 及 bounded repair-state 选择；不调用 Provider 或持久化。 |
| `backend/src/casefile/agent_runtime/chat_validation_contracts.py` | Chat validation 的不可变 issue/report/repair 数据契约与稳定序列化。 |
| `backend/src/casefile/agent_runtime/chat_reference_normalization.py` | 候选引用槽规范化、保守唯一引用补全和 audit finding 排序去重；不拥有 Provider 重试。 |
| `backend/src/casefile/agent_runtime/chat_safe_patches.py` | 记录 v15 Finalizer 后由服务器证明安全的补丁候选，复用 chat_tools 的字段校验与 issue-delta simulation，执行 JSON 值规范等价比较与唯一目标确定性物化；不调用 Provider、不写 Draft。旧 Ledger 编译接口仅供历史 v14 回放。 |
| `backend/src/casefile/agent_runtime/chat_tools.py` | `casefile_chat` 的确定性只读/建议校验工具集与 Frozen Tool Ledger：全卷集合清单与分页浏览 `list_casefile_records`、一跳关系读取 `get_related_objects`、关键字检索 `search_casefile`、单对象全文 `get_casefile_object`、分页冻结验证快照 `get_validation_issues` 与补丁白名单校验 `validate_patch_proposal`；`check_patch_proposal` 与 `simulate_patch_delta` 同时作为 v15 服务器门禁的纯核心。工具按路由 profile 选择、按 TaskRun 冻结 `toolset_version` 拒绝 v2 新工具给旧任务，所有结果只来自冻结 CaseFile，不触网不写库。阶段 2 起所有工具结果经 `bounded_tool_result_json` 套字符上限并标记 `truncated`，`ChatToolContext` 维护最近原文与折叠区账本；v14/v15 将其冻结为带 entry/result hash 的无工具 Finalizer 输入。`casefile-chat-tools-v3` 按路由 `context_tools` 声明只读开放 `retrieve_thread_evidence` 与 `request_thread_compaction`。 |
| `backend/src/casefile/agent_runtime/context/` | 可插拔、版本化的 casefile-chat 上下文工程基座。`models.py` 定义 ContextBlock/ContextPolicy/ContextAssembly/ContextManifest 等数据契约（block 带 age_turns/last_access_turn 生命周期字段）；`protocols.py` 定义 ContextStage/TokenEstimator 插件协议；`registry.py` 按名称注册策略并校验 Policy 引用；`engine.py` 按 Policy 声明顺序确定性执行 Stage，未知策略版本回退 legacy 并产出 fallback 决策；`manifest.py` 把装配结果投影为不含 payload 的审计账本；`estimators.py` 提供多厂商通用保守 Token 估算、按 provider/model 选择的估算器注册表与 usage 校准比；`budget.py` 在 enforce_budget 开启时按 block_limits/trim_order 确定性裁剪可裁剪文本块，受保护块只记账不删改；`dashboard.py` 投影只读上下文仪表（已用/剩余预算、最大块、受保护块、可恢复证据 ID）并校验 Runtime 护栏（pinned 不可裁剪、Recent Turns 受保护、归档必须可恢复、总输入硬上限）；`evidence.py` 提供 `scheme://id` 证据指针契约与解析器注册表（不删原文，只换指针）；`thread_memory.py` 定义 `ChatThreadMemoryState`/`ThreadMemoryDelta` 严格契约、`ThreadMemoryCompactorV1`（旧状态+新增原文确定性合并，constraints/decisions 原文 carry-forward、verified_facts 按 source 去重，永不 memory+memory）、校验/保留检查、压缩输入哈希与默认压缩器注册表；`assembly_render.py` 把装配块投影为 `casefile-chat-prompt-input-v2` 契约载荷（含可选 `thread_memory` 与 `context_dashboard` 块），供 v4/v5 Prompt 包在 Provider 前校验渲染。 |
| `backend/src/casefile/agent_runtime/context/policies/` | Policy-as-data 资源：`schema.json` 校验版本化 Context Policy 文档；`loader.py` 按 `context_policy_version` 从不可变 JSON 加载并校验策略；`agent-focus-v1` legacy 策略通过 `legacy_full_injection_v1` Stage 对现有全量注入输入只计量不删改；`casefile-chat-context-v1` 为阶段 2 正式策略：skeleton→focus_objects→history_window→validation_trim→chat_contract 五段装配。M0/M1 验收通过后已切为**默认策略**并配对 Prompt v4；`CASEFILE_CHAT_CONTEXT_ROLLOUT=agent-focus-v1` 可整组回退 legacy。`casefile-chat-context-v2` 为阶段 3 灰度策略：在 v1 基础上于 `history_window` 之后插入 `thread_memory` Stage（consume `thread_memory_state` extra input），由 `CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2` 启用并配对 Prompt v5。`casefile-chat-context-v3` 为阶段 4 灰度策略：与 v2 同布局，由 `CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v3` 启用，配对 Prompt v6 + `casefile-chat-tools-v3`，Dashboard 中声明可恢复证据 ID 供只读 Context Tools 使用。 |
| `backend/src/casefile/agent_runtime/context/strategies/sources/` | 确定性源策略：`casefile_skeleton.py` 每记录只留 id/collection/label/type 与集合计数；`focus_objects.py` 焦点对象完整展开（≤8）加一跳邻居摘要（≤16），悬空引用只读剪除；`chat_contract.py` 产出 input_hash/author_message/editable/focus/validation/routing 契约块；`thread_memory.py` 从 `run.extra_input["thread_memory_state"]` 校验并产出带生命周期元数据的 `thread_memory` 块。 |
| `backend/src/casefile/agent_runtime/context/strategies/selectors/history_window.py` | 按路由 profile 保留最近 K 条原文；首条用户消息（线程锚点）与含否定/硬约束词的消息确定性 Pin，裁剪决策入 `context.built` 审计账本。 |
| `backend/src/casefile/agent_runtime/context/strategies/transformers/validation_trim.py` | 焦点/本轮 mention 命中的 issue 全量保留，其余压缩为 id/rule_id/severity/title/message(≤200)/object_refs；`validate_request` 门禁路由保留全量快照。 |
| `backend/src/casefile/agent_runtime/context/strategies/legacy.py` | `LegacyChatInputStage`：把 Worker 预渲染的既有 executor 输入包装成可计 Token 的上下文块；`legacy_chat_routing_payload` 兼容别名复用 `models.chat_routing_payload_as_dict`，保证 Prompt 渲染与上下文审计的 routing 序列化一致。 |
| `backend/src/casefile/agent_runtime/brief_to_draft_v8/validation.py` | v8+ 生成工作流复用的 blueprint、story、evidence 和竞争矩阵纯验证/定向修复 helper；不拥有组件调用顺序。 |
| `backend/src/casefile/agent_runtime/provider_adapters/` | Provider 适配器边界：`protocols.py` 定义端口与稳定错误；`fake.py` 提供零网络测试实现；`openai.py`、`deepseek.py` 分别保留协议差异并实现专用 `repair_closure()`；`generation.py` 负责任务版本选择、分区生成与候选装配；`shared.py` 统一输出规范化、错误映射、辅助 Agent、raw output 审计与 usage 合并。`providers.py` 仅作兼容门面。 |
| `backend/src/casefile/agent_runtime/observability.py` | 对成功候选执行不参与门禁的确定性 Brief 语义覆盖代理，并把请求、缓存、推理 Token 标准化为可追溯但不虚构价格的成本输入。 |
| `backend/src/casefile/core/` | 后续纯领域与应用端口的公共落位；不得依赖 FastAPI、SQLAlchemy 或具体 Provider。 |
| `backend/src/casefile/reasoning/` | 推理图分析与搜索策略的预留落位。 |
| `backend/src/casefile/validation/` | 确定性 Schema、引用、时间、知识与发布规则的预留落位。 |
| `backend/src/casefile/simulation/` | 玩家模拟运行与报告组装的预留落位。 |
| `backend/src/casefile/compiler/` | Target Adapter、IR、Renderer、Source Map 与发布包的预留落位。 |
| `backend/src/casefile/importers/` | `text_extraction.py` 将 .txt/.md/.markdown/.docx/文本型 PDF 提取为纯文本（5MB 体积与 100 页上限门禁）并按结构切块，供路径 C 反向解析使用。 |
| `backend/src/casefile/object_store/` | 对象存储端口、本地实现与未来远端 Adapter 的预留落位。 |

## 测试

| 路径 | 职责 |
|---|---|
| `backend/tests/contract/` | 根目录跨语言契约和编辑闭环 Fixture 的契约测试。 |
| `backend/tests/unit/test_foundation_metadata.py` | 静态验证精确 66 表、Identity 主键、JSONB 白名单、个人归属、文档同步和关键约束，不连接数据库。 |
| `backend/tests/unit/test_casefile_contract.py` | 验证 v1 CaseFile Schema、自身合法性、三类产品 Fixture、确定性语义错误和规范哈希。 |
| `backend/tests/unit/test_prose_checklist.py` | 验证 N4.5 Profile v2、PlannerInput v1–v3 兼容、Checklist 稳定顺序/hash、Scene 链、对象闭包、Render 阶段和 Unicode Evidence 绑定。 |
| `backend/tests/unit/test_prose_judge_council.py` | 验证 N4.5 Semantic Council、批量 Arbiter、恢复指纹、服务端 Evidence 目录的确定性切分与 Gold 覆盖、目录外引文失败关闭。 |
| `backend/tests/unit/test_prose_writer.py`、`test_prose_writer_benchmark.py` | 验证 N4.5-04 Writer candidate 规范化、最小请求、指纹恢复、DeepSeek/Fake 审计，以及 24 Task suite 重建、冻结 Fidelity-only 闭环、固定分母、失败分类与报告脱敏。 |
| `backend/tests/unit/test_prose_rewriter.py` | 验证 N4.5-05 完整 Scene Rewrite 请求、失败/保持语义投影、直接 Render lineage、精确恢复、DeepSeek/Fake 脱敏审计、v2 Unicode `length_contract`，以及初稿通过、Round 1 rescue、Round 2 封顶、预算和失败分类。 |
| `backend/tests/unit/test_prose_rewrite_benchmark.py` | 验证 B2 24 题矩阵、坏正文/Consensus/previous-pass/hash 重建、生成器一致性、两轮 Fake rescue 指标、固定分母、报告脱敏，以及 private qualification descriptor 自哈希和冻结 Codex-owner review policy 的 fail-closed 边界。 |
| `backend/tests/unit/test_prose_rewrite_qualification.py` | 以 mocked DeepSeek transport 验证 B2 正式执行器的冻结初始 Consensus、24 题单次运行、两轮 rescue、完整 preservation、协议失败不重试、基础设施终止、source/provider gate、不可变输出、报告自哈希/脱敏和显式 Live 确认；测试不触网。 |
| `backend/tests/unit/test_m3_reasoning_closure.py` | 验证 M3.1 v1 parity、v2 assessment 传播、Evidence/Claim/Hypothesis/ReasoningPath/Resolution 闭包、typed integration、semantic finding 门禁桥接、既有债务 grandfather、人工授权、Shadow warning 与确定性重复运行。 |
| `backend/tests/unit/test_agent_providers.py` | 验证 OpenAI/DeepSeek Provider 路由、DeepSeek 官方兼容端点和无 Key 网络调用门禁。 |
| `backend/tests/unit/test_context_engine.py` | 验证 Context Policy 资源加载、未知版本 legacy 回退、引擎确定性顺序/跳过/替换/跳转/预算标记、legacy 输入计量 Manifest 和共享 routing 序列化。 |
| `backend/tests/unit/test_context_evidence.py` | 验证证据指针解析、解析器注册表、缺失/悬空引用的可审计决策和 URI 兜底描述。 |
| `backend/tests/unit/test_context_budget.py` | 验证保守估算器注册表选择、usage 校准中位数、确定性文本裁剪、受保护块/非法限额/非文本块的预算决策，以及 Engine 与 Manifest 的裁剪集成。 |
| `backend/tests/unit/test_chat_context_eval.py` | 验证 casefile-chat 上下文基线样本可复现、未知策略计入 fallback、报告 JSON 无损落盘、Boundary 场景解析器边界，以及阶段 3 完整上下文 vs 压缩后上下文六道门禁全部通过且确定性可复现。 |
| `backend/tests/unit/test_context_phase2.py` | 验证阶段 2 五类确定性策略（skeleton/focus/history Pin/validation Trim/契约块）、v4 输入契约渲染、token 削减 ≥50%、未知策略回退、默认 v1/legacy 回退开关与工具结果字符上限/折叠。 |
| `backend/tests/unit/test_chat_tools.py` | 验证确定性 chat 工具集：路由 toolset 白名单、检索/关系/校验工具的冻结输入契约、预算耗尽行为与折叠账本；阶段 4 增加 v3 Context Tools 门控、证据指针只读恢复、未声明指针拒绝、压缩请求只登记不执行。 |
| `backend/tests/unit/test_context_phase3.py` | 验证阶段 3 Thread Memory 严格契约、确定性压缩输入、carry-forward 合并、保留/证据指针校验、压缩器注册表冲突、v2 策略排序、`thread_memory` Stage 装配渲染与 Provider Fake 压缩路径。 |
| `backend/tests/unit/test_context_phase4.py` | 验证阶段 4 生命周期元数据（age/last_access 进 Manifest）、只读 Context Dashboard（已用/剩余预算、最大块、受保护块、可恢复证据 ID）、总输入硬上限不可放宽、归档不可恢复与 pinned 可裁剪两种护栏违规检测。 |
| `backend/tests/unit/test_context_tier_benchmark.py` | 验证阶段 4 四档策略注册表顺序冻结、A/B 门禁全通过（零回退/零护栏违规/峰值不劣于 legacy），固定样本套件可复现。 |
| `backend/tests/unit/test_structured_output.py` | 验证 DeepSeek strict transport Schema、Beta 强制工具协议、正式 JSON 自动降级、OpenAI 原生结构输出与最多三次的有限修复状态机。 |
| `backend/tests/unit/test_benchmark_runner.py` | 验证 fake `brief_to_draft` Benchmark 与工具调用指标。 |
| `backend/tests/unit/test_closure_repair_benchmark.py` | 验证 24 场景 Regression、安全 all-of-trials、61 Task/52 Policy Capability 覆盖、Reference replay、严格契约、DeepSeek mock report 分母、Trial artifact 与 Capability CLI 失败关闭。 |
| `backend/tests/unit/test_workbench_read_model.py` | 验证工作台确定性错误的稳定中文输出和真实 `source_fragment` 标识/JSON Pointer 追溯。 |
| `backend/tests/unit/test_a_path_observability.py` | 验证 Brief 八类语义覆盖、标准化成本用量，以及不建表的生成、采用和采用后编辑漏斗推导。 |
| `backend/tests/unit/test_task_cancellation.py` | 验证取消终态对 Attempt/Agent pending 消息的统一收敛，以及取消 HTTP 端点的 202 委派契约。 |
| `backend/tests/unit/test_chat_goal_execution.py`、`backend/tests/integration/test_goal_session_execution.py` | 验证 M3.8 Checkpoint 三类安全点、obligations hash 恢复门禁、非终态确定性模板、capability Observation 持久化、单 Finalizer，以及 TaskRun/Attempt/Delivery/Revision/下一 slice 原子事务、claim 幂等、过期 Attempt 接管、旧 Attempt fencing、故障回滚和同 Thread 单活跃约束。 |
| `backend/tests/unit/test_chat_goal_interactive_benchmark.py`、`backend/tests/integration/test_chat_goal_interactive_executor.py` | 验证 M3.8-07 v2 suite/descriptor/attestation/指纹、typed oracle、Goal candidate/successor feasibility 和资格分母/scenario/family/safety 门禁；在真实 HTTP/Worker/PostgreSQL 上执行 8 个 family、三类 safe point、多消息 FIFO、提前 follow-up 409、clarification、Patch Apply、stale Apply 拒绝与 cancel。测试使用 Fake Provider，不触网。 |
| `backend/tests/unit/test_scene_plan.py` | 验证 N4.4 冻结输入哈希/DAG、模型 Candidate 接地与 provenance 门禁、服务器规范化和忽略 directive 措辞的语义签名，并保留 ScenePlanIR 确定性派生、NetworkX 查询和篡改失败关闭。 |
| `backend/tests/unit/test_scene_plan_benchmark.py` | 验证 ScenePlan 24-task 审计矩阵到 v2 Input/ModelView/冻结 runtime reference 的确定性升级、8 个替代表达 G4 等价回归、v2 Safety mutation reason code、Shadow runtime 报告分母/fingerprint、Flash G3 候选/reference 传递与空响应单次重试、Task-cluster bootstrap、G3/G4 promotion checks、live Provider 注入路径与正式 24×3 Pro+Flash 参数失败关闭；测试本身不触网。 |
| `backend/tests/fixtures/contracts/` | v1 CaseFile 三类有效产品样例，以及非法 ID、悬空引用、错误引用类型、重复顺序和未知结构字段的独立失败样例。 |
| `backend/tests/integration/test_foundation_migrations.py` | 在明确的可丢弃 PostgreSQL `_test` 库验证完整升降级、66 表、SourceRecord/注册/子类型门禁、引用、归属、并发、Canon/Exposure Plan 门禁和不可变触发器。 |
| `backend/tests/integration/foundation_migration_tables.py` | 集中维护基础迁移测试使用的精确 66 表清单，避免主迁移测试文件继续膨胀。 |
| `backend/tests/integration/test_exposure_plan_migration.py` | 在真实 `_test` PostgreSQL 验证新 Draft 自动创建空 Exposure Plan，以及计划修订、条目和引用不可更新/删除。 |
| `backend/tests/integration/application_services_test_support.py` | 为应用服务集成测试集中提供 `_test` PostgreSQL 生命周期、Provider 与建案 helper；由 integration `conftest.py` 暴露共享 fixture。 |
| `backend/tests/integration/chat_outcome_canned_support.py` | 复用 M1 生产路径 trial runner（建案/采用→send_agent_message→Worker→持久化 Outcome 评分），支持指定任务 provider 与按实际冻结卷宗生成消息；供基线测试、阶段 2 验收与 live 验收共享，避免 30 任务 harness 复制。 |
| `backend/tests/integration/test_chat_outcome_canned.py` | M1 DB Canned 基线：30 个 T1 任务走真实生产路径，由确定性 Canned Provider 完成并评分；另覆盖手动验证重跑的 TaskRun 冻结、Worker、`verification.*` 事件和规范化结果 lineage；是上下文灰度的通过率不降基线。 |
| `backend/tests/integration/test_chat_context_phase2_acceptance.py` | 阶段 2 灰度验收：30 任务全部冻结 `casefile-chat-v4`+`casefile-chat-context-v1`，校验 `context.built` v1 分块、零 fallback，并比较真实 ledger Token 与同一请求 legacy 渲染，聚合下降必须 ≥50%；报告写入 `CASEFILE_CHAT_CONTEXT_ACCEPTANCE_REPORT`。 |
| `backend/tests/integration/test_chat_context_phase3_acceptance.py` | opt-in（`CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2`）阶段 3 验收：真实生产路径验证首轮完成后 Rolling Compaction 落库并冻结 `context_state`，次轮 v5 请求携带 `thread_memory` 块；M1 对比同一编辑任务在 legacy 与压缩后上下文两条 Trial，要求补丁建议合法数不降且压缩后请求确实绑定 Thread Memory。 |
| `backend/tests/integration/test_chat_context_phase4_acceptance.py` | opt-in（`CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v3`）阶段 4 验收：真实生产路径验证 v3 策略冻结 v6 Prompt + `casefile-chat-tools-v3`，次轮请求携带 `context_dashboard`、注入 `thread_evidence_resolver` 且能只读解析 `thread://{thread_id}/message/{seq}` 原始消息，Rolling Compaction 行为不回退。 |
| `backend/tests/integration/test_chat_context_phase3_live_acceptance.py` | opt-in 阶段 3 live 验收（`scripts/acceptance-chat-context-v2.ps1 -LiveProvider`）：五类任务在 legacy 全量历史与 v2 Thread Memory 两种 rollout 下各跑「预热轮 + 任务轮」真实 Provider 生产路径，产出 pass rate、压缩事件与持久化 state 报告；脚本比较通过率并要求 v2 每个线程都有 `context.compacted` 状态。 |
| `backend/tests/integration/test_chat_context_phase2_live_acceptance.py` | opt-in live 验收（env 驱动）：先在 `_test` 库写入真实 Provider 凭据，再把任务文案按实际生成卷宗动态适配（实体/事件/验证/编辑/大卷宗五类），同一批任务在 legacy/v1 两轮生产路径调用真实 Provider，产出逐任务候选与失败明细报告；任务级生成失败不中断整体比较，`scripts/acceptance-chat-context-v1.ps1` 负责对比通过率并判回归。 |
| `backend/tests/integration/test_application_services.py` | 在真实 `_test` PostgreSQL 验证 SourceRecord、Worker 候选持久化、首次/再次采用、A/B 工作稿隔离、v1 有限编辑和 Agent 协作闭环。 |
| `backend/tests/integration/test_multiple_draft_migration.py`、`test_multiple_drafts.py` | 验证旧数据升级回填 Current Draft、复合外键与 Draft 内对象 ID 唯一，以及并发激活、归档/锁定门禁和编辑/快照/验证/来源/审计隔离。 |
| `backend/tests/integration/test_application_task_lifecycle.py` | 在真实 `_test` PostgreSQL 验证 TaskRun Prompt 版本、queued/running/orphan 取消、lease 恢复、Provider 配置冻结与不可变事件。 |
| `backend/tests/integration/test_api_vertical_slice.py` | 在真实 `_test` PostgreSQL 验证 Provider 设置、原稿/润色候选、Brief 原子确认、三类 TaskRun、候选采用、工作台验证/来源/审计读模型、SSE 恢复与完成门禁闭环。 |
| `backend/tests/integration/test_brief_to_draft_v8_live_acceptance.py` | 显式 opt-in 的真实 Provider 组件化 v8–v15 验收（默认版本读取 Prompt Registry）：从本地开发库复制已加密凭据到一次性 `_test` 库，通过 API 与 Worker 轮换三种候选策略；v11–v14 轮换五类时间/空间/竞争矩阵场景，v15 额外加入 2×8+ 与 3×8+ 两档密集竞争矩阵场景（共七类）并对 30 次发布验收强制 Evidence 语义 SLO（首次通过率 ≥ 90%、最多一次定向修复后 ≥ 98%）；报告按持久化步骤产物重放 Evidence 图/矩阵语义校验，统计首次通过率、修复恢复率、issue 计数与矩阵规模，并检查步骤/模型调用持久化、SSE、诊断、候选语义和 Draft/Canon 未自动写入边界。 |

## 66 表清单

当前正式业务表恰好为 66 张：

- 身份、输入与任务：`users`、`projects`、`user_provider_settings`、`source_records`、`briefs`、`brief_versions`、`task_runs`、`task_attempts`、`task_events`、`agent_step_runs`、`agent_model_calls`、`imported_documents`、`parse_items`、`idea_candidates`。
- 编译运行：`compiler_profiles`、`compiler_profile_versions`、`compile_runs`、`compile_artifacts`。
- 协作与上下文：`agent_threads`、`agent_thread_context_states`、`agent_messages`、`agent_patch_sets`、`agent_patch_operations`、`brief_intakes`、`brief_intake_questions`、`brief_intake_candidates`。
- 编辑与契约映射：`casefiles`、`drafts`、`casefile_objects`、`casefile_refs`、`casefile_contract_refs`、`draft_operations`。
- 叙事与内容：`narrative_phases`、`entities`、`relationships`、`people`、`locations`、`events`、`information_units`、`evidence_items`、`testimonies`、`claims`、`knowledge_states`、`knowledge_state_entries`。
- 推理与结论：`hypotheses`、`reasoning_paths`、`reasoning_nodes`、`reasoning_edges`、`resolution_specs`、`resolution_slots`、`casefile_constraints`、`structure_locks`。
- 展示设计：`exposure_plans`、`exposure_plan_revisions`、`exposure_plan_entries`、`exposure_plan_entry_refs`、`exposure_plan_obligations`、`exposure_plan_obligation_refs`。
- 版本与审计：`draft_snapshots`、`canon_versions`、`audit_events`。

新增或删除表必须同步更新 ORM、迁移、两份数据库说明、元数据测试和本文。
