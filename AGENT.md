# CaseFile 代码职责地图

本文是 coding agent 的仓库导航和落位约束。开始编码前必须完整阅读；新增、删除、重命名文件或改变文件职责时，必须在同一变更中更新本文。

## 1. 总体边界

- 仓库采用前后端同仓、运行时分离的 monorepo。
- `apps/web` 只负责界面、浏览器状态和调用生成的 API Client，不直接访问数据库，也不手写后端 DTO。
- `backend/src/casefile/core` 是纯领域与应用层，不得 import FastAPI、SQLAlchemy、Provider SDK 或具体文件系统实现。
- `backend/src/casefile/api` 和 `backend/src/casefile/worker` 是两个运行入口；可组合 core 与外层 adapter，但互不调用对方入口。
- `contracts/schemas` 是跨语言机器契约的唯一源头；`contracts/generated` 只存生成物，禁止手改。
- `docs/` 是本地需求输入，已由 `.gitignore` 排除。代码不能依赖其中的文件在运行环境中存在。
- `var/` 只存本机运行数据，禁止提交。

## 2. 根目录文件

| 文件 | 存放的代码或功能 |
|---|---|
| `.gitignore` | Git 排除规则，必须持续排除 `docs/`、`var/`、密钥、依赖和构建缓存。 |
| `README.md` | 面向开发者的项目简介、启动方式和顶层目录说明；不承载详细架构规范。 |
| `AGENTS.md` | coding agent 自动发现入口，仅负责要求读取本文件。 |
| `AGENT.md` | 本职责地图；所有代码落位规则的人工维护源。 |
| `package.json` | monorepo 的前端与契约统一命令，以及 JSON Schema TypeScript 生成/验证工具；不放业务运行依赖。 |
| `pnpm-workspace.yaml` | pnpm workspace 范围和依赖构建白名单，管理 Web 与生成的 TypeScript 契约包。 |
| `pnpm-lock.yaml` | JavaScript/TypeScript 工具和 workspace 依赖的可复现版本锁，修改依赖时同步更新。 |
| `.env.example` | 可公开的环境变量名称与安全默认值；不得放真实密钥。 |

## 3. 前端 `apps/web`

| 路径 | 存放的代码或功能 |
|---|---|
| `apps/web/package.json` | Web 应用依赖与 `dev/build/lint/typecheck/test` 命令。 |
| `apps/web/app/` | Next.js App Router 页面、布局、错误边界和同源 API 代理；页面只做组装。 |
| `apps/web/components/` | 可跨业务复用的 UI 组件和设计系统封装，不发请求、不承载领域流程。 |
| `apps/web/features/intake/` | 项目创建、导入校对、创意候选与 Brief 确认界面。 |
| `apps/web/features/workbench/` | CaseFile 对象树、对象详情、时间线、搜索与命令面板。 |
| `apps/web/features/quality/` | 质量中心、ValidationIssue 定位、修复建议和 PatchCandidate 审阅。 |
| `apps/web/features/simulation/` | 玩家模拟器配置、运行进度和报告。 |
| `apps/web/features/compiler/` | Compiler 配置、构建进度、产物清单和下载入口。 |
| `apps/web/features/tasks/` | 长任务中心、重试、取消、恢复和用量展示。 |
| `apps/web/lib/` | 无 UI 的浏览器侧基础设施，如生成 Client 的配置、Query Client、SSE、格式化器。 |
| `apps/web/store/` | Zustand 本地编辑器状态；服务端数据仍由 TanStack Query 管理。 |
| `apps/web/tests/` | Vitest、React Testing Library 和前端测试工具。 |
| `apps/web/e2e/` | Playwright 用户闭环测试。 |

前端新增代码时：路由壳放 `app/`，业务交互放对应 `features/<domain>/`，通用视觉组件放 `components/`，无 UI 基础设施放 `lib/`。不得为了方便把完整业务实现堆进 `page.tsx`。

## 4. 后端 `backend`

