# CaseFile Chat 上下文工程：v3 失败彻查与修复交接

- 交接时间：2026-08-17
- 分支：`feature/casefile-chat-eval-outcome`
- 交接时 HEAD：`d8a800a`
- 目标：修复 `casefile-chat-context-v3` live 失败率，拿到可信 pass@1/pass^3 后再决定是否升默认。

## 1. 背景与当前状态

上下文工程 0–4 阶段代码已全部完成并提交：

| 提交 | 内容 |
|---|---|
| `0854a30` | Step 4.1 Dashboard + Runtime 硬护栏 |
| `ea87306` | Step 4.2 Context Tools v3 + 前端占用指示 |
| `7efa897` / `71a2de0` | Step 4.3 四档 A/B 基准 + 门禁脚本 |
| `b82363d` | live 失败透传、pro 模型参数、v2 灰度结论 |
| `d1b769f` | live 报告补 answer/reference 明细 |
| `d8a800a` | live 验收支持 v3 rollout + v3 跑批归因 |

当前默认冻结：

- 默认 rollout：`casefile-chat-context-v1` + Prompt `casefile-chat-v4`
- v2：`casefile-chat-context-v2` + `casefile-chat-v5`（live 4/5）
- v3：`casefile-chat-context-v3` + `casefile-chat-v6` + `casefile-chat-tools-v3`
- legacy 回退：`agent-focus-v1` + `casefile-chat-v3`

已知确定性门禁全过：ruff / mypy / compileall / non-PG `409 passed` / PostgreSQL `480 passed, 7 skipped` / phase2+phase3+phase4 rollout gates。

## 2. 问题：v3 live 失败率

Provider：DeepSeek `deepseek-v4-pro`。v3 全量批：

| arm | pass_rate | 失败 |
|---|---|---|
| baseline（legacy） | 4/5 = 0.8 | `golden-entity-question` 答案正文正确但 `referenced_object_ids=[]` |
| rollout（v3） | 3/5 = 0.6 | 同一引用漏填；`boundary-large-casefile` 报 `MaxTurnsExceeded` |

boundary 单样本复跑：

- baseline：模型编造 `src_restart_brief` 等未知引用 ID → runtime 拒绝 → `generation_failed`
- rollout：正文列出目标对象/事件，但 `referenced_object_ids` 漏了 `ent_researcher` → Grader `reference_recall`

关键结论：**v3 压缩管线正常**（完成线程都有 `context.compacted`）；失败是模型输出契约方差被现有校验/Grader 放大，同时 baseline 也波动（0.8），不能拿单批 0.6 直接判定 v3 比 v2 差。

## 3. 失败模式与根因

### F1 引用槽漏填（最高频）
- 提示词已要求"实质讨论的每个对象写入 `referenced_object_ids`"，但模型多次输出正文正确 + 引用数组为空。
- Grader 只统计引用数组（这是正确设计，不要放水）。
- 根因：规则缺少强约束、正负例、最终自检步骤；模型在大卷宗样本方差明显。

### F2 `MaxTurnsExceeded`
- v3 `question` 路由 `max_turns=4`、`max_tool_calls=6`。
- v6 新增"拿不准先读证据/`retrieve_thread_evidence`"规则后，大卷宗枚举题工具轮次更容易耗尽 4 turns。
- SDK `Runner` 到 max_turns 直接抛错，任务表现为 `generation_failed`。

### F3 编造引用 ID
- 模型把 source 内部 ID（`src_*`）当 CaseFile 对象 ID。
- `workflow_service.py` 校验拒绝是正确行为，但发生在 Provider 返回后且无一次带错误信息的重试机会，任务直接失败。

### F4 实验协议噪声
- 每 task 只跑 1 trial；baseline/rollout 先后固定；模型温度未固定。
- 同模型两次跑批 baseline 1.0 → 0.8，说明单批 pass_rate 不足以判定策略差异。

## 4. 相关代码位置

