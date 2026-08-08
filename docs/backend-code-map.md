# 后端代码职责地图

本文涵盖 `backend/` 下所有受 Git 跟踪的源码文件职责。新增、删除、重命名后端源文件时必须同步更新本文。

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
| `backend/src/casefile/data_postgres/repositories.py` | 按 Project/Draft/Snapshot 聚合封装所有者过滤、当前态读写、Operation、语义引用和安全软删。 |
| `backend/src/casefile/data_postgres/models/identity.py` | `users`、单一所有者 `projects` 与用户级密文 `user_provider_settings` ORM。 |
| `backend/src/casefile/data_postgres/models/casefile.py` | `casefiles`、`drafts`、轻量 `casefile_objects` 注册表、旧语义边 `casefile_refs`、v1 `casefile_contract_refs` 和 `draft_operations` ORM。 |
| `backend/src/casefile/data_postgres/models/content.py` | 旧 Narrative Phase 兼容存储、Entity/Person、v1 Relationship/Location、Event、Information Unit/Evidence/Testimony、Claim 与 Knowledge State ORM。 |
| `backend/src/casefile/data_postgres/models/reasoning.py` | Hypothesis、Reasoning Path/Node/Edge、Resolution Spec/Slot、Constraint 与 Structure Lock ORM。 |
| `backend/src/casefile/data_postgres/models/versioning.py` | `draft_snapshots`、`canon_versions`、`audit_events` ORM。 |
| `backend/src/casefile/data_postgres/models/workflow.py` | `briefs`、不可变 `brief_versions`、不可变 `source_records`、三类 `task_runs`、`task_attempts` 与不可变 `task_events` ORM。 |
| `backend/src/casefile/data_postgres/models/agent_execution.py` | v8 `agent_step_runs` 与 `agent_model_calls` 的组件产物、哈希复用、结构化诊断、失败原文保留策略和终态审计 ORM。 |
| `backend/src/casefile/data_postgres/models/__init__.py` | 汇总导入全部 ORM，供 Alembic metadata 发现。 |
| `backend/src/casefile/data_postgres/models/benchmark.py` | Benchmark 持久化模型的预留落位；当前不定义或导出 ORM。 |

## 核心业务与应用层

| 路径 | 职责 |
|---|---|
| `backend/src/casefile/contracts/` | 加载根目录 v1 Schema 的运行时镜像，执行结构、稳定 ID 引用和确定性语义校验。 |
| `backend/src/casefile_contracts/` | 从根目录 Schema 生成、供后端运行时使用的 Pydantic 契约模型；禁止手改。 |
| `backend/src/casefile/application/commands.py` | 与 HTTP 解耦的 Project、Entity 和 Event 类型化写入命令。 |
| `backend/src/casefile/application/errors.py` | 应用层稳定错误码、公开消息和传输无关的错误详情。 |
| `backend/src/casefile/application/snapshot.py` | 从全部规范化当前态投影 CaseFile JSON，稳定排序、契约校验并计算 RFC 8785 SHA-256。 |
| `backend/src/casefile/application/services.py` | Project、Draft 对象/引用编辑和 Snapshot 的事务边界、并发控制及应用规则。 |
| `backend/src/casefile/application/casefile_v1.py` | 在目标无关的 v1 CaseFile JSON 与 38 表规范化当前态之间执行原子写入、完整投影、契约引用映射和规范哈希。 |
| `backend/src/casefile/application/v1_editing.py` | Entity、Location、Event 的有限字段编辑、revision 冲突检查和 v1 契约往返门禁。 |
| `backend/src/casefile/application/workflow_service.py` | Provider 设置、不可变 SourceRecord、Brief 草稿/原子确认/冻结版本、三类 TaskRun 创建、最近任务恢复与 SSE 事件查询的事务边界。 |

## API 与 Worker

| 路径 | 职责 |
|---|---|
| `backend/src/casefile/api/schemas.py` | FastAPI 严格请求 DTO 与应用命令转换。 |
| `backend/src/casefile/api/dependencies.py` | 请求 Session、本地开发身份头和 Draft base revision 依赖。 |
| `backend/src/casefile/api/app.py` | 应用工厂、启动数据库门禁、统一错误体、健康检查与 `/api/v1` 路由。 |
| `backend/src/casefile/api/workflow.py` | Provider、SourceRecord、Brief、润色/拆解/生成 TaskRun、最近任务恢复、TaskEvent/SSE、v1 CaseFile 读取和有限编辑的 HTTP 路由。 |
| `backend/src/casefile/worker/` | 基于 PostgreSQL `FOR UPDATE SKIP LOCKED` 的三类 TaskRun 领取、lease/Attempt 恢复、Agent 执行、结果/事件原子持久化。 |

## 领域模块