| 路径 | 存放的代码或功能 |
|---|---|
| `backend/pyproject.toml` | Python 版本、生产/开发依赖、Schema 代码生成依赖、pytest 和静态检查配置。 |
| `backend/uv.lock` | 后端及 Python 契约生成工具的可复现依赖锁；由 uv 维护，不手工编辑。 |
| `backend/alembic.ini` | Alembic 连接与日志配置，不保存生产凭证。 |
| `backend/migrations/env.py` | Alembic 运行入口，加载 `DATABASE_URL` 和完整 SQLAlchemy metadata。 |
| `backend/migrations/script.py.mako` | Alembic 新迁移模板，生成带类型标注的 revision 文件。 |
| `backend/migrations/versions/` | 正式 PostgreSQL 迁移；文件必须使用 `VyyyyMMddHHmmss__lower_snake_case.py`，保持单调单链。 |
| `backend/migrations/versions/V20260715145030__create_identity_workspace_foundation.py` | 创建 User、Workspace、Membership、WorkspaceSetting，并写入本地默认身份。 |
| `backend/migrations/versions/V20260715145031__create_project_casefile_foundation.py` | 创建 Project、CaseFile、Draft、Object、Ref、Operation 及工作态约束。 |
| `backend/migrations/versions/V20260715145032__create_version_approval_audit_foundation.py` | 创建 Snapshot、Approval、Canon、Audit，并实现批准与不可变触发器。 |
| `backend/migrations/README.md` | 逐表中文职责、关系、约束、写入方和生命周期说明；表职责变化必须同步更新。 |
| `backend/src/casefile/api/` | FastAPI app、routes、鉴权/工作区依赖、SSE、HTTP 错误映射。一个领域一组 route 模块。 |
| `backend/src/casefile/worker/` | PostgreSQL Job Table 的领取、lease、checkpoint、重试与任务执行器。 |
| `backend/src/casefile/core/` | 实体、值对象、状态机、应用服务、Repository/Provider 端口和领域错误。保持纯 Python。 |
| `backend/src/casefile/data_postgres/base.py` | SQLAlchemy Base、约束命名规范和 UUID/时间戳公共 mixin。 |
| `backend/src/casefile/data_postgres/__init__.py` | PostgreSQL adapter 包入口，公开共享 SQLAlchemy Base。 |
| `backend/src/casefile/data_postgres/models/identity.py` | User、Workspace、Membership 和 WorkspaceSetting ORM。 |
| `backend/src/casefile/data_postgres/models/casefile.py` | Project、CaseFile、Draft、Object、Ref 和 Operation ORM。 |
| `backend/src/casefile/data_postgres/models/versioning.py` | Snapshot、Approval、Canon 和 Audit ORM。 |
| `backend/src/casefile/data_postgres/models/__init__.py` | 汇总导入全部 ORM，供 Alembic metadata 发现。 |
| `backend/src/casefile/data_postgres/` | 后续 Repository、查询、事务和 Unit of Work 实现；ORM 不得泄漏到 core。 |
| `backend/src/casefile/agent_runtime/` | 模型 Provider Adapter、结构化输出、审批后恢复、token/费用记录。 |
| `backend/src/casefile/reasoning/` | Claim/Hypothesis/ReasoningPath 图分析，NetworkX 与搜索策略实现。 |
| `backend/src/casefile/validation/` | 确定性 Schema/引用/时间/知识/发布规则、图规则和规则注册表。 |
| `backend/src/casefile/simulation/` | 玩家 Profile、路径运行、诊断、可信度提示和报告组装。 |
| `backend/src/casefile/compiler/` | Target Adapter、IR、Renderer、Target Validator、Source Map、Manifest 与发布包。 |
| `backend/src/casefile/importers/` | text、Markdown、DOCX、文本型 PDF 的解析与来源片段映射。 |
| `backend/src/casefile/object_store/` | 对象存储端口的本地实现及未来 S3 adapter；业务层不直接读写路径。 |
| `backend/tests/unit/` | 纯领域、规则、状态机和算法测试，不连接外部服务。 |
| `backend/tests/integration/` | PostgreSQL、API、Worker、迁移、失败注入和 adapter 测试。 |
| `backend/tests/contract/test_editing_contracts.py` | JSON Schema 元校验、Fixture 分类、ObjectRef 前缀、Pydantic 往返和开发准入覆盖检查。 |
| `backend/tests/unit/test_foundation_metadata.py` | 静态检查 14 表集合、Workspace 作用域、复合外键和关键约束。 |
| `backend/tests/integration/test_foundation_migrations.py` | 在专用 PostgreSQL 验证升降级、默认 seed、跨租户门禁、Canon 批准和不可变约束。 |

跨领域调用必须经过 `core` 中公开的应用端口，不能跨模块直接查询数据表。API route 只完成协议转换、依赖解析和调用应用服务，不写业务规则。

## 5. 契约 `contracts`

