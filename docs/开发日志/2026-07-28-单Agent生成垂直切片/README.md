# 2026-07-28 单 Agent 生成垂直切片

## 目标

打通从用户结构化建案到可编辑 CaseFile v1 工作台的第一条真实闭环：用户配置模型与 API Key、填写并确认 Brief、创建生成任务、查看安全执行轨迹、由单 Agent 生成并验证结构化 Draft、原子写入 Snapshot，最后由用户确认进入工作台。

## 范围

- 以根目录 `contracts/schemas/` 作为 CaseFile v1、Brief、Task 与编辑契约的唯一人工维护源，并生成 Python/TypeScript/runtime 镜像。
- PostgreSQL 业务表从 28 张扩展到 37 张，新增用户 Provider 设置、Brief/BriefVersion、Relationship、StructureLock、v1 契约引用和 TaskRun/Attempt/Event。
- 用户级 API Key 使用 AES-256-GCM 密文存储；接口只返回后四位和验证状态，不支持读取明文。
- 实现单 Agent、Agents SDK 工具循环、版本化 Prompt/Agent/Toolset、结构化输出、有限结构修复和分层 Validator 基础。
- 使用 PostgreSQL `FOR UPDATE SKIP LOCKED`、lease、Attempt 和 TaskEvent 作为任务队列及恢复机制，不引入独立 MQ。
- 通过 SSE 展示阶段、工具摘要、Validator 与用量，不展示或持久化隐藏思维链。
- 真实前端实现五字段建案、Brief 冻结、任务进度、完成门禁、12 类对象展示，以及 Entity、Location、Event 的有限字段编辑。
- 旧原型完整迁移到 `/demo/*`，真实模式与演示模式状态和导航完全隔离。
- Benchmark 覆盖结构有效率、修复次数、延迟、工具调用有效率、工具执行成功率与结果采纳率；Fake Provider 用于零费用联调和可重复测试。

## 关键决策

- 当前只使用单 Agent，不实现 Agent handoff；Agent 内部通过 SDK 工具循环完成规划、生成和校验。
- 默认真实模型为 `gpt-5.6-sol`，模型与 API Key 由左下角设置弹窗按用户配置；Prompt、Agent、Toolset 和 Provider 配置版本都固化到 TaskRun。
- 暂不实现登录认证，开发闭环使用用户 ID `1`；后续认证计划采用 JWT，并由独立工作负责。
- Brief 草稿可修改，只有用户确认后的 BriefVersion 才能作为生成输入；确认版本和任务事件只追加。
- Draft 必须为空才能执行全量生成；一次成功生成只追加一个 `agent_generate_from_brief` Operation 和一个 Snapshot。
- 候选 JSON、规范化表投影回的 Snapshot JSON 与 RFC 8785 哈希必须一致，失败时整笔事务回滚。
- Validator 分层：确定性 Schema/引用/语义规则先执行，Agent 通过工具消费校验结果；更强的独立语义判断层后续扩展。
- 不把数据库表机械做成单一 JSON 文档；字段尽量对应 v1 契约，身份、归属、状态、租约和引用使用关系结构，完整 JSON 仅用于不可变 Snapshot/Canon。
- 数据库结构不是永久冻结；已共享迁移不得重写，后续调整必须通过新迁移并同步 ORM、契约映射、测试和职责文档。

## 遗留事项

- 登录、JWT、用户隔离的真实身份注入和密钥轮换运营流程尚未实现。
- 独立语义 Validator Agent、人工复核队列和更细的分层判定证据仍需设计。
- TaskRun 已具备取消、重试和 lease 数据字段，但用户级取消/重试交互与运维监控仍不完整。
- 当前真实工作台只允许 Entity、Location、Event 的有限字段编辑；其余对象仍为只读展示。
- Benchmark 只有最小 `brief_to_draft` Fixture，尚不足以评估题材多样性、长上下文、成本上限和模型版本回归。
- Simulation、Compiler、Asset/Import、Validator Report、预算账本和正式发布流程仍未实现。

## 下一阶段建议

