# Phase 4 Context Benchmark（Step 4.3 基线）

生成时间：2026-08-17
基准代码：`feature/casefile-chat-eval-outcome`（Step 4.1 `0854a30` + Step 4.2 `ea87306` + Step 4.3 `7efa897`/`71a2de0`）

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

## Live 验收（DeepSeek deepseek-v4-pro，2026-08-17）

- baseline（legacy 全量）：**5/5 通过**。
- rollout（`casefile-chat-context-v2`）：**4/5 通过**（`pass_rate = 0.8`）。
- 失败样本：`boundary-large-casefile`，任务状态 `failed / generation_failed`，错误 `Max turns (4) exceeded`。
- 失败分诊：单独复跑该任务时 rollout 得到 HTTP **402 Insufficient Balance**；结合首次全量跑只有该样本失败、其余 4 个 rollout 线程均正常压缩并落库，归因是 **provider 计费/重试（SDK 4 次重试后包装为 MaxTurnsExceeded）**，不是上下文丢失。
- 由于 API key 余额耗尽，目前无法取得有效的 pass@1/pass^3 双批数据。

## 灰度决策

- 未达到《结果级 Eval 方案》Saturation Policy（`pass@1 ≥ 0.95 且 pass^3 ≥ 0.90` 连续两次），**v2/v3 不升默认**；默认保持 v1/v4。
- 余额恢复后补齐：`scripts/benchmark-context.ps1 -SkipQuickGates -SkipPostgresGates -LiveProvider deepseek -LiveModel deepseek-v4-pro`，连续两次达标后再评估是否把 v2/v3 升默认。

## 脚本行为

- `scripts/acceptance-chat-context-v2.ps1` 在 live 对比回归或压缩缺失时以退出码 1 结束。
- `scripts/benchmark-context.ps1` 支持 `-LiveModel`；没有 API key 环境变量时跳过 live 并在报告里记录原因；live 子脚本失败会让整个基准失败，不再误报 passed。
