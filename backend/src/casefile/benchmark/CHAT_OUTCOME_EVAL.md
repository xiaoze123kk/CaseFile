# CaseFile Chat Outcome Eval

结果级 Eval Suite v1（WA-18）。术语按 Task / Trial / Grader / Transcript /
Outcome 划分，门禁只信任确定性 Grader 与落库事实。

## Modes

| 模式 | 命令 | 说明 |
|---|---|---|
| M0 校准 | `python -m casefile.benchmark chat-outcome --mode calibrate` | 当前 35 条 Reference Solution 全过 + 动态变异集全抓；已进 `scripts/check.ps1` |
| M1 Canned Outcome | `pytest tests/integration/test_chat_outcome_canned.py` | 走真实 `send_agent_message → Worker → complete_chat_task`，对落库 Outcome 打分；已进 `scripts/check.ps1` |
| M2 Live | `python -m casefile.benchmark chat-outcome --mode live --provider deepseek --trials 3 --database-url ...` | 真实模型跑批，输出 `pass@1 / pass^3 / safety_pass^3` |
| L3 反馈 | `python -m casefile.benchmark chat-feedback --database-url ...` | 只读聚合采纳/拒绝/撤销/stale/采纳后重写率 |
| 失败分诊 | `python -m casefile.benchmark.chat_outcome_triage --report report.json` | 按失败签名分组，指示先看哪些 Transcript |

当前 T1 Suite 为 35 Task。M0/M2 报告必须保存 `suite_task_count`、实际选中的
`task_ids`、`prompt_versions`、`toolset_versions` 和 `suite_fingerprint`；子集诊断不能
替代完整 35×3 发布门禁。

M2 默认冻结 `casefile-chat-v13`；如需重放历史基线，必须显式传入
`--prompt-version casefile-chat-v12`。使用 `--report-path <path> --resume` 可从 `<path>.partial.json` 续跑。Runner 每个
Trial 完成后原子更新 checkpoint，并输出精确 `completed/total` 进度；续跑只接受 Suite、
模型、Trial 数和冻结输入完全一致的 fingerprint。中断报告不保存凭据、URL、请求正文或
Provider 原始响应。

## Failure triage

每个失败 Trial 必须打开 Transcript 后三选一：

- `agent_error`：模型真错 → 修 Prompt / 路由 / 工具循环；
- `grader_error`：Grader 误拒正确答案 → 修 Grader，并回灌 Reference Solution；
- `task_error`：Task 歧义或不可完成 → 修 Task。

分诊器先按 `error_kind/error_code` 区分 transport、timeout、max_turns、tool、protocol、
output/completion validation，再处理能力失败。确定性安全失败（blank / forbidden /
unnecessary / draft change）可直接判
`agent_error`；capability 失败先与 `grader_error` 交叉排查。

## Saturation policy

- 当连续两次 M2 Live 达到 `pass@1 >= 0.95` 且 `pass^3 >= 0.90`：
  1. 把 `chat_outcome_t2.build_t2_tasks()` 中的 5 条 T2 任务升入 T1；
  2. 从 `router.feedback` 导出、L3 `rejected/stale/rewrite_after` 样本和线上排障记录补新 T2。
- T1 全绿只代表防回归，不代表能力继续提升；难度层级必须持续前移。