1. 接入 JWT 认证与真实当前用户上下文，完成用户级 Provider 设置、Project、SSE 和 Worker 的端到端隔离测试。
2. 把 Validator 拆成确定性门禁、语义判断 Agent 和人工复核三层，并定义可审计的 ValidationIssue/PatchCandidate 落库策略。
3. 完成任务取消、显式重试、失败恢复、用量/成本上限与运维可观测性，再验证多 Worker 并发。
4. 扩充 Benchmark 数据集和模型回归基线，覆盖多题材、边界输入、工具失败、结构修复、结果采纳率和成本。
5. 按对象优先级逐步开放其余 v1 类型的有限编辑，保持契约 round-trip、revision 冲突和只读边界测试。

---

# 2026-07-29 Brief → Draft 定义重构与真实闭环

## 本轮目标

把上一阶段“结构化建案直接生成 Draft”的纵向切片重构为目标无关、来源可追溯且必须经作者确认的真实闭环：

```text
原稿 SourceRecord
  → 独立 Agent 润色候选
  → 作者编辑/采用
  → Core Brief
  → Agent 原子拆解候选
  → 作者逐条确认
  → 不可变 BriefVersion
  → brief_to_draft
  → CaseFile Draft Snapshot
```

本轮不实现 Compiler、Target Design 或下游成品格式；Brief 与 CaseFile Core 只描述创作意图、推理命题、结论约束和来源事实。

## 决策一：Brief 从“玩家目标”改为目标无关的推理输入

### 背景

旧 Brief 以 `player_goal`、项目画像和面向互动体验的字段组织输入，默认把“推理底座”提前绑定到玩家、时长、人数和成品玩法。CaseFile 后续需要编译为多种成品，这些字段属于下游 Target，而不是 Core 的稳定事实。

### 比较过的方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| 保留 `player_goal`，增加若干可选通用字段 | 改动最小，旧 Prompt 可继续使用 | 定义中心仍是玩家目标；非游戏成品需要绕过或伪造字段 |
| 新增 `target_type`，按类型维护多套 Brief | 能显式表达不同成品 | 在 Compiler 尚未设计时提前固化 Target 枚举；Core 与 Target 持续耦合 |
| 只保留创作意图、推理命题、结论模式、作者底牌和创作边界 | Core 稳定、可被不同 Compiler 消费；作者约束清晰 | 需要同步修改契约、Prompt、Fixture、数据库迁移和前后端 |

### 最终选择

选择第三种方案。Core Brief 由 `source_record_ids`、`creative_intent`、`reasoning_proposition`、`resolution_mode`、`author_answer`、`author_anchors`、`boundary_text` 和 `creative_constraints` 组成。CaseFile v1 机器契约同时移除 `project_profile`、顶层 `phases`、`target_question` 和 `fairness_requirements`，改用 `reasoning_question` 与事件时点知识状态。

原因是这些字段描述“作者希望建立怎样的推理事实与结论边界”，不假设最终消费者是玩家、读者、审查者或其他系统。

## 决策二：原稿不可覆盖，润色只产生独立候选

### 背景

作者原稿既是创作证据，也是 Agent 是否忠实的审阅基准。若润色直接覆盖文本，作者无法可靠比较语义变化，任务失败或误改也无法恢复。

### 比较过的方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| Agent 直接更新 Brief 文本 | 实现最短、界面简单 | 原稿丢失；无法审计；违背作者控制权 |
| 仅在前端内存保存候选和 diff | 无需新增表 | 刷新即丢失；无法跨设备/任务恢复；不是真实闭环 |
| 新增不可变 `source_records` 来源链 | 原稿、Agent 提案和作者修订都可恢复；可校验任务输入哈希 | 增加一张表、迁移、归属约束和不可变触发器 |

### 最终选择

选择不可变 SourceRecord。`human_original` 保存原稿，`agent_polish_proposal` 必须指向输入 SourceRecord 和生成它的 TaskRun，作者编辑并采用时写入新的 `human_revision`。Brief 只引用作者明确采用的来源 ID，任何 Agent 候选都不能静默替换原稿。

## 决策三：底牌和边界先拆解为候选，再由作者确认

### 背景

作者底牌是硬约束，但自然语言可能包含多条事实、模糊条件或内部冲突。完全照抄会降低可执行性，Agent 自动改写又会篡改作者意图。

