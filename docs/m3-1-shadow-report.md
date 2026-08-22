# M3.1 v2 Shadow Scan 报告

扫描时间：2026-08-23

Active policy：`logical-mutation-v1`

Shadow policy：`logical-mutation-v2`

阻断：关闭（`blocking_enabled=false`）

## 范围

- 本地开发库共 47 个项目。
- 10 个 Current Draft 已形成合法 CaseFile 并完成双策略扫描。
- 37 个 Draft 返回 `not_ready`，未进入 finding 统计。

## 跨策略差异结果

Shadow API 保留原有 `shadow_only_finding_keys`，并使用 `rule_code + 排序后的 ref_kind/ref_key/role` 识别同一跨策略问题。本次扫描得到：

| 差异类型 | rule_code | active level | shadow level | 数量 | 实现审查归类 |
|---|---|---:|---:|---:|---|
| genuinely new | `missing_evidence_assessment` | — | warning | 2 | 合理提示；竞争矩阵缺少路径范围内的信息格子，不参与 Apply 门禁。 |
| genuinely new | `unscoped_evidence_assessment` | — | warning | 1 | 合理提示；矩阵包含未进入当前路径范围的信息，不参与 Apply 门禁。 |
| promoted | `knowledge_state_available_before_source` | legacy | repair_required | 10 | 确认是既有时间债务，不是规则误报。全部来自 project 46：知识状态在 `evt_t178_001`–`evt_t178_004` 已声明知道 `info_t178_002`、`info_t178_003` 或 `info_t178_005`，但相应信息的 `source_event_ref` 分别为更晚的 `evt_t178_002`、`evt_t178_003`、`evt_t178_005`。 |

- `shadow_promoted_findings`：10 个 `repair_required`。
- `shadow_new_finding_keys`：3 个 warning。
- 新增 `hard_invariant`：0。
- 实现审查中未归类项：0；10 个 promoted 债务仍待产品作者确认。

Shadow `hard_invariant`：0，因此 hard false positive：0。

## 门禁结论

- v1 仍为线上 Apply policy；Shadow 扫描本身未改变 `can_apply`。
- 10 个 `repair_required` 均为 baseline 既有债务，继续按 introduced/worsened grandfather；hard invariant 则默认检查 candidate 全量，仅机械 normalization 显式允许既有 hard。
- Agent 不能授权新增债务；只有作者精确接受全部 finding key 并填写理由后才可继续。
- 因 `repair_required` 尚未完成人类作者确认，本次不执行 v2 policy flip。
- 本报告只证明当前可扫描 Draft 的 Shadow 分类，不替代后续生产样本观察。
