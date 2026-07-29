# CaseFile Agent 框架选型与 Trade-off

## 1. 决策结论

CaseFile 第一版选择 **OpenAI Agents SDK** 作为统一 Agent Runtime，通过自建 `ModelProviderAdapter` 接入用户选择的 GPT、DeepSeek、Kimi 等模型。

CaseFile 自己持有任务状态、Trace、Checkpoint、预算、权限、审批和审计。OpenAI Agents SDK 只负责动态 Agent Loop、模型与工具交互、Streaming 和结构化结果，不成为业务状态的唯一事实源。

第一版不引入 LangGraph，也不使用 Claude Agent SDK。若以后出现复杂图状态和逐节点持久化需求，再重新评估 LangGraph。

## 2. 产品约束

本次选型基于以下已经确定的产品要求：

- Agent 在探索过程中需要根据中间结果动态规划，不能被固定流程限制。
- 用户自己配置 API Key，并显式选择模型厂商与模型；系统不推荐、不代选。
- GPT、DeepSeek、Kimi 等模型应尽量运行在同一套 Agent Runtime 中。
- 首版关注较低的首字延迟和较少的运行时层级。
- 用户需要看到 Agent 当前阶段、工具调用、候选结果和等待状态。
- 进程异常后允许从最近安全点重跑，不要求恢复模型调用中尚未完成的生成。
- Agent 只能生成 Draft 或 PatchCandidate，不能绕过人工审批修改 Canon。

## 3. 三种框架对比

| 维度 | OpenAI Agents SDK | LangGraph | Claude Agent SDK |
|---|---|---|---|
| 动态探索 | 原生 Agent Loop，模型可根据工具结果继续规划 | 支持循环和条件边，但运行状态需要表达为 State、Node、Edge | 原生自主循环和工具使用能力强 |
| 多模型统一接入 | 官方提供 Model/Provider 扩展面；非 OpenAI 模型仍需逐厂商验证和 Adapter | 模型集成范围广，Provider 中立性较好 | Runtime 围绕 Claude 与 Claude Code 能力设计，不适合作为任意模型统一运行时 |
| Checkpoint | 审批中断和状态续接可用；进程级安全点由应用补齐 | 内建持久化、Checkpoint 和 Interrupt，三者中最完整 | 支持 Session、Resume 和 Interrupt；业务安全点仍需应用负责 |
| Trace | RunHooks、AgentHooks、自定义 Trace Processor；可接自有存储 | 可观察图节点、状态变化和执行路径 | Hooks 与 OpenTelemetry 接入能力较强 |
| 应用控制权 | 服务端可以自己持有工具、存储、审批和运行策略 | 控制力强，但应用需要接受图状态模型 | 对 Claude Code 工具、权限和运行方式控制力强 |
| 运行时复杂度 | 较轻，适合 SDK-first | 图、状态、Checkpointer 和持久化语义增加学习与运行层级 | SDK 依赖 Claude Agent/Claude Code 运行语义 |
| TTFT 判断 | 预期附加层较少，但仍必须实测 | 不能直接断言必然更慢；图初始化和持久化会增加潜在开销 | 不能仅凭架构下结论，需要与实际托管方式一起测量 |
| CaseFile 第一版适配度 | **高，选用** | 中，能力超过当前恢复需求 | 低，与 BYOK 多厂商目标冲突 |

## 4. 选择 OpenAI Agents SDK 的原因

1. **动态性符合探索任务**：模型可以在 Agent Loop 中根据新发现继续选择工具、修正计划和生成候选方案，不需要预先枚举全部路径。
2. **应用仍掌握核心状态**：官方建议的 SDK 模式允许服务端自己持有部署、工具、存储和审批，符合 CaseFile 的 Draft、PatchCandidate、Canon 和人工门禁边界。
3. **多模型存在正式扩展面**：SDK 文档明确提供 Model 和 Provider 配置入口，适合在外层建立统一 `ModelProviderAdapter`。
4. **Trace 可以自建**：通过生命周期 Hook 和 Trace Processor，可以生成厂商无关的 `TaskEvent`，供任务中心、SSE 和调试页面使用。
5. **首版实现路径较短**：无需先把探索过程建模成完整状态图，能先验证 Agent 是否真的提高内容构建和修补质量。

### 需要接受的代价

- SDK 不替 CaseFile 自动完成进程级持久化、幂等重试和安全点恢复，这部分需要自行实现。
- 不同模型厂商对工具调用、JSON Schema、Streaming、Usage 和错误格式的兼容程度不同，必须建立能力测试矩阵。
- OpenAI 专属托管工具或协议特性不一定能在其他 Provider 上工作，首版不能依赖这些能力完成主流程。
- SDK 默认 Trace 行为需要显式检查。BYOK 和敏感创作内容场景应使用自有 Trace，并避免未经授权上传到第三方平台。

## 5. 暂不选择 LangGraph 的原因

LangGraph 的优势非常明确：持久化、Checkpoint、人机中断、长任务恢复和图状态回放比轻量 SDK 更完整。它不是不能做动态探索，循环、条件边和节点内部 Agent 都可以实现动态行为。

第一版暂不使用它，原因是：

