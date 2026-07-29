# CaseFile 技术栈与第一版 MVP v1

## 1. 一句话结论

第一版做一个**桌面 Web 推理卷宗工作台**，只跑通这一条闭环：

```text
Brief → Agent 生成结构化 Draft → 人工编辑 → Validator 定位问题
→ Agent 生成 PatchCandidate → 人工批准/拒绝 → 冻结 Canon → 导出测试包
```

第一版是可供内部试用和评测的 MVP，不是完整商业版本。系统采用前后端同仓、模块化单体、用户自带模型 API Key、单一 Agent Runtime 和单一导出目标。用户自行选择已接入的模型厂商与模型，系统首版不做模型推荐、自动路由或静默切换。

## 2. 技术栈列表

| 层级 | 选择 | 用途 |
|---|---|---|
| 前端框架 | Next.js + React + TypeScript | 桌面 Web、页面路由、同源 API 代理 |
| UI | Tailwind CSS + shadcn/ui | 快速建立统一设计系统和工作台组件 |
| 前端数据 | TanStack Query | API 请求、缓存、重试和任务状态刷新 |
| 编辑器状态 | Zustand | 仅管理选中对象、面板开关、未提交编辑等本地状态 |
| 表单与校验 | React Hook Form + Zod | 对象编辑、即时校验和错误定位 |
| 后端 | Python + FastAPI + Pydantic | REST API、契约校验、应用服务 |
| 数据访问 | SQLAlchemy + Alembic | 数据持久化与数据库迁移 |
| 数据库 | PostgreSQL | CaseFile、版本、引用、任务、审计；复杂对象使用 JSONB |
| 后台任务 | Python Worker + PostgreSQL Job Table | Agent、验证、推理和导出长任务 |
| 进度通知 | SSE | 服务端向前端推送任务进度；首版不引入 WebSocket |
| 机器契约 | JSON Schema 2020-12 + OpenAPI | 生成 Pydantic 模型、TypeScript 类型和 API Client |
| Agent Runtime | OpenAI Agents SDK + 自建 AgentRuntime | 负责动态规划、模型调用、工具循环、流式输出和结构化结果 |
| 模型接入 | BYOK + ModelProviderAdapter | 统一接入 GPT、DeepSeek、Kimi 等模型；用户显式选择厂商与模型 |
| 图与推理 | NetworkX | 引用、时间、知识状态、可达性和候选图分析 |
| 测试 | pytest + Vitest + React Testing Library + Playwright | 领域、接口、组件和端到端回归 |
| 工程管理 | pnpm + uv + PowerShell 脚本 | 分别管理前端和 Python 依赖，统一启动与检查 |
| 本地运行 | Web、API、Worker、PostgreSQL 四个单实例 | 优先支持本机脚本启动，Docker Compose 作为可选方式 |

### 不引入的组件

第一版不引入 Redis、Celery、Kafka、微服务、图数据库、向量数据库、Kubernetes、LangGraph、Claude Agent SDK 和多 Agent Debate。只有固定评测证明现有方案不够时再升级。

### Agent Runtime 与模型接入边界

首版统一使用 **OpenAI Agents SDK** 运行 Agent，但不把 CaseFile 绑定为只能调用 OpenAI 模型：

- OpenAI Agents SDK 负责动态 Agent Loop、工具调用、Streaming、结构化结果、生命周期 Hook 和审批中断接口。
- `ModelProviderAdapter` 负责把用户选定厂商的流式输出、工具调用、结构化输出、用量和错误统一成 CaseFile 内部接口。
- OpenAI-compatible API 只有通过能力测试后才复用通用 Adapter；协议或行为不兼容的厂商使用专用 Adapter。
- Provider 不支持当前任务所需能力时，必须明确报错或经用户确认后降级为单次生成，不得静默切换到其他模型。
- 首版只依赖自建 Function Tool、MCP Tool 和自有 JSON Schema，不把 OpenAI 专属托管工具作为主流程依赖。
- CaseFile 自己负责 `TaskRun`、Trace、Checkpoint、预算、取消、权限、结构锁、PatchCandidate、Approval、Canon 和审计；这些状态不交给模型厂商托管。
- Trace 使用 SDK Hook 或自定义 Trace Processor 接入自有事件存储。BYOK 场景默认不得把其他厂商的模型输入输出上传到 OpenAI Trace 平台。
- Checkpoint 采用“最近安全点恢复”：进程异常后从最近成功工具结果、结构化候选或待审批状态重跑，不承诺恢复模型调用中的未完成 Token。

任务内允许模型根据中间结果动态调整探索步骤，不要求预先把每一步固化为有向图。框架选型原因见 `docs/技术选型和tradeoff/Agent框架选型与Tradeoff.md`。

### 数据库迁移约定

当前后端采用 Python + SQLAlchemy，因此数据库迁移统一使用 **Alembic**，不再额外引入 Flyway。若以后整体切换为 Java/Spring Boot，再评估改用 Flyway；同一项目不能同时维护两套迁移工具。

迁移文件放在：

```text
backend/
  alembic.ini
  migrations/
    env.py
    versions/
      0001_initial_schema.py
```

执行约定：

```bash
# 根据 ORM 变化生成候选迁移
uv run alembic revision --autogenerate -m "change description"

# 人工检查迁移文件后升级
uv run alembic upgrade head

# 查看当前数据库版本
uv run alembic current

# 仅用于本地开发验证的单版本回退
uv run alembic downgrade -1
```

迁移规则：

