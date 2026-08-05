# Demo 建案中心接入真实后端

日期：2026-08-06

## 已确认决策

- `/demo/intake` 正式成为前端开发环境：五阶段（最初想法 → 关键追问 → 简报成案 → 简报审阅/冻结 → 三份候选工作稿）全部接真实后端 API 与 Agent 任务；`/demo` 分析师工作台保持 fixture 不接后端。
- demo 前端布局、视觉与五阶段编排不变；只把数据源从纯内存 fixture 换成真实 `/api/v1`，并修正“前端 Fixture / 不请求真实 API”等旧文案。
- demo 与创作模式共用同一开发用户（actor 1），密钥在创作模式或 demo 左下角设置入口配置一次两边都生效；demo 左下角仿创作模式增加设置入口，复用共享 `SettingsDialog`。
- 每次 demo 会话自动新建一个 Project（首次后端写入时惰性创建），刷新或重挂载即开启新会话，旧项目保留在开发库中，本轮不做清理。
- 候选生成映射为一次“生成三份”并发触发 3 次 `brief_to_draft` 任务，逐份轮询状态，全部完成后进入可预览/可采用状态；失败按任务级错误提示。
- 创作模式不废除，代码不动，与 demo 共享同一后端；现有后端端点已覆盖全部流程，本轮不新增后端能力、不动表结构。
- demo 的“简报版本”显示沿用会话内计数（每次确认冻结 +1）；候选新旧判定使用服务端候选的 `brief_version_no` 与本地冻结版本对齐。
- 工作台预览为本地 fixture 种子；真实候选的标题、推理问题、对象计数与约束摘录取服务端数据，种子形态沿用确定性 fixture 结构。

## 本次纳入

- 新增 `features/demo-prototype/demo-intake-api.ts`：demo 专用的真实 API 适配层（项目/来源、四类 Intake 任务、简报读写与确认、锚点拆解、三份候选生成与采用、Provider 探测），内部复用 `lib/api-client`。
- 新增 `features/demo-prototype/demo-intake-mapping.ts`：服务端 DTO → 原型模型（PrototypeBrief/问题/答案/审阅/候选）的纯函数映射与反向序列化。
- 重写 `DemoPrototypeProvider` 数据流：润色、追问、成案、审阅保存/冻结、锚点拆解、候选生成与采用改为真实任务 + 轮询；上下文接口对组件保持稳定。
- `IntakeCenterPrototype` 的润色/追问/成案/人工简报/对话修改/保存候选动作改为异步真实流程；`BriefReviewStage` 的重新拆解/保存/冻结接锚点任务与简报确认；`DraftCandidatesStage` 文案改为真实生成。
- demo 壳左下角新增设置入口，复用创作模式 `SettingsDialog`。
- 更新隔离边界测试（demo 可请求真实 API，但不得导入 workflow-store、不得使用浏览器存储）、主流程测试改为 mock API 适配层；修正“前端 Fixture”文案。

## 明确排除

- `/demo` 分析师工作台不接后端，其内容仍为确定性 fixture；接力条只表达预览稿/当前工作稿/旧简报状态。
- 不新增后端端点、表或迁移；不改创作模式前端。
- B/C/D 建案路径仍为“后续开放”；不实现会话级项目清理。
- 候选强度/取舍说明服务端未提供，demo 展示为客观状态描述（通过校验、待审阅），不伪造 Agent 推荐语。
- 不引入 SSE/审计轨迹；demo 用任务轮询获取完成状态。

## 追加修复：Provider 认证失败回退

- 现象：demo 会话任务全部以 `provider_authentication_failed` 失败（“模型服务认证失败，请检查 API Key 与模型权限。”），而创作模式任务正常。
- 根因：保存密钥时后端不校验（`credential_status` 保持 `unverified`），demo 的 Provider 探测按 openai→deepseek 顺序取第一个已配置项；本机 openai 密钥已失效，所有成功任务实际都走 deepseek。
- 修复：`demo-intake-api` 新增 `listConfiguredProviders`（过滤删除墓碑）、`isDemoAuthFailure`（按任务失败码 `provider_authentication_failed` 识别）与 `runTaskWithProviderFallback`（认证失败时自动改用下一个已配置 Provider 重试，其他错误直接抛出不回退）；六个任务型流程（润色/追问/成案/对话修改/锚点拆解/三份候选）全部接入，候选生成先以带回退的首份运行确定可用 Provider 再复用于其余两份。
- 测试：新增 `tests/demo-intake-api.test.ts` 覆盖回退成功、非认证错误不回退、无 Provider 提示与错误分类；全套 85 项测试、typecheck 与 ESLint 通过。

## 追加修复：关键追问系统性返回空问题集

- 现象：demo 追问阶段空白——`brief_intake_questions` 任务全部成功但返回 `questions: []`，即使用明显不完整的原稿（“有个案件发生了，我想做成一个推理游戏。”）也返回空；创作模式表现为静默跳过追问。
- 根因：v1 提示词措辞（“信息已经足够时返回空数组”“不因追求完整而凑足两道问题”）把模型推向空结果；且模型对契约字段名理解不稳（曾把 `question_key` 写成 `question_id`、`prompt` 写成 `question`，顶层多出 `input_hash`，触发 `candidate_validation_failed`）。
- 修复：新建 `brief_intake_questions/v2`：只要原稿未显式声明结论处理方式/推理目标/规模就必须产出 1–2 个问题，显式列出契约 JSON 字段名（`question_key`/`ordinal`/`prompt`/`impact`/`required`/`suggestions`，顶层只有 `questions`）；registry 指针与测试哈希基线同步更新。
- 验证：直接调用 DeepSeek 确认契约可解析；真实任务端到端成功落库 2 个问题（必答的结论处理方式 + 可选的推理目标）。
- demo 兜底：追问页在问题为空时显示“Agent 判断当前原稿信息已足够，无需追问”，成案按钮保持可用，不假装问过也不卡死流程。

## 追加修复：Provider 回退重试携带过期 intake revision

- 现象：回退场景下（openai 认证失败→deepseek 重试）任务创建报 `brief_intake_revision_conflict`（“Brief Intake revision is stale”）。
- 根因：`create_questions_task` 与 `create_synthesize_task` 创建任务时会把 `brief_intakes.revision` +1；`runTaskWithProviderFallback` 重试时仍携带第一次尝试前捕获的旧 revision，被 `_expected_revision` 以 409 拒绝。润色（无 revision 入参）、锚点拆解与三份候选（创建任务不推进版本）不受影响。
- 修复：追问与成案（含对话修改）的回退操作内每次尝试前重取最新 intake，用新 revision 发起任务。
- 回归测试：假后端模拟“任务创建推进 revision + openai 认证失败”，验证回退重试携带新 revision 后成功进入追问页；临时还原修复时该测试确实失败。

## 验收状态

- [x] 完成 API 适配层与映射层，五阶段全部走真实后端。
- [x] 完成左下角设置入口与共享 SettingsDialog 复用。
- [x] 更新隔离与主流程测试，mock API 适配层下全流程通过。
- [x] 更新 `AGENT.md` 演示约束与组件职责；前端 typecheck、ESLint、85 项测试与生产构建全部通过。
- [x] Provider 认证失败自动回退到另一个已配置模型服务。
- [ ] 浏览器闭环：完整建案→冻结→三份候选→采用，刷新重置为新项目（需要本地后端与 worker 运行，留待人工验收）。
