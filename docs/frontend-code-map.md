# 前端代码职责地图

## 分析师工作台信息架构重构

- `workbench-navigation.tsx`、`workbench-navigation.module.css`：工作台子树的固定桌面导航。总览、五个分析视图、待处理问题与编译作品始终可访问；对象档案可在任意视图展开。导航事件回交宿主，复用未保存保护，不持有领域数据。
- `workbench-overview.tsx`、`workbench-overview.module.css`：当前工作稿总览，呈现真实核心问题、按类型的对象数量、待处理问题和 Agent 讨论入口。状态不可用时不宣称验证通过；对象引用与跳转由宿主处理。
- `workbench-shell.module.css`：仅作用于分析师工作台的蓝灰桌面令牌与三栏布局；导航、主内容和详情有固定位置。保留拖动分栏、收起恢复与 Agent Controller/Portal 身份。
- `analyst-workbench.tsx`：保留原有领域功能与编辑门禁；工具不再藏在可切换的侧栏中，待处理问题可直接进入，搜索覆盖全部可用视图，离开编译时恢复上次分析视图。顶部只保留当前卷宗身份、搜索、验证与设置。
- `workbench-object-directory.tsx`、其 CSS：保留类型/子类型筛选、计数和引用选择，收紧字号及层级间距，并在搜索时直接展示匹配对象；总览的类型入口可定位对应目录。
- 关系图的 `workbench-canvas.module.css`、`workbench-canvas-kernel.tsx`、`workbench-relationship-graph.tsx` 改用浅色画布、低饱和节点与白底关系标签，保留语义色、箭头、布局、聚焦、拖动与动效开关。节点使用实色底、名称字印与加大的点击区域；单击直接打开右侧对象并切回对象基础页，Ctrl 保留多选。关系图不再展示选择/平移工具：拖动空白处平移、点击节点查看详情、滚轮缩放。默认单点拖动只移动自身，不再启用关联节点弹性跟随；底部关系表和文字摘要入口已移除。
- 详情侧栏明确展示“对象详情 / 协作者”文字入口。对象身份改变或详情重新展开时，用 180ms 淡入位移动画过渡；不通过 React key 重挂载编辑器，减少动态效果设置下禁用。对象编辑、关联详情、来源、验证与 Patch 审阅、Agent 运行与输入法保护继续由原有模块负责。

本节取代下文旧版顶栏模式导航、动态模式侧栏、对象目录大字排版与背景装饰的描述。建案中心、小说工作台、API、契约与持久化行为不在本次修改范围。


## 小说协作工作台

- `apps/web/features/analyst-workbench/compile-center-view.tsx`、`compile-center.module.css`、`compile-target-icon.tsx`：编译中心的桌面作品入口选择页，用五个纸质对象 SVG 大图标呈现小说、剧本、互动脚本、作者卷宗和测试材料；小说进入独立工作台，其余未实现入口禁用并标明即将开放。移除事件拼接预览、模拟编译按钮与编译选项；仅在此页隐藏 Agent Dock、线程入口和对象侧栏，保持既有 Controller 与挂载槽，离开后恢复。

- `apps/web/features/novel-workspace/novel-workspace.tsx`：编译中心点击小说后的独立桌面工作表面，左侧章节/卷宗资料、中间创作对话、右侧章节正文；支持拖动分栏、全文阅读、原稿对照、章节编辑、按 Project/Draft 或 Candidate 隔离的浏览器本地编辑稿、Markdown 导出。返回编译中心保留外层工作台状态。
- `apps/web/features/novel-workspace/novel-document.ts`：小说正文前端输入与协作适配端口、全文导入分章、独立原稿/编辑稿、修订门禁、精确 before 匹配与原子采纳；不将事件摘要冒充编译正文，不调用卷宗对象修改或写入 Canon。
- `apps/web/features/novel-workspace/novel-workspace-panels.tsx`、`novel-workspace.module.css`：全文导入与修改审阅的原生模态窗口、桌面三栏档案纸排版。TXT/Markdown 文件只在浏览器读取，导入新稿前备份已有编辑稿。
- `apps/web/tests/novel-workspace.test.tsx`：小说入口、分章保真、原稿隔离、本地保存与失败、全篇协作上下文、显式采纳和过期建议拒绝。