- 不使用 `Base.metadata.create_all()` 代替正式迁移。
- 每次数据库结构变化必须在同一变更中提交对应的 Alembic 迁移。
- 自动生成只负责产生候选文件；重命名、枚举、JSONB、索引和数据迁移必须人工检查。
- 数据迁移必须支持已有数据库升级，不能只保证空库初始化成功。
- API 和 Worker 启动时检查数据库版本；版本落后或不兼容时明确失败。
- CI 同时验证空库执行 `upgrade head`，以及上一基线数据库升级到 `head`。
- 生产环境优先通过新迁移向前修复；可能丢失数据的 `downgrade` 不作为常规回滚方案。

## 3. MVP 功能范围

### 3.1 五个页面

1. **项目列表**：新建、打开和查看最近项目。
2. **创建与 Brief**：输入一句想法、规模、约束和补充素材。
3. **CaseFile 工作台**：左侧对象树、中间对象表单/时间线、右侧问题与 Agent 动作。
4. **问题与补丁审阅**：查看问题依据、修改前后 Diff、影响对象，并批准或拒绝。
5. **冻结与导出**：检查门禁、冻结 Canon、下载导出物。

### 3.2 最小数据对象

- 项目与版本：`project_profile`、`draft_revision`、`canon_version`。
- 推理对象：`entities`、`locations`、`events`、`information_units`。
- 结论对象：`claims`、`hypotheses`、`resolution_specs`。
- 结构对象：`relationships`、`reasoning_paths`、`constraints`。
- 质量对象：`validation_issue`、`patch_candidate`、`task_run`、`audit_log`。

新增字段必须至少满足一个条件：参与验证，或参与导出；否则不进入 MVP 核心 Schema。

### 3.3 Agent 首版能力

只实现两个受控任务：

1. `brief_to_draft`：把 Brief 转为符合 JSON Schema 的 Draft CaseFile。
2. `issue_to_patch`：针对一个 ValidationIssue 和指定对象，生成带对象 ID、JSON Patch 操作和预期修复规则的 PatchCandidate。

Agent 只能写入 Draft 或 PatchCandidate，不能直接写入 Canon。输出必须经过 Schema、引用、锁定范围和预算检查。
两个任务内部都可以由 OpenAI Agents SDK 执行动态多轮探索和工具调用，但必须受最大轮次、Token、费用、超时和取消策略约束。

### 3.4 Validator 首版能力

优先实现六组确定性规则：

1. Schema、必填字段和枚举合法性。
2. ID、对象引用和删除影响完整性。
3. 事件时间顺序、地点冲突和简单移动可达性。
4. 角色知识状态与信息可见性。
5. 关键 Claim 的证据覆盖与信息可获得性。
6. Resolution/Hypothesis 的结论条件完整性。

严重度统一为：S0 阻断、S1 必须处理或明确接受风险、S2 建议。模型只能辅助产生 S2 或待人工复核的 S1，不能单独产生 S0。

### 3.5 版本与导出

- `Draft`：用户和 Agent 可编辑。
- `PatchCandidate`：AI 建议，尚未生效。
- `Canon`：人工批准后冻结，不允许原地修改。

首版只导出一种“通用互动推理测试包”，至少包含：

- `casefile.json`
- `validation_report.json`
- `dossier.md`
- 版本、Schema、Validator 和模型信息组成的 `manifest.json`

## 4. 明确不做

- 多人实时协作、复杂角色权限和公网注册。
- 玩家模拟器的正式版本。
- 拖拽式关系图、地图和推理图编辑器。
- 系统推荐模型、自动模型路由、同一任务静默跨供应商切换、多 Agent Debate、默认启用 ToT/GoT。
- OCR、音视频解析、外部插件和 MCP 生态。
- 多种下游内容格式、自动发布和商业计费。
- 移动端完整创作体验。

## 5. MVP 验收标准

- 3 个标准 CaseFile 能完成从 Brief 到导出的完整流程。
- Agent 结构化输出一次通过或自动修复后的 Schema 合法率不低于 95%。
- 至少 20 个故障 Fixture 可稳定检出预期 Rule ID、严重度和对象引用。
- 所有已实现的确定性规则均具备通过和失败 Fixture。
- 导出前 S0 必须为 0，所有导出文件可被程序重新解析。
- Agent 对 Canon 的每次变更都经过人工审批并留下审计记录。
- API、Worker 或浏览器刷新后，已保存 Draft 和已完成任务结果不丢失。
- 空数据库可以通过 Alembic 一次迁移到 `head`，上一基线数据库升级后已有数据不丢失。
- 固定 Benchmark 能记录模型厂商、模型、Adapter、Prompt、Schema、规则、输入、耗时、TTFT、Token 和费用版本。

## 6. 建议周期

第一版内部 MVP 按 **8 周**控制：

| 周期 | 目标 |
|---|---|
| 第 1 周 | 前端可点击设计稿；其余三人完成研究方案和最小实验 |
| 第 2 周 | 冻结 Schema v0.1、初始 Alembic 迁移、页面状态、API 契约、Benchmark v0 和技术 Spike |
| 第 3–4 周 | 项目/CaseFile CRUD、工作台、Draft 生成和任务状态贯通 |
| 第 5–6 周 | Validator、问题定位、PatchCandidate、Diff 与审批闭环 |
| 第 7 周 | Canon 冻结、导出、端到端 Benchmark 和失败恢复 |
| 第 8 周 | 真实样例试用、修复高优问题并冻结 MVP |

若第 2 周末仍未冻结 Schema、API 和 Benchmark，停止增加功能，先解决契约分歧。
