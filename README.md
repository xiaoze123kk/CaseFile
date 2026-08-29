# CaseFile

> 面向个人创作者的互动推理内容结构化设计与验证平台。

![CaseFile](apps/web/public/casefile-brand.png)

CaseFile 把零散的创作想法、文本材料和推理设定，整理成一份可编辑、可验证、可追溯的数字卷宗。它面向推理小说、互动叙事、剧本杀及其他依赖人物、事件、线索、假设与结论关系的内容创作，让作者能够从建案开始，逐步完成 Brief、结构化工作稿、逻辑审阅、叙事规划和场景执行计划。

项目不是一个“输入提示词后直接生成全文”的写作器。CaseFile 的核心是让人、模型与确定性规则共同工作：模型负责提出候选和建议，服务端负责契约、引用、并发与一致性门禁，作者始终拥有采用、修改与确认的最终决定权。

## 产品工作流

```mermaid
flowchart LR
    A[创作想法 / 文本材料] --> B[关键追问与 Brief]
    B --> C[冻结 BriefVersion]
    C --> D[TaskRun + Worker]
    D --> E[多个 Draft 候选]
    E -->|作者显式采用| F[Current Draft]
    F --> G[分析师工作台]
    G --> H[验证 / Agent 建议 / 人工修订]
    H --> F
    F --> I[Snapshot + Exposure Plan]
    I --> J[Narrative Compiler]
    J --> K[NarrativeIR / NovelPlanIR / ScenePlanIR]
```

一条典型创作路径如下：

1. 从一句想法、已有原稿或导入文档开始建案。
2. 通过关键追问补齐创作约束，形成结构化 Brief 候选。
3. 人工审阅并冻结 Brief，启动可恢复的后台生成任务。
4. 比较多个不可变工作稿候选，显式采用其中一份作为 Current Draft。
5. 在工作台核对对象、时间、关系、证据、假设、推理路径和空间信息。
6. 运行确定性验证，审阅 Agent 的分析、Finding 与修改建议，再决定是否应用。
7. 固定 Snapshot 与披露顺序，编译出可复验的 NarrativeIR、小说场景规划和场景执行计划。

## 核心能力

| 能力 | 当前实现 |
|---|---|
| 建案与素材整理 | 支持创意候选、自由文本建案、SourceRecord 来源保留，以及 `.txt`、`.md`、`.docx`、文本型 PDF 的反向解析与逐项确认。 |
| Brief 设计 | 通过关键追问、结构化候选、人工审阅和原子确认形成不可变 BriefVersion；原始材料与最终约束保留来源关系。 |
| 候选生成 | Brief-to-Draft 以持久化 TaskRun、Attempt、Event 和独立 Worker 执行；失败可恢复，进度通过 HTTP/SSE 返回。 |
| 多工作稿 | 同一 Brief 可生成多个不可变候选；只有作者显式采用后才会创建或切换 Current Draft，旧稿及其历史保持隔离。 |
| 分析师工作台 | 提供对象目录、时间线、关系图、推理分析、地图、证据对比、验证问题、导出预览和编译中心等统一视图。 |
| 卷宗 Agent | 持久化 Thread 与消息上下文，支持问答、分析、审计和通用修改建议；Patch 必须经过服务端复验与人工批准，支持 Apply、Undo 与 Redo。 |
| 验证与推理 | 结合 JSON Schema、稳定引用、时间约束、知识状态、证据—假设矩阵、结论义务和结构锁，产出可定位的 Finding。 |
| 版本与审计 | Current Draft 使用 revision 乐观并发；Source、候选、Operation、Snapshot、Canon、Exposure revision 和 Audit 保留不可变谱系。 |
| 叙事编译 | 将冻结的 CaseFile、Exposure 与 Novel Profile 投影为 NarrativeIR，生成经过结构和语义门禁的 NovelPlanIR，并由权威状态引擎规范化为 ScenePlanIR v2。 |
| Provider 与隐私 | 支持 OpenAI、DeepSeek 与零成本 FakeProvider；用户密钥按 Provider 独立使用 AES-256-GCM 加密，明文不会返回前端。 |

## 当前产品边界