当前工作区尚未对接完整小说产物和小说专用 AI 改写服务：宿主通过 `manuscript` 传入已完成全文，通过 `collaborate` 提供小说正文服务。未提供时展示真实空态与未接入说明，不产生模拟 AI 回复。浏览器编辑稿不是后端正式产物；不得使用当前 CaseFile Agent Patch/Apply 冒充小说写入接口。

## Agent 增强反馈

桌面浏览器验收可通过进程级 `CASEFILE_WEB_DIST_DIR=.next-feedback` 和 `NEXT_PUBLIC_CASEFILE_API_URL` 启动独立 Next.js 输出与隔离 API，默认构建路径保持 `.next`。

- `apps/web/features/analyst-workbench/workbench-agent-feedback.ts`：公共事件的纯归并器，管理有序工作记录、黏性验证阻断、预览代次与 Unicode 偏移检查，以及精确 Draft/revision 的关注投影。
- `apps/web/features/analyst-workbench/workbench-agent-progress.tsx`：仅运行中的消息展示细线工作记录，完整过程默认折叠；终态移除状态记录与验证摘要装饰，具体问题通过消息的验证入口查看。最新回复仍承载 Controller 提供的 Goal 状态、投递记录、停止与待处理操作，不再在输入框上方另设状态条。
- 生产 Agent 的中间、侧栏与底部承载位置统一复用紧凑圆角 Composer（奶油纸色输入框、纸飞机发送按钮），不展示“下条消息”、上下文标签或添加上下文入口；工作台直接选择仍自动带入发送上下文。中间与侧栏复用既有侦探角色素材，运行时不切换为大文本框。
- `apps/web/features/analyst-workbench/use-agent-goal-feedback.ts`：唯一 Controller 使用的 Goal SSE/快照/投递恢复适配器，保持 Goal 完成与 Run 完成分离。
- `apps/web/features/analyst-workbench/workbench-agent-attention.tsx`、`apps/web/features/analyst-workbench/workbench-agent-attention.module.css`：限定工作台子树内的纯展示关注层；使用明确的 `data-agent-object-id` 匹配 React 与 Leaflet 元素，不修改选择、上下文、视口或领域数据。
- `workbench-agent-live-panel.tsx`：统一持有预览、Goal、投递及本地发送反馈，显式提交 steer/replace/follow_up；Portal 迁移不复制 Controller。`workbench-agent-conversation.tsx` 将正式正文与未完成预览分离，并保护向上阅读。
- `apps/web/tests/workbench-agent-feedback.test.tsx`：重放去重、Unicode、跨尝试隔离、预览撤销和关注不改变选择的测试。

## Agent 工作表面与上下文

- `apps/web/features/analyst-workbench/workbench-agent-patch-card.tsx`、`workbench-agent-patch-card.module.css`：生产对话与详情共用的纸质补丁审阅卡；仅从 PublicPatchSet 投影对象分组、Create/Update/Delete 前后对照、请求/一致性分类和状态，匿名新增对象不按同名合并，大批量可展开全部修改；用桌面容器查询适配侧栏，不推测证据、头像或未变更承诺。
- `apps/web/features/analyst-workbench/workbench-agent-message-patch.tsx`：将对话与详情的 Patch Review 绑定到 LivePanel 同一份按 Thread/Draft/revision/status 隔离的审阅状态。对话“应用 N 项”先执行既有 Simulation，再显式确认 Apply；“调整”只追加输入草稿，不自动发送或修改卷宗。

- `apps/web/features/analyst-workbench/workbench-relation-detail.tsx`、`workbench-relation-detail.module.css`：关系详情的卷宗排版，展示关系含义、真实方向及关联对象；内部 ID 和字段路径只用于回调定位，不展示给用户。缺失对象不可导航，来源入口仅对已有详情模型支持的对象开放。
- `apps/web/features/analyst-workbench/workbench-relation-visual.tsx`、`workbench-relation-visual.module.css`：对象摘要与关系详情共用的字印占位和关系方向线；只渲染展示模型，不持有导航或领域状态。