- 当前只要求“从最近安全点重跑”，尚不需要任意节点精确恢复或历史状态分叉。
- CaseFile 已经需要一套自己的 `TaskRun`、审批、预算和审计状态；再引入 Graph State 容易形成两套状态来源。
- 如果把动态 Agent Loop 包在图节点内，会同时维护 SDK 循环和图编排两层控制流。
- 首版更需要尽快验证 Agent 输出质量、Provider 兼容性和真实 TTFT，而不是先建设通用工作流平台。

出现以下任一情况时重新评估 LangGraph：

- 任务跨天运行，并要求逐节点精确恢复和回放。
- 大量并行分支需要汇合、比较、撤销或从历史节点分叉。
- 人工中断点遍布复杂工作流，且需要长期持久化等待。
- 外部副作用需要统一的节点级重试、补偿和可视化状态图。

## 6. 不选择 Claude Agent SDK 的原因

Claude Agent SDK 的自主工具循环、Hooks、Session、MCP 和权限控制能力很强，适合构建以 Claude 为核心的编码或通用 Agent 产品。

它不适合 CaseFile 当前目标的关键原因是运行时围绕 Claude 与 Claude Code 设计，没有面向 GPT、DeepSeek、Kimi 等任意模型的通用 Provider Adapter 定位。选用它会让“用户自由选择厂商”退化为多套 Agent Runtime，增加行为差异、测试矩阵和恢复逻辑。

如果未来产品明确变成 Claude-first，并高度依赖 Claude Code 的文件、Shell、权限或会话能力，可以重新评估；当前 BYOK 多厂商目标下不选。

## 7. CaseFile 自建边界

### Agent Runtime

- 接收任务、CaseFile 输入版本、用户选择的 Provider/Model、预算和结构锁。
- 调用 OpenAI Agents SDK 完成动态规划、工具循环和结构化输出。
- 只返回 Draft、候选方案或 PatchCandidate，不直接写 Canon。

### ModelProviderAdapter

- 统一模型标识、鉴权、流式文本、工具调用、结构化输出、Usage、超时和错误。
- OpenAI-compatible 只表示协议入口相似，不代表功能完全兼容；接入前必须跑能力测试。
- Provider 能力不足时明确失败或显式降级，不静默替换用户选择的模型。

### Trace 与 Checkpoint

- `TaskEvent` 追加记录阶段、模型调用、工具调用、候选结果、审批等待、失败和完成事件。
- 用户界面展示行动摘要，不展示或保存模型隐藏思维过程。
- Checkpoint 保存输入版本、已完成工具结果、结构化中间产物、预算用量和待审批状态。
- 只在任务开始、工具成功、候选形成、PatchCandidate 落库和等待审批等安全边界保存。
- 模型调用中断后从上一个安全点重跑；具有副作用的工具必须使用幂等键。

## 8. 验证门槛

正式实现前后都使用同一组基准样例验证：

- GPT、DeepSeek、Kimi 至少各选一个目标模型完成连通性测试。
- 分别验证 Streaming、工具调用、JSON Schema、Usage、取消、超时和错误映射。
- 对比直接 API 与 OpenAI Agents SDK 的冷启动、热启动 TTFT，以及总耗时的 p50/p95。
- 注入 Worker 崩溃，验证能从最近 Checkpoint 重跑且不会重复应用 PatchCandidate。
- 检查 Trace 中不包含 API Key、授权头和未允许保留的原始敏感内容。
- Provider 不兼容时必须向用户显示具体能力缺口，不能静默切换模型。

## 9. 官方参考

- [OpenAI Agents SDK：SDK 模式](https://developers.openai.com/api/docs/guides/agents#build-with-the-sdk)
- [OpenAI Agents SDK：Models and providers](https://developers.openai.com/api/docs/guides/agents/models)
- [OpenAI Agents SDK：生命周期 Hook 与迁移边界](https://developers.openai.com/cookbook/examples/agents_sdk/migrate-from-claude-agent-sdk/readme#what-you-migrate)
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Claude Agent SDK Overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Claude Agent SDK Hosting](https://code.claude.com/docs/en/agent-sdk/hosting)

## 10. 英文术语快速解释

| 术语 | 快速解释 |
|---|---|
| Agent Runtime | 执行模型规划、工具调用和多轮循环的运行层。 |
| Agent Loop | 模型观察当前状态、选择动作、获得结果并继续规划的循环。 |
| BYOK | `Bring Your Own Key`，用户提供自己的模型 API Key。 |
| Provider | 提供模型 API 的厂商或服务平台。 |
| Adapter | 把不同厂商接口转换为 CaseFile 统一接口的适配层。 |
| Trace | 一次 Agent 任务从开始到结束的执行轨迹。 |
| Checkpoint | 任务中断后可从其恢复的安全状态。 |
| Hook | 在模型、工具或任务生命周期特定时刻触发的回调。 |
| Streaming | 模型生成时持续返回增量内容，而不是等待全部完成。 |
| TTFT | `Time To First Token`，请求发出到收到首个输出的时间。 |
| Trade-off | 选择一种方案时必须同时接受的收益、成本和限制。 |
| p50 / p95 | 50% / 95% 请求能达到的延迟分位数，用于观察典型与较差情况。 |
| 幂等 | 同一操作重复执行，不产生重复数据或额外副作用。 |