| 关注点 | 文件 | 位置 |
|---|---|---|
| 路由轮次/工具预算 | `backend/src/casefile/agent_runtime/chat_routing.py` | `EXECUTION_PROFILES` 约 L28-104；question `max_turns=4` |
| v6 引用/工具规则 | `backend/src/casefile/agent_runtime/prompts/casefile_chat/v6/shared.md` | L29-37 引用规则，L17-27 工具规则 |
| 工具 runtime | `backend/src/casefile/agent_runtime/providers.py` | `_chat_tool_runtime` L505；`_run_auxiliary_agent` L1992；max_turns 使用约 L2143/L2155 |
| DeepSeek 模型设置 | `backend/src/casefile/agent_runtime/providers.py` | `_deepseek_model_settings` L3375（目前无 temperature） |
| 未知引用校验 | `backend/src/casefile/application/workflow_service.py` | 约 L759-793 |
| Grader reference_recall | `backend/src/casefile/benchmark/chat_outcome_eval.py` | 约 L436-472（只看 `referenced_*_ids`，不看正文，保持） |
| live 测试 | `backend/tests/integration/test_chat_context_phase3_live_acceptance.py` | 支持 v2/v3；失败行已有 answer/refs |
| live 脚本 | `scripts/acceptance-chat-context-v2.ps1` | `-Rollout` 支持 v1/v2/v3；失败 exit 1 |
| 总基准脚本 | `scripts/benchmark-context.ps1` | `-LiveProvider` / `-LiveModel` / `-LiveRollout` |
| 基线/归因文档 | `docs/phase4-context-benchmark.md` | 跑批结果与灰度决策记录 |

## 5. 修复方案与实施顺序

### P0-1 新建 Prompt `casefile-chat-v7`（模型侧硬约束）

在 `backend/src/casefile/agent_runtime/prompts/casefile_chat/v7/` 以 v6 为底创建：

1. 引用自检步骤：最终输出前逐项核对正文实质讨论对象/事件是否都已进入对应引用槽。
2. 正负例：
   - 正例：正文实质回答"林研究员负责什么" → `referenced_object_ids` 必须包含 `ent_researcher`。
   - 负例：仅顺带提及 → 不填；`src_*` / `clm_*` 等内部 ID 一律禁止。
3. ID 白名单：只能来自 `casefile.records`、`focus_objects`、工具结果；禁止 source/brief 内部 ID。
4. 工具预算：question 路由必须最迟第 3 轮给出最终答案；大卷宗枚举题优先 `get_related_objects(事件ID)` 一次展开，而不是逐对象读全文。
5. 不要原地改 v6（v6 已用于 v3 任务且要保留回放）。

配套改动：
- `backend/src/casefile/agent_runtime/context/assembly_render.py` 或对应版本常量：新增 `CHAT_CONTEXT_PROMPT_V4_VERSION = "casefile-chat-v7"`（按现有 V2/V3 常量结构命名，注意当前 V3 是 v6）。
- `backend/src/casefile/application/workflow_service.py` `_chat_context_policy_version()` 与 `_new_task`：v3 策略配对 v7 + `casefile-chat-tools-v3`。
- Prompt manifest：`previous = casefile-chat-v6`，runtime toolset `casefile-chat-tools-v3`，tool policy 与 v6 一致。
- 更新 `backend/tests/unit/test_prompt_repository.py` 期望 hash。

### P0-2 Runtime 保守引用补全（确定性安全网）

位置：`workflow_service.py` candidate 落库前（`persist_*` / 结果构造附近），或 provider 返回后、未知引用校验前。

规则：
- 仅当 `referenced_object_ids` / `referenced_event_ids` 缺失时检查。
- 数据源只允许：当前 `focus_objects` + `casefile.records` 的 `id` / `name` / `title`。
- 正文必须出现对象名/事件标题且**唯一匹配**才补 ID；有歧义不补。
- 绝不删除引用、绝不根据正文猜 ID。
- 建议环境开关：`CASEFILE_CHAT_REFERENCE_AUTOFILL`（默认关；v3 rollout 先开 1 验证）。
- 补全后记事件：`context.reference_autofilled`，把补全的 `object_ids/event_ids` 放进 payload，便于观察。

预期消除 F1。

### P0-3 未知引用的受控重试

位置：`workflow_service.py` 未知引用校验（约 L759-793）与 worker/provider 调用链。

规则：
- 检测到 unknown references 时，不立即失败，而是把错误信息 + "允许的 ID 只来自当前 casefile" 回注 provider，做最多 1 次 repair call。
- 第二次仍失败则失败，并在 error_details 里记录 `repair_attempted=true`。
- 如 provider 层改动太大，先做 P0-1/P0-2 再评估是否还需要 P0-3。

预期消除 F3。

### P1-1 question 路由轮次预算

- `chat_routing.py`：question `max_turns` 4 → 6；`max_tool_calls=6` 保持不变。
- 运行全量非 PG 测试与 M0/M1 gates，观察峰值/总 token 不超 A/B 报告中的 tier allowance。
- 预期消除 F2；成本由 `max_tool_calls` 和 input 硬上限继续兜底。

### P1-2 Live 验收固定温度

