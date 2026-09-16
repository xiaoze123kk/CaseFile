# Agent 边界 Hook

当前真实 DeepSeek 请求统一使用 `model_policy.DEEPSEEK_MODEL_ID = deepseek-flash`。
`deepseek_transport.py` 在发送前拒绝旧模型 ID；旧运行和评测记录不改写。
小说模型变更发布为 `prose-shadow-runtime-v11`。历史 Pro 参数只保留离线回放能力，
不能在当前策略下重新发起 Pro API 请求或冒充 Flash 的正式资格证据。

`runtime_hooks.py` 提供公共事件、绑定、输入快照和分派器；
`generation_hooks.py` 保留 brief-to-draft 的兼容导出。

## 已接入的边界

| 执行链 | 模块 | 检查 |
| --- | --- | --- |
| 普通 Chat / Goal Finalizer | `chat_completion_hooks.py` | 候选引用与语义校验、公开语言；安全终局投影也经过公开语言 Hook |
| Scene Compiler 定向修复 | `novel_compile_hooks.py` | 未被指定修复的场景必须保持原样 |
| 小说正文准备 | `novel_compile_hooks.py` | 冻结 runtime 绑定、上游内容哈希、ScenePlan 版本 |
| 连续性审核响应 | `novel_compile_hooks.py` | 请求绑定、响应 Schema、场景引用范围 |
| 整本候选 | `novel_compile_hooks.py` | NovelCandidate Schema |

Chat 的规范化仍按原顺序执行。`chat_postprocessing.py` 负责安全补丁候选物化与
既有业务事件；执行循环只组合这一步及完成校验，并负责有界修复调度。
普通 Chat 与 Goal 共用完成校验入口，原 `chat_execution` 导出继续兼容。

## 扩展方法

1. 在对应策略模块增加纯同步 handler，接收 `HookInput`，返回 `HookResult()`。
2. 在该模块 handler 表及有序 `HookBinding` 元组注册明确的 ID、版本和边界。
3. 有可修复的校验失败时，抛出已有领域异常，保留错误码及 issue 信息。
   调用方已有的修复预算、次数与停止条件继续生效。
4. 补充该规则的有效输入、失败输入、修复及恢复路径测试。

在已有边界增加规则无需往 Agent loop 加分支。新增执行阶段或新的事件位置仍需
由编排模块显式接入，不能用 Hook 隐式触发工具、调用模型或安排下一次修复。
本次是现有检查的行为保持提取；之后若改变规则或顺序，必须同时发布新的冻结策略
版本与相应运行指纹，不能拿旧运行的成功记录当作新策略的验证结果。

## 权限与失败语义

Hook 仅为受信任的内建 Python 回调，不从模型输出、Prompt 文本或文件路径动态加载。
每个 handler 获得自己的 payload 深拷贝，顶层只读，不共享可变输入。
handler 禁止网络、数据库、Provider、消息发送及共享状态写入。
这属于模块契约，不是对任意 Python 插件的进程沙箱。

异步分派继续保持 v17 的超时和异常包装。同步分派保留原领域异常，便于现有修复器
分类；同步回调不能被强制中断，超时只在返回后检查，因此仅用于短小的纯校验。
同步边界不接受资源请求或修复 issue 返回值，防止被调用方静默丢弃；校验失败应抛出
相应领域异常。可选 Hook 仅限阶段观察；取消异常始终传播。

小说文学判断仍由 LLM 完成。合法连续性 `blocked` 意见仍交给 Writer 作为建议，
不会被 Hook 直接升级为致命失败。产品交付与严格审核的统计口径不变。

## 诊断与持久化

Hook 记录只含 handler/version/event/stage/component、状态、异常类型和耗时，不记录
输入、正文、凭据或异常文本。Chat 返回内部 diagnostics，失败附在原异常上；
小说正文执行器将本次记录交给 ProseStore，在原有带 lease 校验的产物事务中写入
最终 compile-manifest 对应 AgentStepRun 的 diagnostic_jsonb。既有产物复用不改写旧记录。
公共 SSE、正文产物、候选 Schema 与审批写入权威不变。

## 验证入口

在 backend 下运行：

```powershell
uv run pytest tests/unit/test_runtime_hooks.py tests/unit/test_agent_boundary_hooks.py tests/unit/test_chat_execution.py tests/unit/test_chat_goal_execution.py tests/unit/test_scene_compiler_runtime.py tests/unit/test_generation_skills_hooks.py -q
```

PostgreSQL 集成测试为 `tests/integration/test_prose_shadow_runtime.py` 与
`test_generation_skill_runtime.py`，必须使用隔离的 `_test` 数据库及 Fake Provider。
这些检查验证行为与持久化兼容，不是付费模型能力或性能提升的证据。