### 比较过的方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| 把整段原文直接作为一个硬约束 | 不会改写作者文本 | 无法逐条校验、定位冲突或稳定引用 |
| Agent 拆解后直接写入 Brief | 自动化程度高 | Agent 误解会直接成为硬约束，缺少人工责任边界 |
| Agent 只持久化原子候选，作者逐条编辑、定级和采用 | 可补强、可审阅、可追溯；不静默篡改 | 多一次确认交互，需要处理候选过期 |

### 最终选择

选择第三种方案。Brief 保存后自动创建 `brief_anchor_extract` TaskRun；Worker 只把原子底牌、创作约束、建议强度和警告写入 `result_jsonb`。前端用 Brief revision 与输入哈希拒绝过期候选，作者保存采用结果后，服务端才允许确认 BriefVersion。

服务端门禁为：`author_anchored` 必须同时有作者底牌原文和至少一条原子底牌；非空创作边界必须有至少一条原子创作约束。Agent 候选本身不具备硬约束效力。

## 决策四：三类 Agent 工作统一使用 TaskRun

### 背景

上一阶段已经具备 PostgreSQL TaskRun、Attempt、Event、lease 与 SSE 回放。如果润色和拆解另建前端“假任务”或新队列表，恢复、错误、用量和审计会出现两套语义。

### 比较过的方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| 润色/拆解直接同步调用 Provider | API 简单 | 长请求不可恢复；断线后结果丢失；无法复用 SSE |
| 前端自行维护临时任务状态 | UI 开发快 | 与服务端事实漂移；刷新丢失；不是真实任务 |
| 扩展现有 TaskRun 支持三类任务 | 统一 lease、Attempt、Event、用量、恢复和失败语义 | TaskRun 需要冻结不同形态的输入，并按类型约束字段 |

### 最终选择

选择扩展现有 TaskRun，任务类型为 `brief_polish`、`brief_anchor_extract` 和 `brief_to_draft`。每个任务都保存 `input_hash` 与不可变 `input_jsonb`，按类型保存 SourceRecord 或 Brief revision；`result_jsonb` 保存可恢复结构化结果。`GET tasks/latest`、事件列表和 `Last-Event-ID` SSE 共同恢复刷新或断线后的状态。

## 决策五：直接修正预发布 v1，并用前向迁移承接旧数据

### 背景

旧 CaseFile `1.0` 已把玩家、阶段和公平性写入 Core，但当前仍是预发布开发基线。保留错误定义会让后续 Compiler 永久承担兼容债务；直接改已有迁移又会破坏已经存在的本地数据库。

### 比较过的方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| 原地改旧迁移 | 新装数据库最干净 | 破坏共享迁移历史，现有数据库无法安全升级 |
| 新增 CaseFile 1.1/2.0 并长期双读 | 版本边界最显式 | 当前尚未发布却立刻背负双契约、双 Prompt 和双投影 |
| 保持版本号 `1.0`，修正事实源并新增前向迁移 | 及时纠正预发布定义；迁移链仍单向可升级 | 必须转换旧 Brief、Task 输入和不可变历史 JSON/哈希 |

### 最终选择

选择第三种方案。根目录 Schema 是唯一人工事实源，Python、TypeScript 和 runtime 镜像全部重新生成；新 Alembic 迁移承接 SourceRecord、TaskRun 扩展和旧数据转换，不改写上一段迁移。生成器的漂移检查同时覆盖发布目录、后端 Pydantic 包和 runtime Schema 镜像。

数据库中为旧纵向切片保留的物理兼容列不再进入 v1 投影或 Prompt；Core 机器契约、Snapshot 和新任务输入只使用目标无关名称。

## 决策六：DeepSeek 使用独立 Provider 适配并冻结到任务

### 背景

用户已经配置 DeepSeek 凭据。虽然 DeepSeek 提供 OpenAI 兼容接口，但模型枚举、JSON 输出、错误分类和后续能力演进仍需要明确的 Provider 边界。

