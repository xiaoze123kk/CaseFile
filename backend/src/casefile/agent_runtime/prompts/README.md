# CaseFile System Prompt Registry

此目录是生产 Agent System Prompt 的唯一事实源。每个 Agent 功能拥有独立、完整且可单独演进的 Prompt 版本；运行时代码不得内联生产 System Prompt。

## 目录契约

```text
prompts/
├── registry.json
└── <agent_id>/
    └── vN/
        ├── manifest.json
        ├── system.md                         # 单 Prompt 版本
        ├── <component>.md                    # 原子 Bundle 版本
        └── fragments/                        # Prompt Package v2 包内指令片段
            └── <fragment>.md
```

Prompt 版本有三种互斥形态：

- 单 Prompt：`manifest.json` 引用唯一的 `system.md`，并记录其 `system_prompt_sha256`。
- 原子 Bundle：`manifest.json` 的 `components` 必须精确声明 `planner`、`story`、`evidence`、`governance`；每项只允许同名 `.md` 文件并记录独立 SHA-256。`brief-to-draft-v8` 使用此形态。
- Prompt Package：Manifest `schema_version=2`，声明运行时兼容关系、带哈希的包内 fragments，以及每次模型调用对应的 component。component 只绑定有序指令片段、严格输入契约、输出 Schema 与工具策略；`brief-to-draft-v9` 是首个生产版本，v10 增加竞争矩阵，v11 在其上增加时间与空间语义。

所有 Prompt 文件必须为 UTF-8、LF 换行、非空内容；哈希按原始字节计算。版本目录使用 `vN`，完整版本号使用 `<agent-id>-vN`，其中 `agent_id` 的下划线替换为连字符。

## 发布与激活

1. 复制当前版本为单调递增的新 `vN` 目录；已发布目录不得修改或删除。
2. 修改新 Prompt，并更新 Manifest 的版本链、变更摘要和全部对应文件哈希。
3. 在 `test_prompt_repository.py` 的不可变发布清单中加入该版本的全部哈希；Bundle 必须列出每个组件。
4. 运行 Prompt Repository、Provider 和打包校验，先提交尚未激活的新版本。
5. 评审通过后，单独移动 `registry.json` 中的 `current_version` 指针；回滚同样只移动该指针。

`registry.json` 是生产新任务唯一的激活入口，不能通过环境变量选择历史 Prompt。`TaskRun` 会冻结 Registry 解析出的 `prompt_version`；v8 冻结四组件 Bundle，v9–v12 分别冻结对应的 Pipeline 与 `casefile-generation-tools-v2`，并在任何模型调用或步骤复用前完整校验 Prompt Package、输入契约、输出 Schema 和工具策略绑定。历史 v8–v11 TaskRun 始终按自身冻结版本执行。

未知版本、缺失资源、哈希漂移或 Bundle 组件不完整都会失败关闭，不会静默回退到当前版本。

## Prompt Package 边界

Prompt Package 是模型调用资产与契约的发布单元，不是工作流 DSL。Agent 执行图仍由 `agent_version` 对应的 Python Runtime 管理，工具实现与 Provider 结构化输出适配仍由代码维护。

- Identity、Objective、Instructions、Constraints 不成为固定 Manifest 分类；它们按实际复用需要写入一个或多个 fragment。
- 动态上下文不得插入 fragment。Runtime 必须先通过 component 绑定的 Pydantic 输入契约，再把单一确定性 JSON 文档作为独立 user 消息发送。
- 不支持 Jinja、`str.format`、表达式、任意模板变量、跨版本 fragment 或运行时文件路径。
- 条件行为由静态指令说明，Context 只携带类型化事实和可选诊断。例如定向修复通过 `targeted_repair_issues` 表达，而不是动态拼入修复指令。
- Output Schema 和 Tool Policy 使用版本化注册 ID；Package 加载时验证引用，模型调用前再次验证 TaskRun 冻结的 Agent/Toolset 版本。
- Examples 只有被 component 显式引用为 fragment 时才进入模型上下文；测试 Fixture、Eval 和真实 Provider acceptance 不属于生产提示词资源。

`brief-to-draft-v11` 保持 planner/story/evidence/governance 四部件：Story 使用 `StoryWorldIRV2` 表达无时区作品内壁钟时间与 schematic/WGS84 空间位置，Evidence 继续使用 v10 的 `EvidenceLogicIRV2` 竞争矩阵。它只映射已有 CaseFile 2.0 契约，不生成 Exposure Plan。

`brief-to-draft-v12` 在 v11 基础上增加独立 Temporal Planner：为 Blueprint 的每个事件分配可审计的作品内时间，禁止 `unknown`，要求绝对壁钟锚点，并将相对时间确定性注入 Story 的 `StoryWorldIRV3`。时间规划失败会阻断 Story 及下游生成，历史 v11 TaskRun 不受影响。

v11 已在真实 API → Worker → PostgreSQL/SSE → 不可变候选路径完成 30 次发布验收：语义通过 `28/30`（门槛至少 `27/30`），五类场景为 `5/6`、`5/6`、`6/6`、`6/6`、`6/6`，零不变量违规且失败运行诊断完整。v12 已通过 FakeProvider、Prompt 契约、Docker PostgreSQL 应用服务和 Workbench 时间线回归验证，因此 Registry 当前激活至 v12。需要回滚时同样只移动 `registry.json` 指针，不修改任何已发布版本目录。
