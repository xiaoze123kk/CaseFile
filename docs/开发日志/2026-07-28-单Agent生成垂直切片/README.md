# 2026-07-28 单 Agent 生成垂直切片

## 目标

打通从用户结构化建案到可编辑 CaseFile v1 工作台的第一条真实闭环：用户配置模型与 API Key、填写并确认 Brief、创建生成任务、查看安全执行轨迹、由单 Agent 生成并验证结构化 Draft、原子写入 Snapshot，最后由用户确认进入工作台。

## 范围

- 以根目录 `contracts/schemas/` 作为 CaseFile v1、Brief、Task 与编辑契约的唯一人工维护源，并生成 Python/TypeScript/runtime 镜像。
- PostgreSQL 业务表从 28 张扩展到 37 张，新增用户 Provider 设置、Brief/BriefVersion、Relationship、StructureLock、v1 契约引用和 TaskRun/Attempt/Event。
- 用户级 API Key 使用 AES-256-GCM 密文存储；接口只返回后四位和验证状态，不支持读取明文。
- 实现单 Agent、Agents SDK 工具循环、版本化 Prompt/Agent/Toolset、结构化输出、有限结构修复和分层 Validator 基础。
- 使用 PostgreSQL `FOR UPDATE SKIP LOCKED`、lease、Attempt 和 TaskEvent 作为任务队列及恢复机制，不引入独立 MQ。
- 通过 SSE 展示阶段、工具摘要、Validator 与用量，不展示或持久化隐藏思维链。
- 真实前端实现五字段建案、Brief 冻结、任务进度、完成门禁、12 类对象展示，以及 Entity、Location、Event 的有限字段编辑。
- 旧原型完整迁移到 `/demo/*`，真实模式与演示模式状态和导航完全隔离。
- Benchmark 覆盖结构有效率、修复次数、延迟、工具调用有效率、工具执行成功率与结果采纳率；Fake Provider 用于零费用联调和可重复测试。

## 关键决策

- 当前只使用单 Agent，不实现 Agent handoff；Agent 内部通过 SDK 工具循环完成规划、生成和校验。
- 默认真实模型为 `gpt-5.6-sol`，模型与 API Key 由左下角设置弹窗按用户配置；Prompt、Agent、Toolset 和 Provider 配置版本都固化到 TaskRun。
- 暂不实现登录认证，开发闭环使用用户 ID `1`；后续认证计划采用 JWT，并由独立工作负责。
- Brief 草稿可修改，只有用户确认后的 BriefVersion 才能作为生成输入；确认版本和任务事件只追加。
- Draft 必须为空才能执行全量生成；一次成功生成只追加一个 `agent_generate_from_brief` Operation 和一个 Snapshot。
- 候选 JSON、规范化表投影回的 Snapshot JSON 与 RFC 8785 哈希必须一致，失败时整笔事务回滚。
- Validator 分层：确定性 Schema/引用/语义规则先执行，Agent 通过工具消费校验结果；更强的独立语义判断层后续扩展。
- 不把数据库表机械做成单一 JSON 文档；字段尽量对应 v1 契约，身份、归属、状态、租约和引用使用关系结构，完整 JSON 仅用于不可变 Snapshot/Canon。
- 数据库结构不是永久冻结；已共享迁移不得重写，后续调整必须通过新迁移并同步 ORM、契约映射、测试和职责文档。

## 遗留事项

- 登录、JWT、用户隔离的真实身份注入和密钥轮换运营流程尚未实现。
- 独立语义 Validator Agent、人工复核队列和更细的分层判定证据仍需设计。
- TaskRun 已具备取消、重试和 lease 数据字段，但用户级取消/重试交互与运维监控仍不完整。
- 当前真实工作台只允许 Entity、Location、Event 的有限字段编辑；其余对象仍为只读展示。
- Benchmark 只有最小 `brief_to_draft` Fixture，尚不足以评估题材多样性、长上下文、成本上限和模型版本回归。
- Simulation、Compiler、Asset/Import、Validator Report、预算账本和正式发布流程仍未实现。

## 下一阶段建议

1. 接入 JWT 认证与真实当前用户上下文，完成用户级 Provider 设置、Project、SSE 和 Worker 的端到端隔离测试。
2. 把 Validator 拆成确定性门禁、语义判断 Agent 和人工复核三层，并定义可审计的 ValidationIssue/PatchCandidate 落库策略。
3. 完成任务取消、显式重试、失败恢复、用量/成本上限与运维可观测性，再验证多 Worker 并发。
4. 扩充 Benchmark 数据集和模型回归基线，覆盖多题材、边界输入、工具失败、结构修复、结果采纳率和成本。
5. 按对象优先级逐步开放其余 v1 类型的有限编辑，保持契约 round-trip、revision 冲突和只读边界测试。
