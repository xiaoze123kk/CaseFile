# Agent 系统提示词版本仓库开发日志

## 基本信息

- 日期：2026-08-04
- 分支：`feature/database-frontend-develop`
- 目标：以 Git 为唯一真源，为四个现有 Agent 功能建立独立、不可变、可打包的 System Prompt 版本仓库。

## 已确认的关键决策

1. 每个 Agent 功能必须拥有独立、完整、可单独演进的系统提示词。
2. 版本采用任务级单调 `vN` 与 System Prompt UTF-8 内容 SHA-256；已发布版本不得修改或删除。
3. `registry.json` 只维护各 Agent 当前启用版本；新增版本与移动当前指针是两个显式步骤。
4. OpenAI 与 DeepSeek 共用同一份核心系统提示词；Provider 的结构化输出适配不进入本仓库。
5. TaskRun 创建时记录 Registry 当前版本；Worker 与 Provider 按该明确版本加载，禁止排队期间漂移到新版本。
6. 本次仅从当前四个版本建立可信基线，不追溯或伪造更早版本。

## 本次纳入范围

- `backend/src/casefile/agent_runtime/prompts/` 包内资源仓库、版本 Manifest、当前版本 Registry 和发布说明。
- 严格、只读、失败关闭的 Prompt Repository 加载器。
- 将四段内联 System Prompt 原文迁入各自版本目录。
- 将明确的 `prompt_version` 贯穿内部 Provider Request、Worker 与 Benchmark。
- Registry 完整性、失败路径、运行时版本固定和 wheel 资源打包测试。
- 同步更新 `AGENT.md` 职责地图。

## 明确排除范围

- 用户输入模板、输出 Schema、工具集或 Provider 适配的统一版本包。
- 新数据库表、提示词快照、逐次模型调用日志或数据库迁移。
- Registry API、Web 管理页、在线编辑和真实模型评测门禁。
- 修改现有 System Prompt 的语义或 HTTP/SSE 公共契约。

## 兼容策略

- 保留现有 `task_runs.prompt_version` 字段及四个当前版本字符串。
- 迁移后的 `system.md` 与当前 Python 内联字符串保持完全一致。
- 已记录版本不受以后 `registry.json` 当前指针变化影响；未知或缺失版本明确失败，不静默回退。
- 保留启动时已有的未提交工作，不覆盖无关前端、文档、后端和 `AGENT.md` 修改。

## 实施进展

- [x] 完成需求 grilling、范围收敛与执行计划确认。
- [x] 完整阅读 `AGENT.md` 并核对工作区已有修改。
- [x] 建立四个版本化 System Prompt 资源与 Manifest/Registry。
- [x] 实现并接入只读 Prompt Repository。
- [x] 补齐测试、打包验证和职责说明。
- [x] 完成静态检查与仓库验证。

## 验证计划

- Registry：Agent 一一对应、目录/版本/Manifest/哈希一致、显式版本读取及关键失败路径。
- 运行时：TaskRun 记录当前版本，Worker/Provider 使用任务记录的明确版本，Benchmark 显式选取当前版本。
- 兼容性：四份迁移文本等价，Fake/OpenAI/DeepSeek 现有行为测试通过。
- 工程：Ruff、mypy、相关 pytest、`scripts/check.ps1 -SkipPostgres`、wheel 内容检查和 `git diff --check`。

## 验证结果

- 四份迁移后的 `system.md` 与原 Python 内联字符串逐字一致，固定基线 SHA-256 测试通过。
- Prompt Repository、Provider 与 Benchmark 相关测试共 24 个通过；全后端 mypy 对 47 个源文件检查通过。
- `scripts/check.ps1 -SkipPostgres` 通过：迁移命名/单链、compileall、Ruff、mypy、Alembic head 与 42 个非 PostgreSQL 测试全部通过，27 个 PostgreSQL 测试按参数跳过。
- 专用 `_test` PostgreSQL 上的 `test_api_vertical_slice.py` 通过，确认 TaskRun 保存 `casefile-chat-v1` 且 Worker 向 Provider 传递同一明确版本。
- 构建 `casefile_backend-0.1.0-py3-none-any.whl` 并检查压缩内容，Registry、README、四份 Manifest 和四份 System Prompt 均已打包，无需修改 Hatch 配置。
- `git diff --check` 未发现本次变更产生的空白错误；输出中的 CRLF 提示来自启动时已有的无关前端工作区文件。

## 首次发布前基线修正

- 经用户明确授权，直接将四个当前版本的 System Prompt 改写为简体中文，不新增版本目录，也不移动 `registry.json` 指针。
- 本次属于首次正式发布前的基线修正；字段名、工具名、枚举值、JSON Pointer 和其他机器标识符保持契约原文。
- 同步更新四份 Manifest 的内容哈希与变更摘要，以及单元测试中的固定发布哈希。正式发布后继续执行已发布版本不可修改的规则。

### 中文基线契约优化

- 经用户要求继续直接修正当前首次发布前基线，不新增版本目录或修改 Registry 指针。
- 四份 System Prompt 均加入输入数据与指令隔离，防止原稿、Brief、CaseFile 或历史消息中的文本覆盖系统规则。
- `brief_polish` 补充语言跟随、特殊表达保留和三个输出字段的明确语义；`brief_anchor_extract` 补充空值、来源排他、原子化、去重、矛盾保留和强度判定规则。
- `brief_to_draft` 明确处理 `repair_feedback`、克制补全、事实可追踪、顶层对象 ID 精确采用以及目标中立要求。
- `casefile_chat` 不再静态误禁 `tags`；Worker 从应用层唯一 `EDITABLE_FIELDS` 白名单生成按集合划分的能力映射，并通过内部 Provider Request 注入模型输入。
- 动态用户输入包装及 DeepSeek JSON Schema 附加指令同步改为简体中文；HTTP API、数据库、SSE 和 TaskRun 冻结输入保持不变。
- Prompt Repository 与 Provider 定向测试 25 项通过，全部非 PostgreSQL 测试 44 项通过，全后端 mypy 对 47 个源文件检查通过。
- 真实 PostgreSQL 聊天闭环用例通过，确认新的内部 Request 仍可完成建议生成、持久化、整批应用和撤销。
- wheel 重新构建成功，四份 System Prompt 的包内字节哈希均与各自 Manifest 一致；本次范围 `git diff --check` 通过。
- `scripts/check.ps1 -SkipPostgres` 已执行，但被工作区同时存在的无关 import 排序改动拦截；本次涉及的全部 Python 文件已用同一 Ruff 配置单独检查通过。