- CaseFile 当前是本地运行的个人创作工具，一个 Project 只有一个所有者。
- 暂不提供 Workspace、Membership、邀请、团队角色、评论或共享项目。
- 前端固定使用本地开发身份 `X-CaseFile-User-Id: 1`；这不是生产认证机制，API 默认只应绑定回环地址。
- Agent 只能提出候选、Finding 与待审 Patch，不能绕过验证或自动 Apply。
- Current Draft 的事实时间与 Exposure Plan 的披露顺序是两条独立版本链。
- Narrative Compiler 当前聚焦冻结输入、NarrativeIR、NovelPlanIR 与 ScenePlanIR；最终正文渲染、目标平台适配和发布包仍不属于现阶段交付。

## 界面入口

本地启动后访问 `http://127.0.0.1:3000`：

- `/`：建案中心。从创作输入、关键追问、Brief 审阅一路进入候选生成与采用。
- `/workbench`：分析师工作台。围绕 Current Draft 进行结构浏览、编辑、验证、Agent 协作与叙事编译。

API 默认位于 `http://127.0.0.1:8000`：

- `/docs`：FastAPI OpenAPI 交互文档。
- `/health/live`：进程存活检查。
- `/health/ready`：数据库版本和应用就绪检查。
- `/api/v1`：CaseFile 业务 API。

## 技术架构

CaseFile 是前后端同仓、运行时分离的模块化单体：

| 层 | 技术与职责 |
|---|---|
| Web | Next.js 16、React 19、TypeScript、TanStack Query、React Flow、Dagre、Leaflet、D3。 |
| API | Python 3.12、FastAPI、Pydantic；负责协议转换、身份/并发门禁和应用服务调用。 |
| Worker | 基于 PostgreSQL 队列领取 TaskRun，提供 lease、Attempt 恢复、取消、事件持久化和 Provider 调度。 |
| Domain | 纯 Python 的验证、Logical Mutation、Closure Repair 与 Narrative Compiler，不依赖 FastAPI、SQLAlchemy 或具体 Provider。 |
| Data | PostgreSQL 18、SQLAlchemy 2、Alembic、psycopg；规范化当前态与不可变版本链并存。 |
| Contracts | JSON Schema 2020-12 是跨语言事实源，生成 Python/Pydantic 与 TypeScript 契约包。 |
| Quality | Ruff、mypy、pytest、ESLint、TypeScript、Vitest、Playwright，以及确定性 Benchmark 门禁。 |

关键运行关系：

```text
Next.js Web ──HTTP/SSE──> FastAPI ──transaction──> PostgreSQL
                               │                       ▲
                               └── enqueue TaskRun ────┤
                                                       │
Provider <── adapter / frozen input ── Worker <────────┘
```

## 仓库结构

```text
CaseFile/
├─ apps/web/              # Next.js 产品前端
├─ backend/               # FastAPI、应用层、领域层、Worker、迁移与测试
├─ contracts/             # JSON Schema、OpenAPI 与生成的跨语言契约
├─ fixtures/              # 合法/非法样例与各类 Benchmark 数据集
├─ infra/compose/         # 本地 PostgreSQL 与隔离测试库
├─ scripts/               # 初始化、启动、检查、契约生成与验收入口
└─ docs/                  # 产品、架构、数据与代码职责说明
```

## 本地开发

### 环境要求

- Windows + PowerShell
- Node.js `>= 20.9` 与 `pnpm`
- Python `3.12` 与 `uv`
- Docker Desktop（包含 Docker Compose）

### 1. 安装依赖

```powershell
pnpm install --frozen-lockfile
uv sync --project backend --extra dev
```

### 2. 初始化本地环境

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -SeedDevUser
```

初始化脚本会：

- 在缺少 `.env` 时复制 `.env.example`；
- 生成并保存本地 `CASEFILE_MASTER_KEY`；
- 启动开发库 `127.0.0.1:55432` 和可丢弃测试库 `127.0.0.1:55433`；
- 等待 PostgreSQL 健康并迁移开发库到唯一 Alembic head；
- 创建前端当前使用的本地开发用户。

`.env.example` 中的账号只适用于绑定回环地址的本地容器，不得用于部署环境。已有加密 Provider 密钥后，不要更换 `CASEFILE_MASTER_KEY`。

### 3. 启动完整应用

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -SkipDependencySync
```