- 对象详情的六类对象（核心问题、实体、信息、事件、地点、假设）共用 `workbench-object-editor.tsx` 与其 CSS 的卷宗排版：类型字印、大标题、真实类型与关联计数摘要、带标签的信息行和末尾补充信息。更多创作信息使用独立设置卡片；`workbench-knowledge-state.tsx` 与其 CSS 展示各时点的认知统计、已确认（角色已知）、推测、误判分组，保留真实引用跳转和缺失状态，不推测信息获得时间或事实可信度。仅人物将特征与目标显示为人物画像；不从说明推测角色或案件位置。`workbench-context-inspector.tsx` 的关联行使用姓名/对象字印占位，方向由 `workbench-relation-overview.ts` 的真实端点生成；认知时点无方向箭头。宽度适配使用桌面详情容器查询，不引入移动端交互。
- 已加载工作台顶部保持模式导航和全局操作，隐藏项目切换器；项目切换仍由无工作稿恢复入口和 `workbench-scope-switcher.tsx` 提供。
- 编译工具导航只保留“编译中心”，使用 `public/casefile-analysis-compile-v1.png` 透明深墨木刻图标，和分析导航图标保持同一视觉系统，不再把中文短标签绘制成方框。

- `apps/web/public/casefile-analysis-{timeline,relations,reasoning,map,evidence}-v1.png`：分析导航的透明图标，分别表示时间线、关系图、推理分析、地图和证据对比；`casefile-analysis-icons.md` 记录生成提示词。`analyst-workbench.module.css` 通过 alpha mask 继承菜单选中和悬停颜色。

- `apps/web/features/analyst-workbench/workbench-relation-overview.ts`：对象详情关联摘要的纯展示模型；按真实对象类型确定因果、线索、关系与推理引用的展示优先级，保留原始关系 ID 和全部语义关系，区分因果方向与认知时点，不推测叙事重要性。`workbench-context-inspector.tsx` 将其渲染为前四条关键关联摘要，经 `workbench-object-editor.tsx` 的展示槽放在核心信息之后、补充设定之前。

- `apps/web/features/analyst-workbench/use-workbench-collaboration.ts`：Reducer 的页面会话适配器；IME 合成期间延后会隐藏或迁移 Composer 的布局操作，避免先隐藏旧承载槽而打断输入法。

- `apps/web/features/analyst-workbench/workbench-sidebar.tsx`：统一侧栏的对象/Agent 基础页、历史导航和临时详情承载槽；只负责布局，不持有消息或复制审阅状态。
- `apps/web/public/casefile-sidebar-dossier-v1.png`、`casefile-sidebar-companion-v1.png`：侧栏对象详情与协作者的透明墨刻图标，以 CSS alpha mask 跟随选中/悬停颜色；图标按钮保留中文无障碍名称和悬停提示。

- `apps/web/features/analyst-workbench/workbench-collaboration-state.ts`：集中维护工作模式、直接选择、Inspector 开关、中间/右侧详情栈与 Escape 优先级；Patch 按工作模式路由，其他详情始终在右侧。草稿更新不丢弃稳定 ID 路由。
- `apps/web/features/analyst-workbench/workbench-agent-context.ts`：各 Thread 的未发送文本、当前直接选择候选、显式添加标签与移除状态；不得把关联高亮转成消息上下文。
- `apps/web/features/analyst-workbench/workbench-agent-live-panel.tsx`：唯一 Thread/消息/运行/Composer/审阅 Controller，发送时冻结上下文，失败保留草稿，不随承载位置重新创建。
- `apps/web/features/analyst-workbench/workbench-agent-portal.tsx`：迁移稳定 DOM 容器，保留 textarea 身份、焦点和选区；IME 合成期间暂缓迁移。
- `apps/web/features/analyst-workbench/workbench-agent-detail-portals.tsx`：将 Controller 所有的公共 Patch/Finding 与审阅状态投影到双导航栈，不新增审批协议。
- `apps/web/features/analyst-workbench/workbench-collaboration-detail.tsx`、`workbench-collaboration-detail.module.css`：统一 Patch/Validation/Provenance/Relation 详情外壳；按稳定 ID 重建，缺失目标明确提示。来源只使用确定性文本命中、记录派生和已声明 Fragment。
- `workbench-agent-conversation.tsx` 直接展示消息正文，不展示署名、时间、发送时 revision 或上下文标签；公共消息中的上下文快照仍保留，发送上下文继续由 Composer 与 Controller 管理。聊天正文、发送预览与生成预览共用 20px 宋体排版。

