# CaseFile

> 面向个人创作者的互动推理内容结构化设计与验证平台。

![CaseFile](apps/web/public/casefile-brand.png)

开发者评测入口：[Benchmark 总目录](benchmarks/README.md)——按能力方向查找评测目标、运行入口、套件和证据管理规则。

CaseFile 把零散的创作想法、文本材料和推理设定，整理成一份可编辑、可验证、可追溯的数字卷宗，再由作者确认叙事方案，生成和打磨小说正文。它面向依赖人物、事件、线索、假设与结论关系的内容创作，当前主要交付路径是桌面 Web 上的推理设计与小说创作。

CaseFile 的核心是让人、模型与确定性规则共同工作：模型负责规划、分析与正文候选，服务端负责契约、引用、并发、预算与证据绑定，作者拥有采用、修改与确认的最终决定权。小说链路的连续性和文学判断交给 LLM，产品完成与严格语义通过分别记录。

## 项目亮点

- **可计算的推理设定**：人物、事件、证据、命题、假设和推理路径使用稳定对象引用连接，支持定位问题和分析修改影响。
- **事实与讲述分离**：事件实际发生时间保存在 Draft，读者获知信息的顺序保存在独立 Exposure Plan；调整悬念和倒叙不需要改写事实时间。
- **作者可控的 AI 修改**：模型先提出候选，服务端绑定对象、模拟变更、验证影响，再由作者审批；结构化修改支持 Apply、Undo 和 Redo。
- **可恢复的长任务**：API、独立 Worker 与 PostgreSQL 队列配合，持久化任务尝试、进度事件和组件产物；恢复时核对冻结身份与预算。
- **从设定到正文的分阶段编译**：冻结卷宗经过叙事投影、小说规划、场景计划和正文生成，阶段产物可追溯；正文还可进入独立的章节编辑与版本审阅。
- **能力与证据分开管理**：Schema、Prompt、任务输入和评测套件都有版本或哈希；确定性正确性、模型能力、运行时交付和正式资格分别验证。

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
    K --> L[逐场正文生成与审核]
    L --> M[小说工作区：编辑 / 改写 / 润色 / 保存版本]
