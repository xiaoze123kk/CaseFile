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

`registry.json` 是生产新任务唯一的激活入口，不能通过环境变量选择历史 Prompt。`TaskRun` 会冻结 Registry 解析出的 `prompt_version`；v8 冻结四组件 Bundle，v9–v13 分别冻结对应的 Pipeline 与 `casefile-generation-tools-v2`，并在任何模型调用或步骤复用前完整校验 Prompt Package、输入契约、输出 Schema 和工具策略绑定。历史 v8–v12 TaskRun 始终按自身冻结版本执行。

`casefile-chat-v11` 在 v10 基础上加固审计执行器的预算耗尽报告：预算耗尽只停止发起新调用、不代表未取得证据，已检查范围必须与 `valid: true` 的工具结果一致，禁止把末尾的 `tool_budget_exhausted` 描述为全程未开展或否认已成功读取的对象与快照；v10 保留供历史 TaskRun 重放。

`casefile-chat-v12` 在 v11 基础上加固意图路由器：`sub_intents` 增加明确取值表与必填硬规则，确保 `MULTI_QUERY/DECOMPOSE` 后置重写可被稳定触发；“低置信度/随便”修饰的全卷逻辑漏洞复查明确降级为安全问答；其余组件与契约继承 v11。

`casefile-chat-v14` 引入 Chat Validation Pipeline v2：带工具的 route 先运行 Evidence Agent 并冻结有序、限长、带哈希的 Tool Ledger，再由无工具 Structured Finalizer 生成最终候选；领域错误统一为结构化 issue/repair plan，修复轮次复用同一 Ledger，不重新调用工具。v14 仅允许通过显式灰度变量选择，Registry 默认版本不自动切换。

`casefile-chat-v15` 在 v14 编排上增加服务端 Safe Patch Registry：只收录冻结 Ledger 中验证成功且不引入新问题的补丁预演；Finalizer 只能选择这些补丁，运行时在目标唯一时确定性恢复冻结 `value_json`，修复历史与补丁物化记录进入 Benchmark 诊断。v15 同样只允许显式灰度选择，不自动移动 Registry。

未知版本、缺失资源、哈希漂移或 Bundle 组件不完整都会失败关闭，不会静默回退到当前版本。

`story_planner_skeleton-v1` 与 `story_planner_semantic_fill-v1` 是 Constraint-First 生产管线的两个独立、不可变 Prompt。前者只提议求解器字段，后者只填充模型所有字段；新建 Planner Task 以 `story-planner-constraint-first-v1` 记录 bundle 身份，各 AgentModelCall 继续分别记录真实 Prompt 版本与 hash。`story_planner-v3` 仅保留给历史 TaskRun 精确重放。

`prose-fidelity-judge-v1`、`prose-adversarial-judge-v1`、`prose-coherence-judge-v1` 与 `prose-arbiter-v1` 是 N4.5-02 正文语义委员会的四个独立无工具 Prompt。前三者必须按服务端 Checklist 原顺序完整返回，Arbiter 只批量裁决争议项；Runtime 负责 Evidence 原文绑定、Consensus、预算和恢复，Prompt 不拥有控制流或资格决定。

四个 `v2` 版本保留 v1 语义职责，只增加顶层 `server_bindings` 复制协议：`scene_id`、`checklist_hash`、`render_hash` 由 Runtime 预计算并纳入 request fingerprint，模型不得自行计算或误用 ScenePlan/Profile 等上游 hash。v1 保留用于 `0923fe3` 开发消融的精确重放。

四个 `v3` 版本在 v2 的身份绑定之上增加 `server_evidence_catalog`：Runtime 按冻结策略从正文生成 Unicode 区间与逐字原文，模型只能完整复制目录对象，不能自行计算或改写 Evidence。Runtime 仍会复验正文绑定和目录成员资格；v1、v2 保留用于历史调用精确重放。

四个 `v4` 版本把 Provider 输出缩为私有 `compiler.prose-judge-candidate.v1`：模型只返回逐 check verdict、rationale 与服务端 Evidence ID，不再转写 role、scene、hash、Unicode 区间或引文。Runtime 解析 ID 后组装并复验公共 `compiler.prose-judge-report.v1`。v4 同时冻结逐 check 独立判定、地点/时间分别核验、因果两端必须实际成立，以及“授权但提前披露”和“根本未授权新增事实”分离规则；v1–v3 保留用于历史调用精确重放。

四个 `v5` 版本保持 Candidate/Public Report 边界不变，并补齐三项开发集语义：必需 Event 被否定、未来化或假设化时不得放过相关 `location_time`，一个 check 的失败不得传播到正文已实际实现的独立 Beat/Reveal，因果顺序按明确事件关系而非句子排列或自我说明判断。v4 继续保留用于历史调用精确重放。

`prose-writer-v1` 是 N4.5-04 的单轮、无工具完整 Scene Writer。Runtime 在调用前精确复验 ScenePlanIR、NarrativeIR、Profile、Checklist 与前一场 accepted Render，只向模型发送 Checklist 已投影的当前 Scene 权威上下文；模型只返回 `compiler.scene-render-candidate.v1`，Scene identity、stage、round、block ID、字符数与全部 lineage hash 由服务端注入。

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

`brief-to-draft-v13` 保持 v12 的机器契约与生成拓扑，明确 day/hour/minute/second 的无时区壁钟格式并禁止多余低位零、`Z` 和时区偏移。运行时只会确定性移除与声明精度冲突的零值后缀；非零秒、小数秒和时区值仍然进入有限修复流程。

`brief-to-draft-v14` 保持 v13 的时间与推理拓扑，要求除 local_key、枚举、Schema 字段、稳定编号和必要专名外的所有创作者可见自然语言使用简体中文。最终质量门禁会把纯英文的标题、名称、说明、正文、命题、问题、理由及自然语言数组项归属到对应领域部件，并进入既有的一次定向修复。

v11 已在真实 API → Worker → PostgreSQL/SSE → 不可变候选路径完成 30 次发布验收：语义通过 `28/30`（门槛至少 `27/30`），五类场景为 `5/6`、`5/6`、`6/6`、`6/6`、`6/6`，零不变量违规且失败运行诊断完整。v12 已通过 FakeProvider、Prompt 契约、Docker PostgreSQL 应用服务和 Workbench 时间线回归验证。v13 已通过聚焦契约和 FakeProvider 回归后激活；真实 Provider 发布验收仍需单独执行。需要回滚时同样只移动 `registry.json` 指针，不修改任何已发布版本目录。