本轮仅桌面 Web；Sidebar 宽度保留当前拖动范围，不新增刷新持久化。关系线和关系表入口打开同一详情，详情变化不重建画布。

`apps/web/features/analyst-workbench/workbench-header.module.css`：已载入工作台的 56px 桌面顶栏样式；三个图标与文字模式标签（15px 加重文字、选中图标朱红底与短下划线）、定宽命令搜索、紧凑验证状态、设置与可收起的更多操作。品牌入口返回建案中心，重置收进更多菜单；验证入口复用现有问题视图与未保存编辑保护。加载/错误门禁顶栏继续由 `workbench-gate.module.css` 管理。

关系星图的光晕按节点身份错峰呼吸，聚焦的单向关系使用独立 SVG 流光覆盖层，保留原始箭头与语义线型。动效开关仅控制展示，不改变布局或选择；系统减少动态效果设置优先停用动画。实现位于 `workbench-canvas-kernel.tsx` 与 `workbench-canvas.module.css`。

本文涵盖 `apps/web/` 下所有受 Git 跟踪的源码文件职责。新增、删除、重命名前端源文件时必须同步更新本文。

路由壳放 `app/`，业务交互放对应 `features/<domain>/`，通用视觉组件放 `components/`，无 UI 基础设施放 `lib/`。

前端产品新增、重构与视觉验收只面向桌面 Web；默认不新增或维护移动端断点与移动端专属交互，除非用户明确改变这一产品约束。

## 项目配置

| 路径 | 职责 |
|---|---|
| `apps/web/package.json`、`next.config.ts`、`tsconfig.json`、`eslint.config.mjs`、`vitest.config.mts` | React/Next.js 前端的运行、类型、Lint、测试和构建配置。 |

## App Router 页面

| 路径 | 职责 |
|---|---|
| `apps/web/app/layout.tsx`、`app/providers.tsx`、`app/globals.css` | App Router 根布局、真实 Workflow Provider、演示 Prototype Provider 和"数字档案纸"全局设计令牌。 |
| `apps/web/app/page.tsx`、`app/brief/page.tsx`、`app/workbench/page.tsx` | 真实原稿建案/Agent 润色审阅、Brief 原子确认/生成和 v1 工作台路由壳。 |
| `apps/web/app/visual-intake/page.tsx` | 独立的建案视觉实验路由壳；只渲染本地 Fixture Demo，不替换真实建案中心或接入生产工作流。 |
| `apps/web/app/demo/` | 建案、Brief、工作台旧地址到真实产品路由的兼容跳转，以及仍使用 Prototype Store 的推理/质量实验路由。 |

## 通用组件

| 路径 | 职责 |
|---|---|
| `apps/web/components/archive-shell.tsx` | 全站唯一产品壳、真实项目侧栏、七模块产品地图、Provider 设置入口和工作流导航。 |
| `apps/web/components/archive-ui.tsx` | 无 Store 依赖的 Case Spine、文档头、面板头、状态徽记等全站设计系统组件。 |
| `apps/web/public/casefile-brand.png` | 用户确认并按导航栏尺寸优化的 CaseFile 品牌位图。 |
| `apps/web/public/casefile-agent-mascot-3d.png` | 工作台底部 Agent 输入框左侧的透明背景 3D 卷宗调查员角色图标。 |

## 业务功能模块