| 路径 | 存放的代码或功能 |
|---|---|
| `contracts/schemas/editing-contracts.schema.json` | 编辑闭环代码生成聚合入口，只引用 CaseFile、ValidationIssue 和 PatchCandidate 三个正式契约。 |
| `contracts/schemas/casefile/common.schema.json` | ActorRef、ObjectRef、公共元数据、命名空间扩展、Rule ID 和 JSON Pointer 定义。 |
| `contracts/schemas/casefile/objects.schema.json` | ResolutionSpec、Entity、Event、Claim 等 12 类 CaseFile 核心对象定义。 |
| `contracts/schemas/casefile/casefile.schema.json` | CaseFile 1.0 根结构、项目画像、版本和核心对象集合，是 CaseFile 内容契约入口。 |
| `contracts/schemas/casefile/patch-candidate.schema.json` | PatchCandidate 与稳定对象寻址的 PatchOperation 契约；字段路径不得依赖数组下标。 |
| `contracts/schemas/task/` | TaskRun、checkpoint、进度、预算与用量契约。 |
| `contracts/schemas/compiler/` | Compile Profile、Target IR、Source Map、Manifest、发布产物契约。 |
| `contracts/schemas/validation/validation-issue.schema.json` | ValidationIssue 1.0 标准输出、严重度、定位引用、修复提示和 Validator 版本契约。 |
| `contracts/schemas/reasoning/` | ReasoningRun、候选路径、图节点和评测结果契约。 |
| `contracts/schemas/api/` | 仅属于 HTTP 边界、无法归入领域对象的请求/响应 envelope。 |
| `contracts/generated/python/` | 可安装的 `casefile-contracts` Pydantic v2 包；整体由生成脚本覆盖，禁止手改。 |
| `contracts/generated/typescript/` | pnpm workspace 包 `@casefile/contracts`，提供生成的 TS 声明；整体禁止手改。 |
| `contracts/tests/contract-roundtrip.ts` | Ajv 2020-12、生成 TS 类型、合法/结构错误 Fixture 的 TypeScript 往返门禁。 |
| `contracts/tests/tsconfig.json` | 仅用于契约消费者的严格 TypeScript 编译配置。 |
| `contracts/openapi.json` | 从 FastAPI 导出并经一致性检查的 OpenAPI 3.1 快照。 |

契约变更顺序：修改 `schemas/` -> 重新生成 Python/TypeScript -> 导出 OpenAPI -> 跑跨语言 fixture 测试。破坏性变更必须提升 Schema 版本并提供迁移策略。

## 6. 测试资料、基础设施与脚本

| 路径 | 存放的代码或功能 |
|---|---|
| `fixtures/casefiles/` | 3 个合法 CaseFile 开发准入样例，合计覆盖全部核心对象、至少 4 类推理任务和 3 类结论模式。 |
| `fixtures/editing/` | 合法 ValidationIssue、PatchCandidate 和基于稳定对象引用的三方冲突场景。 |
| `fixtures/invalid/schema/` | 由合法 CaseFile 加最小 mutation 构成的结构错误样例，必须被 JSON Schema 和 Ajv 拒绝。 |
| `fixtures/invalid/semantic/` | 10 条核心不变量的未来 Validator 场景；其中 CaseFile mutation 必须保持 Schema 合法。 |
| `fixtures/imports/` | 可安全提交的导入来源、AI 推断/用户确认状态及预期 CaseFile 映射。 |
| `fixtures/compiler/` | 编译输入、Target IR、Source Map、Manifest 与期望产物。 |
| `infra/compose/docker-compose.yml` | 仅绑定 localhost 的 PostgreSQL 17 本地开发服务与持久卷配置。 |
| `scripts/dev.ps1` | Windows 本地启动 PostgreSQL、Web、API、Worker 的统一入口。 |
| `scripts/generate-contracts.ps1` | 从聚合 Schema 确定性生成可安装 Python 包和 TypeScript workspace 包。 |
| `scripts/check-contracts.ps1` | 重生成并比较漂移，执行 Python/TypeScript 双运行时 Schema 与 Fixture 门禁。 |
| `scripts/check.ps1` | 依次执行契约漂移与往返、迁移命名、Python、Alembic 和单元测试门禁。 |
| `scripts/check-migration-names.ps1` | 检查迁移真实时间戳、命名、版本唯一性和单链关系。 |
| `scripts/new-migration.ps1` | 按 Asia/Shanghai 时间生成符合规范的 Alembic revision。 |
| `var/casefile-data/` | 本机对象存储和临时运行数据；目录整体被 Git 忽略。 |

## 7. 当前占位文件规则

骨架目录内的 `.gitkeep` 只为让 Git 追踪尚未实现的边界，不承载功能。第一次加入真实文件时删除同目录 `.gitkeep`，并把真实文件及职责补充到本文相应表格；不要让同一功能同时散落在多个目录。

## 8. Git 提交规范

- 所有 Git 提交必须使用英文 Conventional Commit 前缀和中文说明，标题格式为 `<type>: <中文简要说明>`。
- 前缀按变更性质使用 `feat`、`fix`、`refactor`、`docs`、`test`、`build`、`chore`；除前缀、文件名、代码标识符、协议名和专有名词外，标题与正文说明必须使用中文。
- 一次提交只表达一个完整目的；数据库迁移、模型、契约、测试和职责文档需要同步变更时，应作为同一个完整目的提交。
- 提交意图和影响必须清楚说明，不得只写英文前缀或模糊描述。
