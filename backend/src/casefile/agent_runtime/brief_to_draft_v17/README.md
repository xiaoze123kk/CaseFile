# Brief-to-Draft v17

v17 保持 v16 的业务依赖、并行生成与修复预算，增加程序驱动的 Skill 披露和作用域 Hook。
Registry 默认仍为 v16；v17 仅通过显式冻结版本使用。

## 职责与资源

- `generation_hooks.py`：事件、绑定、只读输入快照、类型化结果和顺序 dispatcher。
- `generation_hook_policy.py`：v17 必需校验与阶段观察绑定，随 Pipeline spec 冻结。
- `generation_validation_hooks.py`：复用原纯校验器，不持有模型、数据库或修复循环。
- `generation_skills.py`：发现描述、激活组件、选择资源、调用作用域清理及执行指纹。
- `skill/SKILL.md`：能力名称与描述的唯一来源，包含流程概览和资源索引。
- `skill/manifest.json`：组件契约、资源哈希、条件、问题码及内建 Hook 引用。
- `prompts/brief_to_draft/v17`：实际 Markdown 材料；Prompt Package schema 3 的
  `deferred_fragments` 声明按需片段。历史 schema 2 校验保持不变。

首轮只支持内建 Python 回调。Hook 不调用模型、写数据库、修改共享状态或推进阶段。
每个回调收到独立快照；返回资源 ID、问题列表、阻止原因或观察信息。只有准备事件能
返回资源，只有产物事件能返回问题。可选 Hook 只能观察阶段结束或失败。
超时采用 asyncio 的协作取消；内建同步纯校验不得进行阻塞 I/O。

Skill 激活发生在模型调用之前，在调用成功、复用、失败或取消后通过 finally 清理。
并行 Story/Evidence 的激活对象完全独立，不使用 session 全局注册表。每次修复重新激活，
不提供 once。未知处理器、条件、资源或不匹配的契约/哈希会在模型调用前失败。

## 扩展示例

增加领域校验时：

1. 在纯领域模块实现返回现有 issue 格式的校验函数。
2. 在 `generation_validation_hooks` 的静态处理器集合中适配函数。
3. 在新发布 spec 的 `hook_bindings` 增加 `AfterArtifact` 绑定；component 匹配已有
   验证边界，例如 `_v16_story_relationship_coverage_issues`。处理器从快照读取该边界的
   `args` 和 `kwargs`，不能修改原产物。必须为语义变化发布新版本。
4. 补充主路径、错误路径与修复预算测试；无需修改执行主循环。

增加指导材料时：在 Prompt Package 中增加带哈希的片段，并在 Skill manifest 的
`conditional_resources`、`repair_resources` 或 `issue_resources` 中引用。
条件只允许内建的 `always`、`has_entities`、`has_competition`；新条件需要代码实现与测试。
Planner 的关系覆盖规则始终加载，因为它必须在决定对象数量前获知要求。
未知问题码仍加载该组件的通用修复指导。

## 追踪与恢复

步骤 input_hash 绑定实际输入、指令、契约、工具策略、Skill manifest 和 Pipeline Hook
配置。步骤复用仍检查输出 Schema 与输出哈希。Blueprint 失效影响全部下游；时间失效
影响 Story、Evidence 和 Governance；Evidence 失效影响 Governance。矩阵、链接、编译与
最终门禁重新执行。

Skill 资源 ID、选择原因、指令哈希和 Hook 记录写入 AgentStepRun.diagnostic_jsonb.execution。
Hook 观察记录使用独立 `hook_*` 步骤；内部事件不追加公共 SSE，也不推进公开阶段。
公开业务步骤列表过滤 Hook 观测行，步骤 DTO 不包含 execution 材料。权威事件、lease 和候选事务仍由 Worker/Application 管理。

## 验证

定向测试：`test_generation_skills_hooks.py`、`test_generation_skill_runtime.py`，以及历史
brief-to-draft 和 Prompt Package 回归测试。集成测试使用隔离 `_test` 数据库和 FakeProvider。

真实验收入口：

```powershell
scripts/acceptance-brief-to-draft-v8.ps1 -PromptVersion brief-to-draft-v17 -Provider deepseek -ModelId deepseek-v4-pro -Repeats 30
```

模型覆盖只作用于隔离测试库的设置副本。报告记录用量、延迟、修复、加载材料及 Hook 结果；
不会切换 Registry 或自动采用候选。定向小样本、Fake 和静态检查不代表正式发布验收通过。