| 路径 | 职责 |
|---|---|
| `apps/web/features/analyst-workbench/analyst-workbench.tsx`、`analyst-fixture.ts`、`workbench-views.ts` | 分析师工作台三大模块页面编排（工作台、分析、编译作品）；工作台内部合并当前工作总览与对象档案，共用顶部入口和左侧对象索引，左侧对象选择直接进入 Agent 对话并把对象作为右侧 Inspector 与对话上下文，不再维护中央对象卡片墙；其余模式按上下文切换导航，并共享按需 Inspector、轻量 Agent 入口、跨面板状态、Current Draft 全量重载与本地 Fixture 数据模型；`workbench-views.ts` 是底层生产画布视图的唯一注册表，时间/关系/推理/证据/空间归入分析模式，编译中心归入编译模式；导出预览入口、页面及命令已移除；证据对比视图内含「线索对比」与「待处理问题」两个子视图。 |
| `apps/web/features/analyst-workbench/workbench-scope-switcher.tsx`、`workbench-scope-switcher.module.css` | 顶部全局项目切换与独立工作稿切换组件；工作台标题栏不再展示工作稿切换入口。组件负责真实列表、加载/失败/空态、指针并发激活、键盘关闭/焦点恢复和返回建案中心生成新稿。 |
| `apps/web/features/analyst-workbench/workbench-real-data.ts`、`workbench-real-data-types.ts`、`workbench-spatial-model.ts` | 将真实 CaseFile Current Draft 纯映射为对象目录、时间线、关系/推理图和空间卷宗工作台模型；关系图只消费显式 Relationship，不从事件同场或地点活动事实推断关系；空间纯数据层负责 WGS84、场景坐标、确定性拓扑、未定位地点、地点级事件聚合、空间关系规范化和图层可见性过滤，Fixture 只通过显式适配器进入。 |
| `apps/web/features/analyst-workbench/workbench-object-directory.tsx`、`workbench-object-directory.module.css`、`workbench-object-detail-model.ts`、`workbench-object-editor.tsx`、`workbench-object-editor.module.css`、`workbench-object-persistence.ts` | 对象目录的名称/编号搜索、互斥类型筛选与动态计数；详情模型集中转换真实对象、中文词汇、关联引用和结构锁，详情面板提供浏览态、按需快速编辑、关联事件和未保存切换保护；持久化 Hook 统一对象/空间位置 PATCH、Current Draft 重载和 revision 冲突结果，不依赖 client-only 地图组件。 |
| `apps/web/features/analyst-workbench/workbench-context-panels.tsx`、`workbench-context-panels.module.css` | 展示当前 Draft 的真实确定性验证与只追加审计事实，并统一加载、空态、错误、重试与专属面板样式。 |
| `apps/web/features/analyst-workbench/workbench-secondary-views.tsx` | 历史时间线次级视图；编译入口选择页独立于此模块，导出预览已移除。 |
| `apps/web/features/analyst-workbench/spatial-map/` | client-only 空间卷宗边界；React 视图管理模式、分模式图层/视口、状态核验、未定位抽屉、快览与显式位置编辑，独立 controls/preview-card 避免主视图膨胀；Leaflet renderer 分别使用地理 CRS 与 `CRS.Simple`，只产生关系覆盖层及拖动坐标预览，不负责 PATCH 或 revision。 |
| `apps/web/features/analyst-workbench/workbench-relationship-graph.tsx`、`workbench-reasoning-graph.tsx` | 将关系与推理读模型适配为只读画布场景，声明节点类型颜色、图例、详情选择和无障碍替代表。 |
| `apps/web/features/analyst-workbench/workbench-evidence-comparison.tsx` | 证据对比视图的「证据 × 假设」矩阵：按核心问题展示每条信息/证据对每个假设的支持/冲突/中立判定、强度与理由，并在选中单元格时给出可靠度、叙事分类、信息类型与支持/反驳的主张；数据来自 `reasoningGroups`（`Hypothesis.evidence_assessments`），真实工作稿立即可用。 |
| `apps/web/features/analyst-workbench/workbench-validation-issues.tsx` | 证据对比视图的「待处理问题」子视图：按创作者可理解的问题类别聚合重复发现，优先展示影响、涉及内容与修改建议；规则代码、数据路径等开发字段折叠在技术详情中，并保留对象定位、验证重跑和 finding 处理动作。fixture 演示继续保留知识状态三段式对照与补丁审批流程。 |
| `apps/web/features/analyst-workbench/workbench-agent-surface.tsx`、`workbench-agent-composer.tsx`、`workbench-agent.module.css` | 卷宗统筹 Agent 的底部主聊天框 `dock` 与完整对话 `desk` 表面边界、基于真实 Workbench Focus 的上下文输入、中文 IME 安全的 Composer，以及独立于主工作台的 Agent 视觉布局；底栏直接发送并进入完整对话，不再维护 Quick Ask 浮层。 |
| `apps/web/features/analyst-workbench/workbench-agent-live-panel.tsx` | 生产 Thread 与公共 Message/Run 控制器、公共 SSE 恢复、消息发送与 Patch API 生命周期；opaque handle 仅保存在控制器状态中，并向 Workbench Inspector 提供生成契约中的作者审阅事实。 |
| `apps/web/features/analyst-workbench/workbench-agent-thread-menu.tsx` | Agent Thread 搜索、Combobox/Listbox 键盘选择、新建、置顶、重命名、归档和归档筛选呈现；持久化由 live panel 回调完成。 |
| `apps/web/features/analyst-workbench/workbench-agent-desk.tsx` | 完整 Agent Desk 的阅读列布局：Header、Conversation、Task Strip、预设指令和 Composer 组合，不拥有领域状态。 |
| `apps/web/features/analyst-workbench/workbench-agent-inspector.tsx` | Workbench 右侧 Inspector 中的公共 Patch/Finding 唯一审阅所有者：按“你要求的修改 / 为保持一致性同步调整”展示中文 target、field、before/after/why、影响与原子规则，支持公共 warning 确认、Apply/Undo/Redo；不展示内部操作、版本、hash 或 policy，也不自行推导 can_apply。 |
| `apps/web/features/analyst-workbench/workbench-agent-task-strip.tsx` | 公共 Run activity、上下文状态、验证摘要、停止回复和终态摘要的 Sticky 展示；不显示 token、Provider、Prompt 或内部阶段。 |
| `apps/web/features/analyst-workbench/workbench-agent-conversation.tsx` | 基于生成 Public DTO 的调查记录式消息 Turn、公共引用/Finding/Patch 摘要入口和 Workbench 定位回调；不读取原始 Task result，也不执行 Patch 写入。 |
| `apps/web/features/analyst-workbench/workbench-agent-panel.tsx` | 本地预览 Agent 编排；与生产面板共享 Agent Surface，但不接真实 Thread/Task 持久化。 |
| `apps/web/features/analyst-workbench/workbench-canvas-kernel.tsx`、`workbench-canvas-layout.ts`、`workbench-canvas.module.css` | 关系图与推理图共享的 React Flow 只读画布内核；关系图展示人物、组织等实体及无连线的地点点位，只有实体间显式 Relationship 生成连线，并使用确定性力导向星图、发光圆点节点、语义彩边及关系类别线型；拖动关系节点时按拓扑距离弹性带动同一连通分量，孤立地点保持原位，松手后与单点拖拽共用布局历史和持久化；推理图保留确定性 Dagre 布局；两者共享按 `project:{projectId}:draft:{draftId}` 隔离的浏览器布局偏好、选择/平移/多选/全屏交互，不得表达或触发领域写入。 |
| `apps/web/features/analyst-workbench/workbench-canvas-controls.tsx`、`workbench-icon.tsx`、`workbench-geometry.ts`、`workbench-presenters.ts` | 工作台内部复用的画布控件、悬浮提示、图标、几何边界和展示标签；不承载跨功能业务状态。 |
| `apps/web/features/analyst-workbench/timeline/` | 时间线专属的无时区时间解析、React+D3 比例轴、点/整段区间拖动、人物/地点泳道、时间确定性与问题叠层、窄屏编辑清单和写入前影响预览，以及 Current Draft 的独立版本化线性 Exposure Plan 编辑；事实时间写入仍走 Draft revision，披露顺序只推进 Plan revision，二者均不绕过身份与并发门禁。 |
| `apps/web/features/workflow/` | 原稿建案、独立润色候选审阅、Brief 原子拆解/人工确认、Brief → Draft 和工作台的唯一产品实现，以及设置弹窗、SSE 可恢复安全审计轨迹、完成门禁、目标无关对象展示与有限编辑；直接接真实 API/Workflow Store。 |
| `apps/web/features/intake/intake-center.tsx`、`intake-model.ts`、`brief-confirmation-feedback.tsx` | 真实四步建案中心、创作简报编辑、确定性 inline 决策门禁，以及后台确认中的归档式状态过渡；`brief_review` 仅作为隐藏服务端生命周期，不再拥有独立页面。 |
| `apps/web/features/intake/visual-intake-demo.tsx`、`visual-intake-demo.module.css` | `/visual-intake` 的隔离式桌面视觉实验：编排三条起案入口、共同追问/确认、Brief 失效更新、冻结与修订历史，并实现“活的卷宗脊柱”视觉；不调用 API 或持久化状态。 |
| `apps/web/features/intake/reverse-parse-stage.tsx`、`reverse-parse-stage.module.css` | 路径 C 反向解析审阅：文档上传与解析进度、解析块来源高亮跳转、grading 分级与 field_sources 区分展示、逐项确认/拒绝/重试、高风险项警示，并形成 Brief 候选进入后续冻结/生成流程。 |
| `apps/web/features/workflow/task-recovery.ts` | 三类真实 TaskRun 共用的最近任务/本地指针恢复、轮询、事件积压合并和按游标重连 SSE。 |
| `apps/web/features/reasoning/` | 推理实验室的整卷生成态、与真实进度绑定的因果点火动效、路径总览、React Flow 静态因果画布、候选审阅器，以及实验室内来源快速查看与显式工作台定位交互。 |
| `apps/web/features/quality/` | ValidationIssue 筛选、确定性证据链、PatchCandidate 人工审阅和显式重新验证。 |
| `apps/web/features/benchmark/` | Benchmark 配置、运行与结果界面。 |
| `apps/web/features/simulation/` | 玩家模拟配置、运行进度和报告。 |
| `apps/web/features/compiler/` | 固定快照编译门禁、目标配置、产物清单和本地构建状态。 |
| `apps/web/features/tasks/` | 长任务、重试、取消、恢复和用量展示。 |