### 比较过的方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| 把 DeepSeek 当成 OpenAI 的自定义 base URL | 复用代码最多 | Provider 审计不清晰；设置和模型能力容易串用 |
| 引入另一套第三方 SDK | 可能提供更多便捷封装 | 增加依赖，行为与官方 HTTP 契约之间多一层 |
| 独立 DeepSeek Provider，使用官方 Chat Completions/JSON 模式 | 凭据、模型、错误和 TaskRun 路由清晰；依赖最少 | 需要维护小型适配层与结构化解析 |

### 最终选择

选择独立 DeepSeek Provider。用户级 OpenAI/DeepSeek 设置分别加密保存，TaskRun 冻结 Provider、模型和配置版本；FakeProvider 只用于可重复测试。真实验证只允许读取解密后的内存值发起最小请求，不输出、不写日志、不把密钥放入错误详情。

## 决策七：只保留一套真实前端流程

### 背景

旧代码中存在 Prototype/真实两套建案和 Brief 组件，测试还引用已删除组件，容易造成“演示能跑、真实 API 不通”的假闭环。

### 比较过的方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| 继续维护 demo 与 real 两套页面 | 原型试验自由 | 状态、修复和测试重复；产品证据不可信 |
| 恢复已删除的旧 BriefWorkspace | 能快速修复 import | 重新引入旧 `player_goal` 定义和第二套交互 |
| 新建唯一 BriefReviewWorkspace，旧 demo 路由只跳转 | 状态事实唯一；真实浏览器证据对应真实 API | 需要迁移测试和导航引用 |

### 最终选择

选择第三种方案。`/brief` 使用唯一 `BriefReviewWorkspace`；`/demo/brief` 只重定向。前端从 API 恢复 SourceRecord、最新 TaskRun、事件 backlog 和 Draft，不用 Prototype Store 伪造任务成功。

## 契约生成与漂移门禁

本轮发现生成脚本存在两个可复现问题：开发依赖未声明，以及生成失败后会让 TypeScript workspace 包暂时缺失，导致 `pnpm` 无法再次启动生成命令。最终锁定 `datamodel-code-generator==0.71.0`，使用严格引用和 JSON 生成报告，断言所有模块实际落盘；生成漂移检查扩展到：

- `contracts/generated/python`
- `contracts/generated/typescript`
- `backend/src/casefile_contracts`
- `backend/src/casefile/contracts/schemas/v1`

这保证根 Schema 发生变化时，任一运行时镜像手改或漏生成都会失败。

## 本轮明确没有实现

- 不实现 Compiler、Target Adapter、Target Design、IR、Renderer、Source Map 或任何成品导出。
- 不把玩家人数、时长、阶段、公平性、玩法目标重新放入 Core Brief 或 CaseFile v1。
- 不实现 Simulation、独立语义 Validator Agent、自动 Patch 合并或发布流程。
- 不实现登录/JWT、团队协作、评论、审批任务或远端对象存储。
- 不让 Agent 自动采用润色、底牌或创作边界候选。
- 不承诺物理删除所有旧兼容列；本轮保证它们不再进入机器契约、Prompt、新 Snapshot 和新任务输入。

## 验证记录

完成状态以本轮最终命令和浏览器证据为准；任何因本机 PostgreSQL、凭据或外部服务不可用而未执行的检查，必须在最终汇报中单独列为阻塞，不能记作通过。

### 契约与代码门禁

- `pnpm check:contracts` 通过：生成物无漂移，7 项 Python 契约测试通过，TypeScript 契约编译和 round-trip 测试通过。
- `scripts/check.ps1` 通过：Alembic 7 个 revision 单一 head，Ruff 通过，mypy 检查 42 个源文件无问题，43 项后端测试全部通过；其中包含 PostgreSQL 迁移、应用服务和 API 集成测试。仅保留一条来自 Starlette TestClient/httpx 兼容层的弃用警告。
- `pnpm --filter @casefile/web check` 通过：ESLint、TypeScript、4 个测试文件共 33 项测试、Next.js 生产构建全部通过，13 个页面完成静态构建。
- `git diff --check` 通过；本轮没有提交或推送。

### 开发数据库闭环

