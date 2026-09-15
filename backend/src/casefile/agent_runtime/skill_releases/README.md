# 小说 Skill 候选发布与成本冒烟

当前候选为 `prose-writer-v6` / `prose-rewriter-v9`；首轮分层试验 v5/v8 保留不改写。
默认 Registry、旧任务版本与
业务 API 不变。内部 `build_prose_*_request` / `execute_prose_*` 的 `prompt_version`
参数显式选择候选。产品是否启用需单独发布，不由成本冒烟自动切换。

## 职责与组装

- `prompts/` 内角色指令继续由 PromptRepository 校验，旧发布不改写。
- 本目录每个发布包含 `SKILL.md` 与不可变资源清单，按阶段加载；首期没有额外的模型选技能调用。
- `skill_resources.py` 提供公共资源哈希、路径与描述读取，v17 沿用原阶段与 Hook 策略。
- `prose_skills.py` 组装角色、JSON Schema、Skill、任务数据、修复反馈；独立调用始终携带所需材料。
- `prose_model_view.py` 在 v6/v9 模型视图中移除已知编译器结构的追踪哈希，将对象目录等
  数据放在变化更频繁的绑定之前。原始请求不修改，正文、原文对象、检查项和评审意见不裁剪。
- 资源、角色与 Schema 不匹配时在请求发出前失败。Skill 版本、加载原因和稳定前缀哈希
  留在请求/结果元数据，不因追踪需要注入模型。
- 服务端继续负责结构、协议、引用、预算与证据绑定；文学判断与修订由模型完成。

## 缓存与用量

相同前缀只代表具备复用条件。真实命中以供应商 `usage` 为准；不声称清空远端缓存，
首次调用标为首次观测。`usage_details` 保留原始响应，未知字段为 null，独立于兼容旧调用方的
整数计数。成本报告按 token 加权统计缓存命中率，并报告用量覆盖率；未知费用不当作零费用。

不设置固定缓存命中率门槛。首次观测与重复请求单独报告，重复相同请求的数据不能称为
小说链路稳定运行命中率。不同场景和不同 Agent 的前缀复用需真实工作负载验证，
不能通过增加重复次数抬高命中率。主要比较实际输入 token、输出 token 与调用次数，
跨运行费用对比还必须说明已有缓存状态的差异。

## 冒烟入口

从仓库根目录执行（输出目录必须尚不存在）：

```powershell
./scripts/prose-cost-smoke.ps1 -Mode Fake -OutputDir backend/var/benchmark/prose-cost/my-offline
./scripts/prose-cost-smoke.ps1 -Mode Live -OutputDir backend/var/benchmark/prose-cost/my-live -Prices /absolute/path/prices.json -BudgetCny 20
```

Live 读取 `CASEFILE_DEEPSEEK_API_KEY` 或 `DEEPSEEK_API_KEY`，不将密钥写入产物。
价格 JSON 必须包含 `model_id`、`verified_at`、`source`、`cached_per_million`、
`uncached_per_million`、`output_per_million`，单位为人民币/百万 token，高峰价格用于预留。
每次运行前核对官方价格，不复用过期价格。模型必须与当前运行时一致；禁止隐式切换。
本次用户指定跟随 Flash，后续模型变化应重新核对并冻结对应价格。

运行固定三个公开样本，旧/新版本各两次，最多一次传输重试，不启动付费 Judge。
历史修订 fixture 单独验证并保留来源，不改变其旧套件门禁，不构成正式资格评测。

总预算上限为 20 元。串行调用前按 UTF-8 字节保守上界、全部输入未命中和最大输出预留，
预留和 pending 记录先落盘。失败或缺失用量保留预留，无法容纳下一调用时停止。
`budget_charged_upper_cny` 是预算保守记账；`estimated_cost_cny` 按实际 token 与峰谷时段估算，
两者均不是供应商账单。跨时段调用按高峰上界估计。中断不自动续跑，现有输出目录禁止覆盖。

产物包括冻结输入/请求/价格的 `manifest.json` 与逐尝试 `report.json`；报告列出未完成配对、
结构失败、首次/重复观测、模型 ID、缓存、输入、输出和费用。正文人工对照单独记录，
不得从结构通过推断文学质量通过。