| 路径 | 职责 |
|---|---|
| `backend/src/casefile/benchmark/` | `brief_to_draft` Provider 级 Fixture 运行器与指标汇总；记录 CaseFile 结构有效率、模型调用/工具协议、修复次数、延迟和结构化诊断覆盖率。它明确不验证 TaskRun、Worker、持久化、SSE 或候选采用边界，不能单独作为发布验收。 |
| `backend/src/casefile/agent_runtime/` | 目标无关的版本化 Prompt、OpenAI Responses/DeepSeek Chat Completions/Fake Provider、AES-256-GCM 用户密钥，以及全部 Agent 任务的结构化结果与 Validator 指标。`structured_output.py` 统一 Pydantic Schema 编译、OpenAI 原生 Structured Output、DeepSeek Beta strict tool、正式 JSON 模式降级、有限定向重试与用量汇总；当前 `brief_to_draft` 先生成对象计划，由服务端分配稳定 ID，再并发生成故事世界、证据推理、解答与约束三个分区并定向修复；历史 Prompt 版本仍保留原工具协议。 |
| `backend/src/casefile/core/` | 后续纯领域与应用端口的公共落位；不得依赖 FastAPI、SQLAlchemy 或具体 Provider。 |
| `backend/src/casefile/reasoning/` | 推理图分析与搜索策略的预留落位。 |
| `backend/src/casefile/validation/` | 确定性 Schema、引用、时间、知识与发布规则的预留落位。 |
| `backend/src/casefile/simulation/` | 玩家模拟运行与报告组装的预留落位。 |
| `backend/src/casefile/compiler/` | Target Adapter、IR、Renderer、Source Map 与发布包的预留落位。 |
| `backend/src/casefile/importers/` | text、Markdown、DOCX、文本型 PDF 导入及来源映射的预留落位。 |
| `backend/src/casefile/object_store/` | 对象存储端口、本地实现与未来远端 Adapter 的预留落位。 |

## 测试

| 路径 | 职责 |
|---|---|
| `backend/tests/contract/` | 根目录跨语言契约和编辑闭环 Fixture 的契约测试。 |
| `backend/tests/unit/test_foundation_metadata.py` | 静态验证精确 38 表、Identity 主键、JSONB 白名单、个人归属、文档同步和关键约束，不连接数据库。 |
| `backend/tests/unit/test_casefile_contract.py` | 验证 v1 CaseFile Schema、自身合法性、三类产品 Fixture、确定性语义错误和规范哈希。 |
| `backend/tests/unit/test_agent_providers.py` | 验证 OpenAI/DeepSeek Provider 路由、DeepSeek 官方兼容端点和无 Key 网络调用门禁。 |
| `backend/tests/unit/test_structured_output.py` | 验证 DeepSeek strict transport Schema、Beta 强制工具协议、正式 JSON 自动降级、OpenAI 原生结构输出与最多三次的有限修复状态机。 |
| `backend/tests/unit/test_benchmark_runner.py` | 验证 fake `brief_to_draft` Benchmark 与工具调用指标。 |
| `backend/tests/fixtures/contracts/` | v1 CaseFile 三类有效产品样例，以及非法 ID、悬空引用、错误引用类型、重复顺序和未知结构字段的独立失败样例。 |
| `backend/tests/integration/test_foundation_migrations.py` | 在明确的可丢弃 PostgreSQL `_test` 库验证七段升降级、38 表、SourceRecord/注册/子类型门禁、引用、归属、并发、Canon 门禁和不可变触发器。 |
| `backend/tests/integration/test_application_services.py` | 在真实 `_test` PostgreSQL 验证 SourceRecord、三类 Worker、lease 恢复、不可变历史和 v1 有限编辑闭环。 |
| `backend/tests/integration/test_api_vertical_slice.py` | 在真实 `_test` PostgreSQL 验证 Provider 设置、原稿/润色候选、Brief 原子确认、三类 TaskRun、SSE 恢复与完成门禁闭环。 |
| `backend/tests/integration/test_brief_to_draft_v8_live_acceptance.py` | 显式 opt-in 的真实 Provider v8 验收：从本地开发库复制已加密凭据到一次性 `_test` 库，通过 API 与 Worker 执行策略轮换任务，检查步骤/模型调用持久化、SSE、诊断和 Draft/Canon 未自动写入边界。 |

## 47 表清单

当前正式业务表恰好为 47 张：

- 身份、输入与任务：`users`、`projects`、`user_provider_settings`、`source_records`、`briefs`、`brief_versions`、`task_runs`、`task_attempts`、`task_events`、`agent_step_runs`、`agent_model_calls`。
- 编辑与契约映射：`casefiles`、`drafts`、`casefile_objects`、`casefile_refs`、`casefile_contract_refs`、`draft_operations`。
- 叙事与内容：`narrative_phases`、`entities`、`relationships`、`people`、`locations`、`events`、`information_units`、`evidence_items`、`testimonies`、`claims`、`knowledge_states`、`knowledge_state_entries`。
- 推理与结论：`hypotheses`、`reasoning_paths`、`reasoning_nodes`、`reasoning_edges`、`resolution_specs`、`resolution_slots`、`casefile_constraints`、`structure_locks`。
- 版本与审计：`draft_snapshots`、`canon_versions`、`audit_events`。

新增或删除表必须同步更新 ORM、迁移、两份数据库说明、元数据测试和本文。
