# M3.3-06 Closure Repair Shadow Benchmark Report

日期：2026-08-23

分支：`codex/m3-3`

模式：`shadow`（未切换 `suggest`）

## 确定性 Golden Gate

- Suite：`casefile-closure-repair-benchmark-v1`
- 场景：24
- Provider：FakeProvider
- 真实权威链：Simulation → Assessment → Scope → Context → Provider adapter → Repair Engine → Rebase Proof
- Golden contract failure：0
- Safety violation：0
- 三类 Claim、一轮/两轮、scope/protected/StructureLock、DELETE、unknown object/path/value、hard/manual、baseline debt、no progress、cycle、stale、rebase mismatch 均已覆盖

全部零容忍门禁通过：

- scope/protected/StructureLock escape accepted = 0
- hard 或 repair debt 自动授权 = 0
- unknown object/path/value accepted = 0
- 超过两轮、无进展或振荡继续执行 = 0
- rebase mismatch 或未完整证明候选进入 PatchSet = 0

## DeepSeek 真实 Shadow

- Provider / model：DeepSeek / `deepseek-v4-flash`
- 代表性场景：三类 eligible Claim
- 重复次数：每类 3 次，共 9 trial
- Safety violation：0
- Repair success：3/9（33.3%）
- 成功轮数：全部一轮
- Companion operations：5
- Token：input 13,566 / output 1,560 / total 15,126
- 总延迟：21,864.117 ms

三次 dependency 场景均完成完整证明；support/refutation 场景没有成功，候选被服务端以 `repair_proposal_value_invalid` 或 `repair_proposal_noop` 失败关闭。安全门禁通过只证明不安全候选没有越权进入 PatchSet，不代表模型修复质量达到 Suggest 上线标准。

## Release Decision

M3.3-06 代码安全门禁通过。真实 Shadow 的成功率作为观测指标，不参与掩盖任何单次安全失败；当前不启用 `suggest`，不自动 Apply，继续保持 `CLOSURE_REPAIR_MODE=shadow`。M3.3-07 必须等待单独确认后再开始。
