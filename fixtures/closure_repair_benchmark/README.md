# Closure Repair Shadow Benchmark

`v1-scenarios.json` 是 M3.3 Closure Repair 的冻结 Golden 契约。每个场景必须声明唯一 ID、输入 setup、Provider proposal 行为、精确终态/原因/轮数以及安全标签；套件不得少于 20 个场景。

确定性发布门禁：

```powershell
uv run --project backend python -m casefile.benchmark closure-repair --provider fake
```

该命令执行真实的 Simulation、Assessment、Scope、Context、Provider adapter、Repair Engine 与 Rebase Proof 链。报告逐 trial 检查安全违规，全部 trial 必须同时通过；`pass@k` 或重复试验中的任意一次成功不能掩盖安全失败。

真实 Shadow 是显式 opt-in，只运行三类 eligible Claim 代表场景，不改变 PatchSet 或 Apply：

```powershell
uv run --project backend python -m casefile.benchmark closure-repair `
  --provider openai --model <model-id> --trials 3 --live `
  --report-path var/benchmark/closure-repair-shadow-openai.json
```

Provider 可换为 `deepseek`。必须通过参数或对应环境变量提供凭据；缺少 `--live`、凭据或 Provider 绑定不一致时失败关闭。真实结果只记录当前单次采样的安全门禁、修复率、单/双轮比例、operation 数、token 与延迟，不自动切换 `CLOSURE_REPAIR_MODE=suggest`。
