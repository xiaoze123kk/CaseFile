# 真实 CaseFile 工作台与 Agent 协作开发日志

## 基本信息

- 日期：2026-07-29
- 分支：`feature/database-frontend-develop`
- 启动基线：`804f965`
- 实施入口：真实 `/workbench`
- 明确边界：冻结的 `/demo` 原型只作视觉和交互参考，不导入真实页面，不混用 Prototype Store 或 Fixture 状态。

## 已确认的关键决策

1. 工作台采用稳定三栏：左侧对象树、中间 Agent/事实时间线双模式、右侧常驻对象编辑器。
2. 左侧按对象类型折叠展开，子项直接显示具体对象；不再为对象列表单独占用中间区域。
3. 中间默认显示 Agent 协作，切换“事实时间线”时隐藏 Agent 视图但保留线程、输入、滚动和运行状态。
4. Agent 默认读取整个最新 CaseFile，不做对象级上下文裁剪；支持多个独立线程，交互接近 Codex，线程栏可折叠。
5. Agent 以多轮对话为主，固定任务只作为快捷入口；默认展示简洁回答，执行记录可展开，不展示模型内部思维文本。
6. Agent 建议按对象和字段形成结构化建议卡，支持逐项审阅和整批审阅。
7. 整批应用前必须弹出确认窗口；确认后立即写入，并提供整批撤销。
8. Validator 发现语义问题时保留修改并提示，可撤销本批修改或让 Agent 修复；基础结构不合法的修改不得写入。
9. Agent 运行期间允许人工编辑。任务结果若基于旧 Draft，则标记过期、禁止应用并允许重新生成；首版不做自动合并或自动 rebase。
10. 时间线首版只展示事实时间，采用纵向时间轴；不实现叙事顺序、横向比例尺、缩放或拖拽改时间。
11. 时间线和 Agent 双向联动：事件可带入 Agent 讨论，Agent 中的事件引用可跳回时间线定位。
12. 右侧按对象类型展示用户可理解的结构化业务字段，关联字段使用可搜索对象选择器；统一按整个对象取消或保存。
13. 真实界面不展示 JSON、Schema、内部对象 ID、Revision、Provider/Validator 所有权或其他后台维护字段。
14. 视觉采用“卷宗编辑部工作台”：连续档案记录、批注和建议单，不使用通用聊天气泡或泛化 SaaS 卡片。

## 本次纳入范围

- 真实 CaseFile 工作台的对象树、事实时间线、常驻类型化编辑器和 Agent 双模式布局。
- Agent 多线程、消息、执行状态、结构化建议、批量确认、撤销、Validator 回执和过期检测所需的真实持久化/API。
- CaseFile 各对象类型的用户字段展示与本轮明确需要的编辑闭环。
- 数据库迁移、ORM、API、前端状态、测试、数据库说明和职责地图同步。
- 桌面端主布局、合理的窄屏降级、键盘焦点与基础可访问性。

## 明确排除范围

- 修改 `/demo` 冻结原型或把 Prototype Store/Fixture 引入真实模式。
- 叙事时间线、比例时间轴、拖拽排期、时间缩放。
- Agent 自动合并过期建议或自动 rebase。
- 暴露模型内部思维过程。
- 团队协作、成员权限、评论、共享线程。
- Simulation、Compiler 或 Target Adapter 后端实现。

## 当前开发范围与风险

- 工作区启动时已有与本需求无关的未提交前端和 `docs/` 变更；必须保留，禁止覆盖或纳入本轮验证结论。
- `apps/web/lib/api-client.ts` 已有未提交修改，本轮如必须扩展需先理解并以最小补丁合并。
- 审计确认 TaskRun/TaskEvent 适合继续承载可恢复执行与简洁执行记录，但不能单独承载多线程历史和补丁生命周期；本轮新增四张正式协作表，禁止以前端内存状态替代。
- 开发基线数据库为 38 张正式业务表；本轮新增 `agent_threads`、`agent_messages`、`agent_patch_sets`、`agent_patch_operations` 后应为 42 张，必须同步 ORM、迁移、数据库 README、元数据测试和本文。
- 现有单对象编辑服务只支持实体、地点和事件，且一个操作推进一次 revision；本轮批阅不能循环复用该接口，必须在一个事务中应用整组修改并只推进一次 revision。
- 右侧编辑器覆盖 11 类集合时，前后端可写字段必须一致；关联字段需按对象引用重建，不能只在界面展示无法保存的假输入框。

## 实施进展

