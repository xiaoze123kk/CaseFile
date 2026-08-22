# M3.1 v2 Shadow Scan 报告

扫描时间：2026-08-23

Active policy：`logical-mutation-v2`

Shadow policy：`logical-mutation-v2`

阻断：开启（`blocking_enabled=true`）

## 范围

- 本地开发库共 47 个项目。
- 10 个 Current Draft 已形成合法 CaseFile 并完成双策略扫描。
- 37 个 Draft 返回 `not_ready`，未进入 finding 统计。

## Pre-flip 跨策略差异结果

Shadow API 保留原有 `shadow_only_finding_keys`，并使用 `rule_code + 排序后的 ref_kind/ref_key/role` 识别同一跨策略问题。本次扫描得到：

| 差异类型 | rule_code | active level | shadow level | 数量 | 实现审查归类 |
|---|---|---:|---:|---:|---|
| genuinely new | `missing_evidence_assessment` | — | warning | 2 | 合理提示；竞争矩阵缺少路径范围内的信息格子，不参与 Apply 门禁。 |
| genuinely new | `unscoped_evidence_assessment` | — | warning | 1 | 合理提示；矩阵包含未进入当前路径范围的信息，不参与 Apply 门禁。 |
| promoted | `knowledge_state_available_before_source` | legacy | repair_required | 10 | 确认是既有时间债务，不是规则误报。全部来自 project 46：知识状态在 `evt_t178_001`–`evt_t178_004` 已声明知道 `info_t178_002`、`info_t178_003` 或 `info_t178_005`，但相应信息的 `source_event_ref` 分别为更晚的 `evt_t178_002`、`evt_t178_003`、`evt_t178_005`。 |

- `shadow_promoted_findings`：10 个 `repair_required`。
- `shadow_new_finding_keys`：3 个 warning。
- 新增 `hard_invariant`：0。
- genuinely-new `repair_required`：0。
- 实现审查中未归类项：0。

M3.1-07.6 增加 `reasoning_required_path_incompatible_claim_input` 后以同一范围重扫，
没有发现使用不兼容 Claim 输入的既有 required ReasoningPath，因此未引入新的 rollout
债务或 hard finding。

Shadow `hard_invariant`：0，因此 hard false positive：0。

## 门禁结论

- v1 仍为线上 Apply policy；Shadow 扫描本身未改变 `can_apply`。
- 10 个 `repair_required` 均为 baseline 既有债务，继续按 introduced/worsened grandfather；hard invariant 则默认检查 candidate 全量，仅机械 normalization 显式允许既有 hard。
- Agent 不能授权新增债务；只有作者精确接受全部 finding key 并填写理由后才可继续。
- Project 46 的 10 个 `knowledge_state_available_before_source` 被接受为 pre-v2
  baseline debt，不作为 M3.1 activation blocker；这是 rollout 决策，不修改或替作者
  接受具体 CaseFile 内容。
- 完整仓库门禁通过：653 passed、9 skipped；额外 Context v2 验收 2 passed，Context
  v3 验收 1 passed、3 skipped。
- 本次 pre-flip Go Gate 通过，可以在 M3.1 代码合并且 Active 仍保持 v1 后，从新的
  `main` 创建独立 rollout 分支执行 v2 activation。
- 本报告只证明当前可扫描 Draft 的 Shadow 分类，不替代后续生产样本观察。

## Post-flip 验收

M3.1-08 将 Active 从 `logical-mutation-v1` 切换为 `logical-mutation-v2`，Shadow
继续使用 v2。相同 47 个项目的只读扫描结果为：

- 10 个 Current Draft `completed`，37 个 Draft 因 `brief_version_missing`
  返回 `not_ready`。
- 所有结果均为 `active_policy=logical-mutation-v2`、
  `shadow_policy=logical-mutation-v2`、`blocking_enabled=true`。
- Active 与 Shadow 使用相同策略，因此 shadow-only、new 和 promoted 差异均为 0。
- 当前 v2 finding 总量为 16 个 hard、12 个 repair_required、3 个 warning。

16 个 hard 均为 pre-flip 已存在且 v1 同样检查的真实问题，不是 v2 新增或 false
positive：Project 2、24、34、35、47 共 14 个 Evidence/Claim 双向投影不一致；
Project 46 有 2 个 Claim 自依赖循环。Hard invariant 不 grandfather，因此这些 Draft
在修复 hard 问题前继续拒绝无关 Apply，行为与 M3.1-07.5 后的 v1 Active 一致。

Project 46 的 10 个 knowledge timing debt 本身已通过隔离 smoke：只复制这些
`knowledge_state_available_before_source` baseline debt 后执行无关 UPDATE，v2
`can_apply=true` 且无需作者授权。直接对 Project 46 整稿做同一只读 simulation 会被
上述 2 个既有 Claim 自依赖 hard invariant 阻断；这不改变 knowledge debt 的 rollout
决策，也不授权系统修改用户内容。

## Policy flip smoke

在隔离 `_test` 数据库与纯 simulation 中完成：

1. 普通无关 UPDATE：v2 `can_apply=true`。
2. 新建 `temporal_exclusivity_violation`：Agent blocked。
3. 同一 mutation 由作者精确授权：apply。
4. 新建 reasoning hard invariant：作者也不能授权。
5. 既有 knowledge timing debt + 无关 UPDATE：grandfather 并 apply。
6. 旧 v1 pending PatchSet：`closure_policy_version_stale`。
7. 旧 v1 PatchSet Undo：在 v2 下重新证明，并记录 source policy v1。
8. 旧 v1 PatchSet Redo：`agent_patch_redo_policy_stale`。

Post-flip 完整仓库门禁通过：654 passed、9 skipped；额外 Context v2 验收 2
passed，Context v3 验收 1 passed、3 skipped。回滚只需 revert M3.1-08 activation
提交，即恢复 Active=v1；不需要回滚 M3.1 规则实现或改写 Draft。

## 封板结论

M3.1 — Evidence & Reasoning Closure 完成。后续不增加 M3.1.9 或 M3.1.10；
M3.2 History 与 M3.3 Closure Repair Agent 另行设计。
