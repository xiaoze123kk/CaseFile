# Closure Repair Benchmark v2

本目录同时维护两个用途严格分离的套件：

- `v1-scenarios.json`：24 个 FakeProvider Regression/Safety Golden，CI 逐 Trial 精确检查终态、原因、轮数和零安全违规。
- `capability/v1/`：61 个 Task 的 Capability Suite，覆盖 `closure-repair-v1` 全部 52 个 `(rule_code, closure_level)`。12 个 agent-eligible Task 评价真实修复能力，49 个 manual/ineligible Task 只评价正确拒绝，不进入能力分母。

Capability Task 的 `input` 与 `oracle` 严格分离；`oracle` 和 Reference 不进入 Provider 请求。每个 Task 都绑定独立 Reference，收录前必须通过 Reference Validation。`documents/base.json` 是冻结输入基线，`policy-catalog.json` 冻结 49 个拒绝任务所需的 finding contract。

`v1-scenarios.json` 是 M3.3 Closure Repair 的冻结 Golden 契约。每个场景必须声明唯一 ID、输入 setup、Provider proposal 行为、精确终态/原因/轮数以及安全标签；套件不得少于 20 个场景。

确定性发布门禁：

```powershell
uv run --project backend python -m casefile.benchmark closure-repair --provider fake
```

该命令执行真实的 Simulation、Assessment、Scope、Context、Provider adapter、Repair Engine 与 Rebase Proof 链。报告逐 trial 检查安全违规，全部 trial 必须同时通过；`pass@k` 或重复试验中的任意一次成功不能掩盖安全失败。

旧 live Shadow 仍可显式运行三类代表场景，不改变 PatchSet 或 Apply：

```powershell
uv run --project backend python -m casefile.benchmark closure-repair `
  --provider openai --model <model-id> --trials 3 --live `
  --report-path var/benchmark/closure-repair-shadow-openai.json
```

Provider 可换为 `deepseek`。必须通过参数或对应环境变量提供凭据；缺少 `--live`、凭据或 Provider 绑定不一致时失败关闭。真实结果只记录当前单次采样的安全门禁、修复率、单/双轮比例、operation 数、token 与延迟，不自动切换 `CLOSURE_REPAIR_MODE=suggest`。

正式 Capability 基线只支持 DeepSeek，开发冒烟用 `--trials 1`，正式基线用 `--trials 3`：

```powershell
uv run --project backend python -m casefile.benchmark closure-repair `
  --suite capability --provider deepseek --model <model-id> --trials 3 --live `
  --report-path var/benchmark/closure-repair-capability-deepseek.json
```

当前 Repair Agent 使用 `closure-repair-context-v2` 与 `closure-repair-output-v2`：服务端冻结 `allowed_writes/value_schema`，模型直接输出强类型 `value`，领域层仍独立执行 Scope、Simulation 与 Rebase Proof。需要和历史 v1 基线做受控实验比较时增加 `--baseline-report <v1-report.json>`；严格 `comparison_fingerprint` 仍只用于完全相同 contract/harness 的重复运行。

Suite Report 旁会生成逐 Trial artifact 目录。Capability 首版不设能力发布阈值；任何 SafetyGrader 违规仍使报告失败。报告明确标记为 `production_kernel`，不验证 API、Worker、PostgreSQL、lease、SSE 或 Apply/Undo/Redo，因此不能单独作为完整生产发布证据。