- 已完成持久化协作模型和 TaskRun 的 `casefile_chat` 关联；正式表由 38 张增加为 42 张，并迁移到 `20260729161235`。
- 已建立 Provider 中立的对话结果契约：回答、对象引用和字段级修改建议；每条建议只描述一个对象相对 JSON Pointer 和一个 JSON 值，不直接写入草稿。
- Fake/OpenAI/DeepSeek 已共用该对话契约；`casefile_chat` 已进入根 Task Schema、Python/TypeScript 生成契约和前端 Task 类型。
- 后端已完成多线程、消息、执行状态、PatchSet/Operation、逐项或整批采用、整批拒绝、原子应用/撤销、过期结果和非阻塞 Validator 告警的纵向闭环。
- 每次 Agent 发送均冻结完整最新 CaseFile、历史消息和当前指令；响应基于旧 Draft 时仍持久化，但建议不可应用。
- 真实前端已完成对象树、事实时间线、类型化对象编辑器和独立工作台 API 适配；不导入 `/demo` 或 Prototype Store。
- 工作台已按最终协作契约接入多线程、嵌套 Task、对象引用、结构化 PatchSet、逐项/整批采用、整批拒绝、确认应用、撤销、过期结果和 Validator 非阻塞警告。
- Agent 运行状态按线程隔离，刷新后可从持久消息恢复 queued/running Task 的 SSE，切换线程不会串写执行记录。
- 对象编辑器只从 11 类业务字段白名单生成变更，明确排除 `id`、`revision` 和 `knowledge_states` 等系统维护字段；结构锁不向用户暴露后台 JSON Pointer。
- 工作期间分支 HEAD 被并行工作推进到 `55127a2`；本轮保留并绕开已有 `docs/`、`.obsidian`、设计稿删除和其他未提交产物，未执行 reset、clean 或 restore。

## 未完成任务

- [x] 完成真实前端、Agent 后端、迁移与测试边界审计。
- [x] 确定持久化模型和 API 契约。
- [x] 实现并迁移后端纵向闭环。
- [x] 使用 `frontend-design` 实现真实工作台界面。
- [x] 补充测试并完成真实浏览器验收。
- [x] 更新本日志的实际完成范围、验证结果和剩余任务。

后续事项：

- `knowledge_states` 当前继续只读；现有空状态往返语义未收口前，不在本轮开放编辑。
- 本轮只实现已有对象编辑，不新增对象创建、删除或排序能力。
- 真实 DeepSeek 调用在当前代理 TUN 环境下进入模型与修复流程，但候选三次未通过严格 JSON 解析；系统正确保持 Draft 不变。代理环境恢复后仍需再做一次真实模型成功路径复验，本轮浏览器功能闭环使用本地确定性 Provider 验收。

## 验证计划

- 后端：静态检查、mypy、相关 unit/integration/API 测试、迁移命名与真实 `_test` 数据库升降级。
- 前端：lint、TypeScript、相关 Vitest、production build。
- 浏览器：真实 `/workbench` 对象树、Agent/时间线切换、对象编辑、线程恢复、建议审阅、批量确认/撤销、过期建议和 Validator 提示。
- 范围：`git diff --check`，逐文件确认未覆盖启动时已有修改。

## 前端阶段验证结果

- `pnpm lint`：通过。
- `pnpm typecheck`：通过。
- `pnpm test`：6 个测试文件、52 项测试全部通过；新增事实时间排序、对象树选择、id-only Agent 引用跳转、时间线联动和最终 Agent API 契约适配测试。
- `pnpm build`：Next.js 生产构建通过，13 个静态页面完成生成。
- `git diff --check -- apps/web/features/workflow apps/web/tests AGENT.md development_logs`：通过。
- Playwright 真实 `/workbench` 冒烟：三栏布局、左侧对象展开、Agent/事实时间线标签切换、时间线选中对象、带事件进入 Agent、右侧类型化字段和未保存切换确认均通过；页面未出现 JSON、Schema、内部对象 ID 或 Revision。

## 最终验证结果

- `scripts/check.ps1`：Ruff、mypy、迁移链、真实 PostgreSQL 集成/API 测试全部通过，共 55 项测试通过。
- `pnpm check:contracts`：生成契约无漂移，Python 契约 8 项和 TypeScript 往返测试通过。
- `pnpm --filter @casefile/web check`：lint、TypeScript、6 个 Vitest 文件 56 项测试和 Next.js production build 全部通过，13 个静态页面生成。
- 开发数据库已升级到 `20260729161235`，`/health/ready` 返回 ready，42 张正式表完成 bootstrap。
- 迁移后真实浏览器闭环通过：UI 建案和冻结简报、本地确定性生成 Draft、多线程 Agent 消息持久化、完整卷宗响应、结构化建议、批量确认、即时写入、右侧编辑器同步和整批撤销均已验证。
- Agent 引用使用仅含对象 ID 的真实后端响应仍可解析并定位对象；事件引用的时间线跳转有独立回归测试与 Playwright 冒烟证据。
- `git diff --check` 通过；未把既有无关工作区变更纳入本轮完成结论。
