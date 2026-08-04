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