```

一条典型创作路径如下：

1. 从一句想法、已有原稿或导入文档开始建案。
2. 通过关键追问补齐创作约束，形成结构化 Brief 候选。
3. 人工审阅并冻结 Brief，启动可恢复的后台生成任务。
4. 比较多个不可变工作稿候选，显式采用其中一份作为 Current Draft。
5. 在工作台核对对象、时间、关系、证据、假设、推理路径和空间信息。
6. 运行确定性验证，审阅 Agent 的分析、Finding 与修改建议，再决定是否应用。
7. 固定 Snapshot 与披露顺序，审阅小说推荐和规划，显式确认后生成场景计划与正文。
8. 在小说工作区编辑章节，与 AI 讨论、改写或润色，审阅差异后采纳，并保存可比较、可恢复的版本。

## 核心能力

| 能力 | 当前实现 |
|---|---|
| 建案与素材整理 | 支持创意候选、自由文本建案、SourceRecord 来源保留，以及 `.txt`、`.md`、`.docx`、文本型 PDF 的反向解析与逐项确认。 |
| Brief 设计 | 通过关键追问、结构化候选、人工审阅和原子确认形成不可变 BriefVersion；原始材料与最终约束保留来源关系。 |
| 候选生成 | Brief-to-Draft 以持久化 TaskRun、Attempt、Event 和独立 Worker 执行；失败可恢复，进度通过 HTTP/SSE 返回。 |
| 多工作稿 | 同一 Brief 可生成多个不可变候选；只有作者显式采用后才会创建或切换 Current Draft，旧稿及其历史保持隔离。 |
| 分析师工作台 | 提供对象目录、时间线、关系图、推理分析、地图、证据对比、验证问题和编译中心等统一视图。 |
| 卷宗 Agent | 持久化 Thread 与消息上下文，支持问答、分析、审计和通用修改建议；Patch 必须经过服务端复验与人工批准，支持 Apply、Undo 与 Redo。 |
| 多步目标与上下文 | 有界 Goal Controller 编排能力，GoalSession 支持跨任务延续、显式调整目标与等待审批；上下文按策略裁剪、压缩并绑定消息发送时的对象选择。 |
| 验证与推理 | 结合 JSON Schema、稳定引用、时间约束、知识状态、证据—假设矩阵、结论义务和结构锁，产出可定位的 Finding。 |
| 版本与审计 | Current Draft 使用 revision 乐观并发；Source、候选、Operation、Snapshot、Canon、Exposure revision 和 Audit 保留不可变谱系。 |
| 叙事编译 | 将冻结的 CaseFile、Exposure 与 Novel Profile 投影为 NarrativeIR，生成经过结构和语义门禁的 NovelPlanIR，并由权威状态引擎规范化为 ScenePlanIR v2。 |
| 小说正文生成 | 提供快速初稿与完整精修；逐场执行 Writer、LLM 审核与有界修订，保存正文、评审依据和运行记录，分别统计产品接受与严格语义通过。 |
| 小说编辑与版本 | 支持章节编辑、选段协作、整章改写/润色、差异审阅、显式采纳、保存版本及历史比较和恢复。 |
| Provider 与隐私 | 支持 OpenAI、DeepSeek 与零成本 FakeProvider；用户密钥按 Provider 独立使用 AES-256-GCM 加密，明文不会返回前端。 |

## 当前产品边界

- CaseFile 当前是本地运行的个人创作工具，一个 Project 只有一个所有者。
- 前端当前只面向桌面 Web，不维护移动端专属布局或交互。
- 暂不提供 Workspace、Membership、邀请、团队角色、评论或共享项目。
- 前端固定使用本地开发身份 `X-CaseFile-User-Id: 1`；这不是生产认证机制，API 默认只应绑定回环地址。
- Agent 只能提出候选、Finding 与待审 Patch，不能绕过验证或自动 Apply。
- Current Draft 的事实时间与 Exposure Plan 的披露顺序是两条独立版本链。
- 编译中心目前只开放小说工作区；剧本、互动脚本、作者卷宗和测试材料是禁用的后续入口，不能视为已实现的导出或发布能力。
- 小说正文生成与编辑已有实现，但快速初稿不等于已审核正文，产品接受也不等于严格语义通过；任务成功不能单独作为完整小说交付证明。
- 多角色 Judge Council 保留为独立组件与评测能力；生产正文按冻结运行策略执行 Fidelity 审核、连续性建议和编辑决策，不能假定每次生成都运行完整委员会。

## 各大功能如何实现

### 1. 建案、创意与文档反向解析

建案支持三种输入：作者提供想法或原文、由模型生成创意候选、上传已有文档。创意可以选择、收藏、淘汰和重新生成；文档支持 `.txt`、`.md`、`.docx` 与文本型 PDF，通过提取器获得正文，再交给后台解析任务生成待审条目。文档原文、来源块、字段来源和逐项确认结果保留，确认后的内容可汇入 Brief。

实现入口：[建案前端](apps/web/features/intake/)、[创意服务](backend/src/casefile/application/idea_service.py)、[文档提取器](backend/src/casefile/importers/)、[反向解析服务](backend/src/casefile/application/reverse_parse_service.py)。持久化使用 SourceRecord、ImportedDocument、ParseItem 等对象；扫描 PDF 的 OCR 不属于上述文本提取能力。

### 2. 关键追问与 Brief 冻结

Brief 是创作要求和约束的结构化输入。系统基于当前材料提出追问，保存作者回答，再生成可编辑、可选择的 Brief 候选。确认时检查必要问题、候选新鲜度、Intake 与 Brief revision，并在事务内形成不可变 BriefVersion，保留 SourceRecord 来源关系。

实现入口：[BriefIntakeService](backend/src/casefile/application/brief_intake_service.py)、[Intake Worker](backend/src/casefile/worker/handlers/intake.py)。材料变化后的旧回答或旧候选不能绕过版本检查直接成为新输入。

### 3. 结构化工作稿生成与多稿采用

冻结 Brief 后，作者选择策略并启动生成任务。组件化管线组装 Context Pack，由规划、时间、故事世界、证据逻辑和结论治理等部件产生中间结果，再确定性编译为 CaseFile 文档并执行质量门禁；具体部件由任务冻结的运行版本决定，失败诊断和有界修复分别留痕。

生成成功只保存不可变候选。作者显式采用时再次检查来源 Brief、原 Draft 身份和 revision：初始空稿可原位物化，已有内容时建立新 Draft 并原子切换 Current Draft，旧稿历史保持隔离。可以积累多个候选，当前前端每次提交一个选定策略，并非同时批量生成全部策略。

实现入口：[版本化管线注册](backend/src/casefile/agent_runtime/brief_to_draft_runtime.py)、[共享执行图](backend/src/casefile/agent_runtime/brief_to_draft_v8/workflow.py)、[候选服务](backend/src/casefile/application/draft_candidates.py)、[生成 Worker](backend/src/casefile/worker/handlers/brief_generation.py)。目录中的历史版本用于兼容和回放，不能仅按最大版本号推断活动配置。

### 4. 结构化卷宗与分析师工作台

所有视图围绕同一份 Current Draft：对象目录查看人物、地点、事件与信息；时间线呈现事件及时间关系；关系图组织人物与对象联系；推理图展示命题、假设和推理路径；证据对比呈现支持、反驳与缺口；地图承载地点和空间信息。前端把 API 文档映射为各视图读模型，并以稳定对象 ID 联动选择、详情与修改。

后端采用轻量对象注册表、专用内容表和引用关系，完整快照另存为不可变 JSON。编辑携带 Draft ID 和 revision，避免切换稿件后误写；时间调整可先预演再确认。

实现入口：[工作台](apps/web/features/analyst-workbench/analyst-workbench.tsx)、[工作台读模型](backend/src/casefile/application/workbench_read_model.py)、[编辑服务](backend/src/casefile/application/v1_editing.py)、[时间线服务](backend/src/casefile/application/timeline.py)。图与空间展示使用 React Flow、Dagre、D3 时间工具和 Leaflet。

### 5. 确定性验证、推理闭包与修改预演

验证从 JSON Schema 和稳定引用开始，再检查可确定的时间、知识状态、推理依赖、结论义务及结构锁。Finding 携带规则、严重程度、证据和影响对象，作者可以定位和审阅问题。

修改先规范化为 MutationSet，构建逻辑图并计算影响范围，再在副本上按顺序模拟，比较修改前后的 Finding 和推理闭包；必要时进入有界 Closure Repair。真正应用前重新验证当前身份和状态。小说中的 POV、重复叙述和认知变化等文学判断使用 LLM 分级意见，不把这些通用卷宗验证机制直接扩展成正文创作裁决。

实现入口：[契约验证](backend/src/casefile/contracts/)、[VerificationEngine](backend/src/casefile/domain/verification_engine.py)、[Logical Mutation](backend/src/casefile/domain/logical_mutation/)、[验证服务](backend/src/casefile/application/verification_service.py)。

### 6. 卷宗 Agent、检索上下文与安全修改

每条消息绑定发送时的项目、工作稿版本、当前视图和选中对象。Context Engine 按版本化策略组装卷宗骨架、焦点对象、历史窗口与压缩记忆，并在预算内裁剪。对话按意图进入问答、分析、审计或修改流程；检索依据和工具结果形成可验证记录，公开预览与正式消息分开保存。

修改路径由模型提出计划，服务端 Binder 解析目标、字段与引用，再经过模拟和验证形成待审 PatchSet。作者批准后才由统一编辑服务事务应用；撤销、重做继续受并发和历史约束。模型回复中声称“已完成”不构成实际写入事实。

实现入口：[Chat Worker](backend/src/casefile/worker/handlers/chat.py)、[上下文引擎](backend/src/casefile/agent_runtime/context/engine.py)、[消息上下文快照](backend/src/casefile/application/agent_message_context.py)、[修改绑定](backend/src/casefile/application/agent_mutation.py)、[Patch 生命周期](backend/src/casefile/application/workflow/patches.py)。

### 7. 多步目标与跨消息协作

Goal Controller 将目标约束为明确的义务与依赖，只调度注册的分析、审计和修改建议能力，以 Observation 判定完成，并限制步骤和调用预算。GoalSession 在多个顺序 TaskRun 之间保存目标状态，支持等待澄清、等待 Patch 审批，以及显式 `steer`、`follow_up`、`replace`。

运行中的调整进入 FIFO 投递队列，在安全点处理；旧执行切片收敛、控制消息消费、新目标修订与后继任务排队保持原子性。Draft 漂移时进入 stale，不自动沿用旧修改候选。该机制不授予 Agent 自动 Apply 权限。

实现入口：[有界执行循环](backend/src/casefile/agent_runtime/goal/execution.py)、[GoalSession 应用服务](backend/src/casefile/application/workflow/goal_session.py)、[状态矩阵](backend/src/casefile/application/goal_session_state.py)。

### 8. 快照、Canon、披露顺序与审计

Draft 是可编辑当前态；Operation 记录每次修改；Snapshot 固定完整文档；Canon 记录作者确认的设定版本。快照使用 RFC 8785 Canonical JSON 与 SHA-256 固定内容身份，版本与审计记录采用追加式历史，并由数据库约束和触发器保护归属及不可变性。

Exposure Plan 独立管理披露条目的顺序、对象引用和规划义务，拥有自己的 revision。例如案发发生在第一天，读者到第三章才知道，调整第三章的揭示位置不会改写案发时间或推进 Draft revision。

实现入口：[Snapshot](backend/src/casefile/application/snapshot.py)、[Exposure Plan](backend/src/casefile/application/exposure_plan.py)、[数据模型](backend/src/casefile/data_postgres/models/)、[迁移与数据职责](backend/migrations/README.md)。当前迁移 head 应以迁移链和运行时版本常量核对，不依赖历史文档中的固定编号。

### 9. 小说推荐、方案确认与叙事编译

作者逐步确认小说推荐与规划，编译任务绑定精确 Snapshot、Exposure revision、Novel Profile、已批准方案及模型运行身份。NarrativeIR 把卷宗投影成叙事输入，NovelPlanIR 承载章节和场景规划，ScenePlanIR v2 把语义填充与状态回放组合成逐场执行上下文。

LLM 负责主要规划和叙事选择，服务端维护输入、引用、依赖、哈希和状态投影。各阶段产物不可变，恢复只复用身份与依赖匹配的结果；正文阶段失败不能改写此前的规划产物。

实现入口：[编译应用服务](backend/src/casefile/application/compiler/)、[编译领域层](backend/src/casefile/domain/narrative_compiler/)、[编译 Worker](backend/src/casefile/worker/executors/compiler.py)、[方案预览](apps/web/features/novel-workspace/novel-plan-preview.tsx)。

### 10. 逐场正文生成、审核与有界修订

正文根据已批准场景计划生成，绑定当前场景清单、角色与信息状态、上一场已接受正文。快速初稿直接形成带未审核标记的候选；完整精修使用 LLM 连续性建议、Writer、语义 Judge 和编辑决策，有界选择保留、修订、重写或停止。质量 Critic 与 Polisher 是进一步优化步骤，不能覆盖原始语义判断。

服务端保存证据引用、原始评审、正文哈希、实际调用和尝试级产物。产品模式可以按 LLM 编辑决策保留非致命问题，分别记录 `product_accepted` 与 `strict_semantic_pass`；快速交付也不会伪造严格通过。显式续跑检查被冻结的输入、运行版本和已接受前缀，累计预算不会因重启清零。

实现入口：[正文执行器](backend/src/casefile/worker/executors/prose_shadow.py)、[正文产物存储](backend/src/casefile/worker/executors/prose_store.py)、[编辑决策](backend/src/casefile/agent_runtime/prose_revision.py)、[正文清单](backend/src/casefile/domain/narrative_compiler/prose_checklist.py)。

### 11. 小说编辑、创作搭档与版本管理

小说工作区管理独立稿件与章节，服务端保存权威版本，浏览器保留未同步副本。协作支持讨论、改写和润色；选段请求绑定精确文本范围，服务端检查原文唯一匹配，前端在 DOM UTF-16 与 Unicode code point 位置之间转换，防止文本定位漂移。

整章改写和润色可以指定“必须保留”“允许调整”，经后台编辑审核形成完整章节候选。前端计算段落与字符差异，作者审阅后整章采纳；普通修改按修改组审阅。显式保存版本与并发 revision 分离，历史详情按需加载，可比较前一保存版或恢复历史稿。“新对话”只改变后续上下文起点，不删除正文、版本或旧对话。

实现入口：[小说工作区](apps/web/features/novel-workspace/)、[编辑服务](backend/src/casefile/application/novel_editor.py)、[协作请求冻结](backend/src/casefile/application/novel_collaboration.py)、[章节编辑流程](backend/src/casefile/worker/handlers/novel_prose.py)、[小说 API](backend/src/casefile/api/novel_editor.py)。

### 12. Provider、任务运行与评测基础设施

Provider 适配层支持 OpenAI Responses、DeepSeek Chat Completions 和测试用 FakeProvider；凭据使用 AES-256-GCM 加密并绑定用户与 Provider，前端只读取掩码。Prompt Registry 与带哈希的版本包管理模型指令，TaskRun 冻结模型、Prompt、工具和输入身份。

Worker 使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 领取任务，配合 lease、Attempt 和提交前身份检查，拒绝过期执行器写回。TaskEvent 持久化进度，前端通过 HTTP/SSE 读取和重放；组件产物按输入及上游哈希验证复用，具体恢复能力由任务类型决定。

评测覆盖工作稿生成、验证、修复、Agent 路由/检索/结果/上下文/目标，以及小说规划、场景、Judge、Writer、Rewrite 和质量。Fake 集成、真实模型试验和正式资格的指标分别记录，不将存在测试入口表述为已通过。

实现入口：[Provider 适配](backend/src/casefile/agent_runtime/provider_adapters/)、[凭据加密](backend/src/casefile/agent_runtime/credentials.py)、[Worker 队列](backend/src/casefile/worker/queue.py)、[Prompt Registry](backend/src/casefile/agent_runtime/prompts/README.md)、[Benchmark 总目录](benchmarks/README.md)。

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
├─ backend/
│  ├─ src/casefile/
│  │  ├─ api/            # HTTP 协议与依赖注入
│  │  ├─ application/    # 用例、事务、身份与并发门禁
│  │  ├─ domain/         # 验证、逻辑修改、叙事编译纯领域规则
│  │  ├─ agent_runtime/  # 模型流程、上下文、Prompt 与 Provider 端口
│  │  ├─ worker/         # 任务队列、执行、恢复与终态提交
│  │  ├─ data_postgres/  # ORM、仓储、数据库版本检查
│  │  ├─ importers/      # 文档提取、来源分块与解析
│  │  ├─ contracts/      # Schema 运行时镜像与验证
│  │  └─ benchmark/      # 能力评测与资格执行器
│  ├─ migrations/        # 正式 Alembic 单链迁移
│  └─ tests/             # unit / contract / integration
├─ contracts/             # JSON Schema、OpenAPI 与生成的跨语言契约、契约测试
├─ benchmarks/            # 按能力组织的评测导航
├─ fixtures/              # 合法/非法样例与各类 Benchmark 数据集
├─ infra/compose/         # 本地 PostgreSQL 与隔离测试库
├─ scripts/               # 初始化、启动、检查、契约生成与验收入口
└─ docs/                  # 本机产品与架构资料，默认被 Git 忽略
```