- 开发库已升级到 `20260728171649`，共有 38 张业务表；`source_records`、扩展后的 `task_runs`、Attempt、Event、BriefVersion 和 DraftSnapshot 位于同一迁移链。
- 浏览器验证项目中，`human_original`、`agent_polish_proposal`、`human_revision` 各 1 条，三类记录的父子来源和生成任务约束均通过数据库校验，原稿没有被候选或人工修订覆盖。
- Brief draft revision 为 3，当前指向 1 个不可变 BriefVersion；确认内容包含 1 个采用来源、2 条作者底牌和 4 条创作约束，结论模式为 `author_anchored`，且不存在 `player_goal`。
- 最终 Draft 为 revision 2 / active，指向同一个 BriefVersion，并产生 snapshot 1。Snapshot 与对象投影共包含 1 个 `resolution_spec` 和 6 个 `constraint`，不含 `player_goal`、`project_profile`、`phases` 或 `fairness`。
- 每个验证 TaskRun 都有可回放的递增 TaskEvent，并以 `task.succeeded` 或 `task.failed` 终结，刷新后可由 latest Task、事件 backlog 和 SSE cursor 恢复。

### 真实浏览器证据

在本机真实 Next.js、FastAPI、PostgreSQL 和 Worker 上，通过 Playwright 依次完成：

1. 输入并保存目标无关原稿；
2. 发起真实 Agent 润色，比较只读原稿和可编辑候选；
3. 编辑并采用候选，形成独立 `human_revision`；
4. 保存 Core Brief，自动触发底牌与边界拆解；
5. 审阅 2 条底牌与 4 条创作约束，保存后确认并冻结 BriefVersion；
6. 创建 `brief_to_draft` TaskRun，通过可恢复 SSE 获得终态；
7. 进入 `/workbench`，读取 revision 2 的真实 Draft，工作台显示 11 个 Core collection，控制台为 0 error / 0 warning。

证据截图位于：

- `output/playwright/brief-confirmed-live.png`
- `output/playwright/brief-to-draft-succeeded.png`
- `output/playwright/core-draft-workbench-live.png`

浏览器验收同时发现并修复了四个只在持续轮询或刷新中暴露的问题：失败提示层遮挡重试按钮、边界折叠面板在轮询重渲染时关闭、已采用润色候选在刷新后重新弹出、工作台把 11 个集合硬编码显示为 12 个。

### 真实 DeepSeek 证据与边界

- 使用本机已加密保存的 DeepSeek 设置完成最小真实调用，过程中没有输出、记录或写入 API Key。
- 真实 `brief_polish` TaskRun 2 使用 `deepseek-v4-pro` 和 `brief-polish-v2` 成功，记录 1 次请求、416 input tokens、256 output tokens、672 total tokens。
- 真实 `brief_anchor_extract` TaskRun 3 使用 `deepseek-v4-pro` 和 `brief-anchor-extract-v2` 成功，记录 1 次请求、626 input tokens、189 output tokens、815 total tokens。
- 初次润色 TaskRun 1 暴露了非原生结构化输出路径没有向模型提供精确字段 Schema 的问题；补充完整 Pydantic JSON Schema 指令、提升 Prompt 版本并增加 Provider 单元测试后，真实润色与拆解均通过。
- 真实 `brief_to_draft` TaskRun 4 和 5 均完成规划工具调用，但在后续大模型请求中因 `APIConnectionError: Connection error` 失败，因此不能把“真实 DeepSeek 完整生成 Draft”记作通过，也没有继续无上限重试和消耗额度。
- 为只验证产品闭环剩余部分，TaskRun 6 保持真实 API、PostgreSQL、TaskRun、SSE、人工确认门禁和 Draft 原子写入，仅把验证 Worker 显式切换为仓库 FakeProvider，最终生成 snapshot 1。数据库中的 Provider/模型仍是任务创建时冻结的 DeepSeek 设置，不能据此误称该次 Draft 内容由 DeepSeek 生成。

因此本轮已经取得真实 DeepSeek 的联网、鉴权、结构化润色和原子拆解证据；受外部连接失败影响，真实 DeepSeek 的完整 `brief_to_draft` 内容生成仍是唯一未闭合的运行证据。Compiler 及所有下游 Target 能力则是本轮明确不实施的产品范围，而不是失败项。