脚本会启动或连接 Docker Desktop，准备数据库，并在后台启动 Web、API 与独立 Worker。日志写入 `var/dev/`。默认端口可通过 `-WebPort` 和 `-ApiPort` 调整。

### 分别启动

只启动 Web：

```powershell
pnpm dev:web
```

启动 API：

```powershell
.\backend\.venv\Scripts\python.exe -m uvicorn casefile.api.app:app --host 127.0.0.1 --port 8000
```

启动 Worker：

```powershell
.\backend\.venv\Scripts\python.exe -m casefile.worker
```

## Provider 设置

启动应用后，在“设置 → 模型与 API”中分别配置 OpenAI 或 DeepSeek：

- OpenAI 使用 Responses API。
- DeepSeek 使用官方 Chat Completions 接口，并支持内置或兼容的自定义模型 ID。
- 每个 TaskRun 会冻结 Provider、模型、Prompt 与输入身份，Worker 按冻结绑定执行。
- `CASEFILE_PROVIDER_MODE=live` 使用任务选择的真实 Provider；`fake` 仅用于零成本本地集成测试。
- 已保存密钥只返回掩码；仍被进行中任务引用的凭据不能删除。

不要把真实 API Key 写入 README、Fixture、Benchmark 报告或 Git 跟踪文件。

## 数据与一致性

- `contracts/schemas/` 是跨语言契约的唯一人工维护事实源，生成目录禁止手改。
- CaseFile 对象在 Draft 内使用稳定字符串 `object_id`；数据库内部关系使用 `BIGINT IDENTITY`。
- 写操作同时校验 Current Draft 身份和 revision，防止在切换工作稿后误写。
- 成功修改只推进一次 Draft revision，并追加不可变 `draft_operations` 记录。
- Snapshot 对完整 CaseFile 执行契约验证，并以 RFC 8785 Canonical JSON 的 SHA-256 固定内容身份。
- Brief-to-Draft 完成只产生候选，不会自动覆盖 Current Draft。
- Exposure Plan 的 revision 不推进 Draft revision，也不修改事件事实时间。

## 检查与测试

前端完整检查：

```powershell
pnpm check:web
```

不运行 PostgreSQL 集成测试的仓库检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1 -SkipPostgres
```

完整检查必须显式指向数据库名以 `_test` 结尾的可丢弃 PostgreSQL：

```powershell
$env:CASEFILE_TEST_DATABASE_URL = "postgresql+psycopg://casefile:casefile_test_local_only@127.0.0.1:55433/casefile_test"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

浏览器黄金路径使用真实 Web、API、Worker 和隔离测试库，但固定使用 FakeProvider：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-a-path-e2e.ps1 -WebPort 13000 -InstallBrowser
```

首次运行后可省略 `-InstallBrowser`。

## 契约与迁移

修改跨语言 Schema 后：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/generate-contracts.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-contracts.ps1
```

新增数据库迁移必须通过统一入口生成：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/new-migration.ps1 -Description add_example_table
```

迁移文件使用 `VyyyyMMddHHmmss__lower_snake_case.py`，时间按 Asia/Shanghai 生成，并保持唯一 Alembic head 和单链关系。

## 进一步阅读

- [架构边界与模块规则](docs/architecture-boundaries.md)
- [后端代码职责地图](docs/backend-code-map.md)
- [前端代码职责地图](docs/frontend-code-map.md)
- [跨语言契约与 Fixture](docs/contracts-code-map.md)
- [数据库迁移规范](docs/migration-standards.md)
- [数据一致性规范](docs/data-consistency.md)
- [代码质量与 Git 提交规范](docs/code-quality-git.md)

## 开发原则

1. 先保护作者意图，再扩展模型能力。
2. 模型输出始终是候选；验证、授权与写入边界由服务端掌握。
3. 当前态可以编辑，来源、任务事件、操作、快照和审计历史保持可追溯。
4. 契约从 Schema 生成，数据库变化同步更新 ORM、迁移、测试与职责文档。
5. 保持个人产品边界，不为尚未存在的团队协作提前建模。
