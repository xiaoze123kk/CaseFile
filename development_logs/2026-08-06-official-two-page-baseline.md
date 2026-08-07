# 2026-08-06 正式双页开发基线

## 目标

将此前隔离在 `/demo` 下的两套已确认页面提升为唯一前端开发基线，删除旧创作模式及其专属状态、组件和局部后端接口，同时保留已经产品化的数据、Agent、契约与评测内核。

## 已确认决策

- 正式路由只有 `/` 建案中心和 `/workbench` 分析师工作台。
- `/` 的五阶段建案、Brief 审阅/冻结、Agent TaskRun、三份候选和显式采用继续使用真实后端。
- `/workbench` 当前继续使用确定性 fixture；仅候选接力和采用动作连接真实会话边界。本轮不把整套工作台一次性后端化。
- 两页共用内存 `CaseSessionProvider`。刷新或 Provider 重挂载即开启空白新会话，不使用 LocalStorage，也不恢复旧浏览器指针。
- 本地开发身份统一为 actor 1。模型设置弹窗自行管理 OpenAI/DeepSeek 标签，不依赖旧 Workflow Store。
- 兼容入口使用非永久跳转：`/demo/intake`、`/brief` 到 `/`；`/demo`、`/demo/*`、`/reasoning`、`/quality` 到 `/workbench`。正式导航不再生成 `/demo` URL。
- PostgreSQL 现有 45 张表、十一段迁移和开发数据全部保留，本次不产生迁移。

## 本次纳入

### 前端

- 将建案中心和工作台移动到正式路由，建立单一 `ProductShell`、`CaseSessionProvider` 和本地 actor 常量。
- 将 `demo-prototype`、`intake-prototype`、`DemoPrototypeProvider` 与 `data-demo-*` 等实现命名改为正式产品命名；仍属开发数据的模块继续明确使用 `fixture`/`seed`。
- 保留当前建案中心与工作台的未提交视觉调整，连同品牌图、建案底纹、响应式布局和工作台交互一起迁移。
- 删除旧创作模式路由、Archive 壳、Workflow Store、旧三页组件、专属样式和测试。
- 精简全局 CSS，两个正式页面的视觉主体继续由各自 CSS Module 管理。
- 修正 780px 以下工作台网格行定位；移动区域导航固定在第 3 行，当前对象/主画布/检查器占满第 4 行剩余视口，来源抽屉在来源模式复用同一区域，避免 390px 下主画布只剩工具栏。
- 重命名并保留建案、映射、Provider 回退、设置弹窗和分析师工作台回归测试；新增正式路由、兼容跳转、跨页接力和无浏览器存储边界检查。

### 后端

- 删除无前端消费者、只覆盖 Entity/Event 子集的专用 API：`draft/entities*`、`draft/events*`、`adjacent-locations` 和 `actors`。
- 同步删除这些路由独占的请求 DTO、Entity/Event 命令、应用服务分支与 Repository helper。
- 保留 Project 生命周期、SourceRecord、Brief Intake、Brief、候选、通用 v1 对象 Patch、Snapshot、TaskEvent/SSE 和 Agent API。
- 保留规范化 Entity/Event 等物理表及其 ORM，因为它们仍是 CaseFile v1 投影、采用、Patch 与历史数据的组成部分。
- 增加已删除路由返回 404 的集成回归断言。

### 保留的产品内核

- 六类独立且不可变的 Git 版本化 System Prompt、registry/Manifest/哈希校验和历史版本加载。
- OpenAI/DeepSeek Provider、加密密钥、TaskRun/Attempt/Event、Worker、重试、轮询、SSE 与恢复能力。
- SourceRecord、Brief Intake 问题/候选谱系、Brief 审阅/冻结、三稿生成和显式采用。
- CaseFile v1 契约、ObjectRef、Validator、规范化持久化、Snapshot、Canon 与 Audit。
- Agent 多线程对话、PatchSet 逐项审阅、批量应用/撤销和过期门禁。
- 已实现的 `brief_to_draft` Benchmark/Eval 运行器、Fixture 与测试。

## 明确排除

- 不新增提示词管理后台 UI；当前提示词管理继续以 Git-backed 仓库为唯一事实源。
- 不新增 LocalStorage、长期记忆或跨刷新会话恢复。
- 不删除数据库表，不重写迁移，不清理开发数据。
- 不在本轮实现 Compiler、Simulation、Target Adapter 或导出后端。
- 不改写历史 development logs。

## 后续移交

- 按工作台区域逐步用真实 CaseFile v1、Agent Thread/PatchSet、任务事件和通用对象 Patch 替换 fixture；每接入一块都要同步删除相应 seed 行为和更新边界测试。
- 工作台真实化前，任何 fixture 交互都不得被文案或 API 命名描述为已持久化后端能力。
- 继续保持 `Eval Suite → Context Pack → 确定性检索/工具 → 有限记忆` 的 Agent 优化顺序；不把模型推断自动晋升为 CaseFile 事实。
