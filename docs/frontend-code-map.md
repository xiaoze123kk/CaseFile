# 前端代码职责地图

本文涵盖 `apps/web/` 下所有受 Git 跟踪的源码文件职责。新增、删除、重命名前端源文件时必须同步更新本文。

路由壳放 `app/`，业务交互放对应 `features/<domain>/`，通用视觉组件放 `components/`，无 UI 基础设施放 `lib/`。

## 项目配置

| 路径 | 职责 |
|---|---|
| `apps/web/package.json`、`next.config.ts`、`tsconfig.json`、`eslint.config.mjs`、`vitest.config.mts` | React/Next.js 前端的运行、类型、Lint、测试和构建配置。 |

## App Router 页面

| 路径 | 职责 |
|---|---|
| `apps/web/app/layout.tsx`、`app/providers.tsx`、`app/globals.css` | App Router 根布局、真实 Workflow Provider、演示 Prototype Provider 和"数字档案纸"全局设计令牌。 |
| `apps/web/app/page.tsx`、`app/brief/page.tsx`、`app/workbench/page.tsx` | 真实原稿建案/Agent 润色审阅、Brief 原子确认/生成和 v1 工作台路由壳。 |
| `apps/web/app/demo/` | 建案、Brief、工作台旧地址到真实产品路由的兼容跳转，以及仍使用 Prototype Store 的推理/质量实验路由。 |

## 通用组件

| 路径 | 职责 |
|---|---|
| `apps/web/components/archive-shell.tsx` | 全站唯一产品壳、真实项目侧栏、七模块产品地图、Provider 设置入口和工作流导航。 |
| `apps/web/components/archive-ui.tsx` | 无 Store 依赖的 Case Spine、文档头、面板头、状态徽记等全站设计系统组件。 |
| `apps/web/public/casefile-brand.png` | 用户确认并按导航栏尺寸优化的 CaseFile 品牌位图。 |

## 业务功能模块

| 路径 | 职责 |
|---|---|
| `apps/web/features/analyst-workbench/analyst-workbench.tsx`、`analyst-fixture.ts`、`workbench-views.ts` | 分析师工作台页面编排、跨面板共享状态、Current Draft 全量重载与本地 Fixture 数据模型；`workbench-views.ts` 是八种生产主画布视图的唯一注册表；证据对比视图内含「证据矩阵」与「验证问题」两个子视图。 |
| `apps/web/features/analyst-workbench/workbench-scope-switcher.tsx`、`workbench-scope-switcher.module.css` | 顶部全局项目切换与标题栏工作稿切换；负责真实列表、加载/失败/空态、指针并发激活、键盘关闭/焦点恢复和返回建案中心生成新稿。 |
| `apps/web/features/analyst-workbench/workbench-real-data.ts`、`workbench-real-data-types.ts`、`workbench-spatial-model.ts` | 将真实 CaseFile Current Draft 纯映射为对象目录、时间线、关系/推理图和空间卷宗工作台模型；空间纯数据层负责 WGS84、场景坐标、确定性拓扑、未定位地点、地点级事件聚合、空间关系规范化和图层可见性过滤，Fixture 只通过显式适配器进入。 |
| `apps/web/features/analyst-workbench/workbench-object-directory.tsx`、`workbench-object-directory.module.css`、`workbench-object-detail-model.ts`、`workbench-object-editor.tsx`、`workbench-object-editor.module.css`、`workbench-object-persistence.ts` | 对象目录的名称/编号搜索、互斥类型筛选与动态计数；详情模型集中转换真实对象、中文词汇、关联引用和结构锁，详情面板提供浏览态、按需快速编辑、关联事件和未保存切换保护；持久化 Hook 统一对象/空间位置 PATCH、Current Draft 重载和 revision 冲突结果，不依赖 client-only 地图组件。 |
| `apps/web/features/analyst-workbench/workbench-context-panels.tsx`、`workbench-context-panels.module.css` | 展示当前 Draft 的真实确定性验证、冻结 Brief 来源正文/追溯标识和只追加审计事实，并统一加载、空态、错误、重试与专属面板样式。 |
| `apps/web/features/analyst-workbench/workbench-secondary-views.tsx` | 时间线、卷宗编辑、导出预览与编译中心等次级主画布视图。 |
| `apps/web/features/analyst-workbench/spatial-map/` | client-only 空间卷宗边界；React 视图管理模式、分模式图层/视口、状态核验、未定位抽屉、快览与显式位置编辑，独立 controls/preview-card 避免主视图膨胀；Leaflet renderer 分别使用地理 CRS 与 `CRS.Simple`，只产生关系覆盖层及拖动坐标预览，不负责 PATCH 或 revision。 |
| `apps/web/features/analyst-workbench/workbench-relationship-graph.tsx`、`workbench-reasoning-graph.tsx` | 将关系与推理读模型适配为只读画布场景，声明节点类型颜色、图例、详情选择和无障碍替代表。 |
| `apps/web/features/analyst-workbench/workbench-evidence-comparison.tsx` | 证据对比视图的「证据 × 假设」矩阵：按核心问题展示每条信息/证据对每个假设的支持/冲突/中立判定、强度与理由，并在选中单元格时给出可靠度、叙事分类、信息类型与支持/反驳的主张；数据来自 `reasoningGroups`（`Hypothesis.evidence_assessments`），真实工作稿立即可用。 |
| `apps/web/features/analyst-workbench/workbench-validation-issues.tsx` | 证据对比视图的「验证问题」子视图：真实数据的确定性验证问题展示规则代码、JSON 路径、字段路径与可定位的目标对象；fixture 演示保留知识状态三段式对照与补丁审批流程。 |
| `apps/web/features/analyst-workbench/workbench-agent-panel.tsx` | 工作台内卷宗统筹 Agent 对话、预设指令和本地响应编排。 |
| `apps/web/features/analyst-workbench/workbench-canvas-kernel.tsx`、`workbench-canvas-layout.ts`、`workbench-canvas.module.css` | 关系图与推理图共享的 React Flow 只读画布内核、确定性 Dagre 布局、按 `project:{projectId}:draft:{draftId}` 隔离的浏览器布局偏好、选择/平移/多选/全屏交互和专属视觉样式；不得表达或触发领域写入。 |
| `apps/web/features/analyst-workbench/workbench-canvas-controls.tsx`、`workbench-icon.tsx`、`workbench-geometry.ts`、`workbench-presenters.ts` | 工作台内部复用的画布控件、悬浮提示、图标、几何边界和展示标签；不承载跨功能业务状态。 |
| `apps/web/features/analyst-workbench/timeline/` | 时间线专属的无时区时间解析、React+D3 比例轴、点/整段区间拖动、人物/地点泳道、时间确定性与问题叠层、窄屏编辑清单和写入前影响预览，以及 Current Draft 的独立版本化线性 Exposure Plan 编辑；事实时间写入仍走 Draft revision，披露顺序只推进 Plan revision，二者均不绕过身份与并发门禁。 |
| `apps/web/features/workflow/` | 原稿建案、独立润色候选审阅、Brief 原子拆解/人工确认、Brief → Draft 和工作台的唯一产品实现，以及设置弹窗、SSE 可恢复安全审计轨迹、完成门禁、目标无关对象展示与有限编辑；直接接真实 API/Workflow Store。 |
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
| `apps/web/lib/api-client.ts` | 真实 `/api/v1` HTTP/SSE Client、工作流与工作台读模型 DTO，以及统一错误消息。 |
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
