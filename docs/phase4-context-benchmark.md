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

- 完整 `scripts/benchmark-context.ps1` 重跑（含 quick + PG + live）：
  - quick gates：ruff / mypy / compileall / non-PG pytest `409 passed`。
  - PostgreSQL 全量：**480 passed, 7 skipped**；phase2 / phase3 / phase4 rollout gates 全过。
  - baseline（legacy 全量）：**5/5 通过**。
  - rollout（`casefile-chat-context-v2`）：**4/5 通过**（`pass_rate = 0.8`），`compacted_threads = 5/5`。
- 失败样本：`boundary-large-casefile`，失败项 `reference_recall`；线程已正常压缩两次（`state_id` 存在），说明失败不是压缩管线故障。
- 失败分诊：单独复跑该样本（同 key、同模型、同 rollout）得到 **1/1 通过**，答案列出目标事件 `evt_restart_seven` 与目标对象 `ent_researcher` 并成功命中引用。结合基线同任务稳定通过，归因是 **pro 模型在该大卷宗样本上的随机波动（单样本 recall 方差）**，未复现系统性上下文丢失。
- 早前一次全量跑中同一样本曾因 API 余额返回 402 而失败，已不作为质量信号。
- 追加一次 `casefile-chat-context-v3` live 批（同 key、同模型）：
  - baseline（legacy）：**4/5 = 0.8**；`golden-entity-question` 答案正确但 `referenced_object_ids` 为空，属引用槽漏填。
  - rollout（v3）：**3/5 = 0.6**；同一样本仍漏填引用 ID，`boundary-large-casefile` 报 `MaxTurnsExceeded`。
  - 单样本复跑：baseline 报未知引用 `src_restart_brief` 生成失败，rollout 答文中出现 `ent_researcher` 文本但未写入引用 ID（`reference_recall`）。
  - 归因：v3 批未出现系统性上下文丢失，但 pro 模型在 v6 契约下**引用槽填写方差显著**（答对文本但漏填 ID），且大卷宗样本稳定性不足。

## 灰度决策

- 本次有效全量跑未达到《结果级 Eval 方案》Saturation Policy（`pass@1 ≥ 0.95 且 pass^3 ≥ 0.90` 连续两次；v2 `pass_rate = 0.8`，v3 `pass_rate = 0.6`），**v2/v3 不升默认**；默认保持 v1/v4。
- 后续余额允许时补齐多批（每 Task ≥ 3 trials）的 `pass@1 / pass^3`；若连续两批达标再评估是否把 v2/v3 升默认。
- live 报告 row 已增加 `answer_text / referenced_*_ids / expected_*_ids`，便于失败分诊。

## 脚本行为

- `scripts/acceptance-chat-context-v2.ps1` 支持 `-Rollout casefile-chat-context-v1|v2|v3`；live 对比回归或压缩缺失时以退出码 1 结束。
- `scripts/benchmark-context.ps1` 支持 `-LiveModel` 与 `-LiveRollout`；没有 API key 环境变量时跳过 live 并在报告里记录原因；live 子脚本失败会让整个基准失败，不再误报 passed。