## Lib 基础设施

| 路径 | 职责 |
|---|---|
| `apps/web/lib/api-client.ts` | 真实 `/api/v1` HTTP/SSE Client、CaseFile Chat 公共 Run/Event/Patch 生成契约消费者、Logical Mutation simulation/hash/debt/Undo/Redo DTO、工作流/工作台读模型、Compiler Artifact 内容读取类型，以及统一错误消息；Chat 未知 SSE 事件失败关闭。 |
| `apps/web/lib/prototype-model.ts` | 仅供本地原型使用的状态模型、样例数据和编译门禁纯函数；正式服务端契约继续来自 `@casefile/contracts`。 |
| `apps/web/lib/reasoning-prototype.ts` | 推理实验室本地 Fixture、推理路径/节点/边/候选模型与纯查询函数；不承担 React UI。 |
| `apps/web/lib/` | 其他无 UI 基础设施；不得放 React 业务状态。 |

## Store

| 路径 | 职责 |
|---|---|
| `apps/web/store/workflow-store.tsx` | 真实工作流最小会话指针、当前 Provider 选择和前端恢复状态；服务端事实始终由 API 重新读取。 |
| `apps/web/store/prototype-store.tsx` | 尚未接后端的推理/质量实验页 Fixture 状态；不得重新承载建案、Brief 或工作台页面。 |

## 测试

| 路径 | 职责 |
|---|---|
| `apps/web/tests/` | 原型状态迁移、失败门禁、真实/演示状态边界和前端组件测试。 |
| `apps/web/e2e/` | 浏览器用户闭环测试。 |

`workbench-agent-conversation.tsx` 的 AgentAnswer 将正式回复和预览中的空行、编号及无序项渲染为段落和语义列表；仅展示文本，不执行 HTML，不推测分点、不更改历史正文。`workbench-agent.module.css` 提供段落与列表间距。
