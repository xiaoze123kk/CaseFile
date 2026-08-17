# Phase 4 Context Benchmark（Step 4.3 基线）

生成时间：2026-08-17
基准代码：`feature/casefile-chat-eval-outcome`（Step 4.1 `0854a30` + Step 4.2 `ea87306` + 本 Step 4.3）

## 默认版本冻结

- 默认 rollout：`casefile-chat-context-v1` + Prompt `casefile-chat-v4`（M0/M1 验收后已冻结，Step 4.3 不改变默认）。
- 灰度 rollout（环境变量 `CASEFILE_CHAT_CONTEXT_ROLLOUT`）：
  - `casefile-chat-context-v2` → Prompt `casefile-chat-v5`，Rolling Thread Memory。
  - `casefile-chat-context-v3` → Prompt `casefile-chat-v6` + `casefile-chat-tools-v3`，Dashboard + Context Tools。
  - `agent-focus-v1` → legacy 全量注入，整组回退通道。

## 四档确定性 A/B（`scripts/benchmark-context.ps1`）

同一批五类冻结样本，`var/benchmark/context-tiers-v1.json` 门禁结果：

| tier | peak_input_tokens | total_input_tokens | fallback_count | guardrail_violations |
|---|---|---|---|---|
| legacy_full | 491 | 1225 | 0 | 0 |
| context_v1 | 427 | 989 | 0 | 0 |
| compaction (v2) | 473 | 1173 | 0 | 0 |
| dashboard_tools (v3) | 473 | 1173 | 0 | 0 |

结论：所有已知策略零回退、零护栏违规；峰值与总 token 均不劣于 legacy（v2/v3 的固定 Thread Memory 开销在 100-token 噪声地板内）。

## 已跑通的 Gate

- M0 chat-outcome calibration：30 reference trials 全过。
- Boundary Continuation Eval（M0 门禁）：全过。
- 四档 A/B `--gate`：全过。
- quick gates（ruff / mypy / compileall / non-PostgreSQL pytest）：409 passed。
- PostgreSQL 全量：478 passed, 7 skipped（Step 4.2 提交时）。
- Phase 2 rollout gate（v1）、Phase 3 rollout gate（v2）、Phase 4 rollout gate（v3）：全过，已纳入 `scripts/check.ps1`。

## Live 验收状态

- `scripts/benchmark-context.ps1 -LiveProvider deepseek` 已实现；当前环境未提供 live API key 环境变量时自动跳过（`CASEFILE_CHAT_CONTEXT_LIVE_API_KEY` / `CASEFILE_DEEPSEEK_API_KEY` / `DEEPSEEK_API_KEY`）。
- 提供 key 后执行：`scripts/benchmark-context.ps1 -SkipQuickGates -SkipPostgresGates -LiveProvider deepseek`，再按 `docs/casefile_chat-结果级Eval方案-v1.md` 的 Saturation Policy（`pass@1 ≥ 0.95 且 pass^3 ≥ 0.90` 连续两次）决定是否把 v3 升为默认。