前端产品代码按 `intake`、`case-session`、`analyst-workbench`、`novel-workspace`、`settings` 划分；`apps/web/tests/` 与 `apps/web/e2e/` 分别放组件/行为测试和浏览器流程测试。`contracts/generated/` 与 `backend/src/casefile_contracts/` 是生成物，禁止手改。`var/`、`backend/var/`、`tmp/` 保存本机日志、运行产物和评测证据，不提交；依赖目录、缓存和历史冻结资产不属于新的产品能力。

## 本地开发

### 环境要求

- Windows + PowerShell
- Node.js `>= 20.9` 与 `pnpm`
- Python `3.12` 与 `uv`（当前后端声明 `>=3.12,<3.13`）
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

重复运行会复用当前仓库的已有服务，保留正在执行的任务；端口被其他程序占用时会报错，不会强制结束该程序。同一仓库只允许一个启动流程运行。复用服务不会重新加载 API/Worker 代码，修改后需要在任务结束后手动停止对应服务再启动。已有服务运行时会跳过依赖同步；冷启动不带 `-SkipDependencySync` 时使用 pnpm 和 uv 同步依赖。

Docker 检查单次最多 8 秒，启动等待每轮最多约 130 秒。检测到当前 Docker 会话的 `dockerInference` 或 `engine.sock` 无法访问错误时，脚本停止 Docker Desktop，将 `%LOCALAPPDATA%\Docker\run` 和 `%LOCALAPPDATA%\docker-secrets-engine` 同时改名为带时间戳的 `*-recovery-*` 备份，然后重试一次。数据库、镜像和卷不会被清理；未知目录内容或其他 Docker 故障会保留现场并明确报错。这是对已知故障的自动恢复，不能保证 Docker 自身永不报错。