- `_deepseek_model_settings()` 增加可选 temperature；live 测试支持 `CASEFILE_CHAT_CONTEXT_LIVE_TEMPERATURE=0`（默认 live 0）。
- OpenAI arm 同样处理（OpenAI Responses temperature=0）。
- 目的：pass@1/pass^3 测策略差异，不是采样波动。

### P2 Live 协议升级

- `scripts/acceptance-chat-context-v2.ps1` 支持 `-LiveTrials 3`：每 task 跑 3 trials，baseline/rollout 按 task 配对交错。
- live 报告失败行继续保留 `answer_text / referenced_*_ids / expected_*_ids`，新增 `task_usage_jsonb` 与 `error_details_jsonb`。
- 计算 pass@1（任一 trial 过）与 pass^3（3 trials 全过）。
- 验收口径不变：Saturation Policy 要求连续两批 `pass@1 ≥ 0.95 且 pass^3 ≥ 0.90`。

## 6. 实施后验证命令

全部在 repo 根目录执行。先加载 `.env` 与 User 级 DeepSeek key：

```powershell
$lines = Get-Content .env
foreach ($line in $lines) {
  $t = $line.Trim()
  if ($t -and -not $t.StartsWith('#') -and $t -match '=') {
    $p = $t.Split('=', 2)
    Set-Item -Path ("Env:" + $p[0]) -Value $p[1]
  }
}
$key = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
if ($null -eq $key) { throw 'DEEPSEEK_API_KEY missing' }
Set-Item Env:DEEPSEEK_API_KEY -Value $key
```

快速门禁：

```powershell
.\backend\.venv\Scripts\python.exe -m ruff check --config backend\pyproject.toml backend\src backend\migrations backend\tests
.\backend\.venv\Scripts\python.exe -m mypy --config-file backend\pyproject.toml backend\src
.\backend\.venv\Scripts\python.exe -m pytest -m "not postgres" -q
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -SkipPostgres
```

PostgreSQL 全量 + rollout gates：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\benchmark-context.ps1 -SkipLive
```

v3 live（单批）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\acceptance-chat-context-v2.ps1 `
  -SkipQuickGates -SkipM1Gate `
  -LiveProvider deepseek -LiveModel deepseek-v4-pro `
  -Rollout casefile-chat-context-v3 `
  -ReportPath tmp\chat-context-v3-acceptance-summary.json
```

单任务分诊（例如 boundary-large-casefile）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\acceptance-chat-context-v2.ps1 `
  -SkipQuickGates -SkipM1Gate `
  -LiveProvider deepseek -LiveModel deepseek-v4-pro `
  -Rollout casefile-chat-context-v3 `
  -LiveTaskIds boundary-large-casefile `
  -ReportPath tmp\chat-context-v3-boundary-summary.json
```

报告位置（每次跑批覆盖，注意先备份）：

- `tmp/chat-context-v3-live-baseline.json`
- `tmp/chat-context-v3-live-rollout.json`
- `tmp/chat-context-v3-acceptance-summary.json`

## 7. 验收标准

1. 所有确定性门禁保持全绿（ruff / mypy / compileall / non-PG / PG 480 / M0 / Boundary / 四档 A/B / phase2-4 gates）。
2. F1/F2/F3 任一模式在 v3 live 中不再出现，或失败行能自动归因到 agent/grader/task。
3. 连续两批 `pass@1 ≥ 0.95 且 pass^3 ≥ 0.90` 才允许把 v3 升默认；否则保持默认 v1/v4。
4. 任何一次 live 回归（rollout < baseline）或 compacted_threads < task_count，脚本必须 exit 1。

## 8. 约束与注意事项

- **不修改** v1–v6 prompt 文件；修复走 v7 新版本。
- **不放松** Grader：正文提到但 refs 空仍算 recall 失败，安全网只做保守补全。
- **不给模型删除权**：`request_thread_compaction` 仍是请求，`retrieve_thread_evidence` 仍只读。
- PowerShell 脚本：无 BOM、可执行字符串 ASCII only；PowerShell 5.1 下脚本调用不要用数组 splat 传命名参数（直接写参数）。
- 提交前用：
  ```powershell
  [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
  git diff --check
  ```
- 不要提交：`docs/.obsidian/workspace.json`、`backend/uv.lock`、`tmp/`、`backend/var/`。
- `docs/` 被 .gitignore 忽略；本文件这类新 docs 需要 `git add -f <file>` 单独提交。

## 9. 建议提交顺序

1. `feat: 新增 casefile-chat-v7 并配对 context-v3`
2. `feat: chat 引用保守自动补全与事件观测`
3. `feat: question 路由轮次预算与 live 温度控制`
4. `feat: live 验收多 trial 配对协议`
5. `docs: v3 修复跑批结果与灰度决策`
