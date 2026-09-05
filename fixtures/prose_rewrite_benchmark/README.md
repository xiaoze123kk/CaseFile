# Prose Rewrite Benchmark

`v1/` 是 N4.5 B2 的公开 development suite，固定 8 个缺陷族 × 3 个 variant = 24 个坏正文任务。每题冻结初始 Writer Render、完整 Checklist lineage、合法 Fidelity-only Judge/Consensus、原问题、此前通过项、关键 check、最多两轮 Fake Rewrite candidate 和逐轮 Gold Evidence。

公开资产只用于零网络协议、评测和报告回归，不证明模型资格。`generate.py` 必须可逐字重建 `suite.json`、review attestation 和 24 个 task asset。

私有 qualification 包位于 `backend/var/benchmark/private/prose-rewrite/qualification-v1/`，不进入 Git。仓库只跟踪 `backend/src/casefile/benchmark/policies/prose-rewrite-qualification-v1-descriptor.json`。独立 reviewer 未完成前，qualification loader 必须在读取私有题和调用 Provider 之前阻断。