完整启动记录位于 `var/dev/startup-*.log`；数据库准备最多等待 240 秒。可用下面的命令验证超时、恢复备份和端口保护逻辑（使用临时目录，不停止真实 Docker）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-startup.ps1
```

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

统一检查中的完整确定性矩阵由 pytest 执行一次：Closure Repair、Novel Plan regression/safety、General Mutation、Chat Outcome calibration、Context Boundary 和 Context Tier 均复用对应测试；独立的 Chat Goal 门禁仍由脚本运行。架构扫描由 `test_backend_architecture.py` 执行，不再在脚本中预先重复扫描。

日常定位可在 `backend` 下运行 `.venv/Scripts/python.exe -m pytest tests/unit tests/contract --durations=20`，或指定受影响的测试文件。独立 Benchmark CLI 保留用于显式生成报告、诊断和正式资格评测；`check.ps1` 不再额外生成 General Mutation、Context Boundary、Context Tier 的固定路径 JSON 报告。正式资格评测的完整矩阵、次数和门槛不变。

每次 `check.ps1` 都打印 static / evaluation / tests 阶段耗时，并在 `var/checks/<UTC时间-随机后缀>/` 保存独立报告：

- `summary.json`：整轮与各阶段耗时、类别汇总、通过/失败状态及 pytest 报告路径；中途失败也保存已经执行的阶段。
- `pytest.json`：每条测试的 setup/call/teardown 耗时与结果，按耗时降序排列，并分别汇总三个阶段。完整数据库检查的两次 rollout 验收另存 `pytest-phase3.json` 和 `pytest-phase4.json`。
- 控制台自动显示最慢 20 个 pytest 阶段。pytest 内执行的完整 benchmark 仍计入 tests，具体耗时通过其文件名和 nodeid 定位；evaluation 类别只计独立的 Goal 门禁，不重复运行矩阵来计时。

单独运行 pytest 时可添加 `--timing-report var/check-timing.json --durations=20`；未指定时不生成时序报告。阶段总和不包含 Python 启动、收集和其他框架开销，因此不等于整轮墙钟时间；计时文件只记录 nodeid、状态与耗时，不保存测试内容或凭据。比较性能时应使用同一测试范围，并注意后台负载和冷/热缓存差异。

数据库业务测试统一复用 `workflow_database`：保留迁移后的 schema，清理独立提交的数据；只有迁移专项测试执行升降级。不要给共用同一测试库的测试直接启用并行执行。

Fixture 的活动/兼容/历史用途和维护规则见 [fixtures/README.md](fixtures/README.md)。

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

- [Benchmark 总目录](benchmarks/README.md)：按能力寻找套件、执行入口和证据边界。
- [Fixture 生命周期](fixtures/README.md)：区分当前、兼容与历史冻结输入。
- [数据库迁移职责](backend/migrations/README.md)：数据关系、约束和迁移历史。
- [Prompt Registry](backend/src/casefile/agent_runtime/prompts/README.md)：版本包、激活与回放规则。

以下资料位于默认忽略的本机 `docs/`，新克隆仓库可能不包含这些文件；运行时不依赖它们：

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
