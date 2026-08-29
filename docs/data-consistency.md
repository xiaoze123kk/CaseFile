# 数据一致性规范

本文涵盖所有数据库表的设计和操作必须遵守的一致性规则。

## 归属与所有权

- `projects.owner_user_id` 是唯一所有权根且不可变；后代表通过 `project_id` 和复合外键保持同一 Project/CaseFile/Draft 归属。
- 一个 Project 只有一个 CaseFile；一个 CaseFile 可以拥有多份 Draft，但 `casefiles.current_draft_id` 必须通过 `(project_id, casefile_id, current_draft_id)` 非空复合外键指向本 CaseFile 的唯一 Current Draft。
- 切换工作稿必须锁定 Project/CaseFile，使用 `expected_current_draft_id` 比较并原子更新当前指针；跨项目 Draft、归档项目、空 Draft 和锁定 Draft 均不得被激活。

## 对象标识与存储

- `casefile_objects.object_id` 在 Draft 内唯一且删除后不复用；不同 Draft 可以保存相同稳定对象 ID。注册表不含 `payload_jsonb`。核心对象正文只写对应专用内容表，注册类型、基类类型和 Person/Location/Evidence/Testimony 扩展类型由数据库触发器保持一致。
- 所有专用当前态表携带 `project_id`、`casefile_id`、`draft_id`；单值关系使用带归属列的复合外键，多值跨类型关系使用 `casefile_refs`。已知 `ref_kind` 的端点类型由数据库触发器验证。

## 推理与结论

- `resolution_specs` 可在同一 Draft 中保存多条推理命题且 Slot ID/顺序在各自 Resolution 内唯一；Reasoning Edge 的两端必须属于同一 Path。旧物理 Knowledge State 仍保留 Entity/Phase 唯一约束用于迁移兼容，但目标无关 v1 只暴露 `as_of_event_ref`，不得重新把 Phase 写回机器契约。

## 并发与版本

- Current Draft 编辑、Snapshot、生成任务和 Agent 写请求同时携带 `expected_draft_id` 与 revision；服务端先比较 Current Draft 身份，再比较 revision，避免切换到相同 revision 的另一份稿后误写。成功写入 `draft_operations` 时，数据库锁定目标 Draft、验证 `base_revision` 和连续 `sequence_no`，再原子推进 revision；锁定 Draft 禁止编辑。
- Exposure Plan 先校验 `expected_draft_id`，再单独比较 Plan revision；成功重排只追加计划修订/条目/引用和审计，不创建 Draft Operation，不推进 Draft revision，也不修改 Event.time 或 Canon。
- Snapshot 只能固定当前 Draft revision，由项目所有者创建；插入时同时锁定 CaseFile/Draft，并要求 CaseFile、Draft、Snapshot 三层 Schema 版本一致。Snapshot、Operation、Canon 与 Audit 只追加，普通 UPDATE/DELETE 必须被数据库拒绝。

## M3.8 GoalSession 一致性冻结规则

本节的完整状态机、投递顺序和预算定义见 `docs/m3.8-goal-session-runtime.md`。

- 同一 Thread 同时最多一个非终态 GoalSession，并继续最多一个 queued/running/cancelling TaskRun。GoalSession 的 waiting/stale 状态不得伪装为长期 running TaskRun 或占用 Worker lease。
- GoalRevision、obligation、依赖、observation 和 transition 只追加；GoalSession 当前状态是受状态转换矩阵约束的投影。身份、状态、版本、队列顺序和关系不得仅保存在 JSONB。
- steer/replace 消息先按 Thread 消息顺序持久化，再由安全点 FIFO 单条领取。M3.8-06 在 Provider 解释 control 前把 FIFO 队首 lease 给当前 TaskAttempt；未过期 claim 阻止后项越序，过期 claim 只能由接管 TaskRun 的新 Attempt fencing 回收。当前 TaskRun/Attempt 成功终态、slice Checkpoint 哈希、单条 control 消费、GoalRevision/后继 Goal 与下一 queued TaskRun 保持同一事务。
- Goal continuation 同时比较 expected Goal ID/revision 与 Current Draft ID/revision。任一不匹配进入冲突或 stale；不得自动 rebase，也不得复用旧 mutation candidate。
- PatchSet 仍是审批/Apply 生命周期唯一权威。GoalSession 只保存关联和等待状态，不复制 Patch 操作、审阅决策或 Apply 事实。
- M3.8 会话级预算固定为 8 revisions、12 TaskRun slices、6 次已消费 steer/replace；单 TaskRun 继续使用 M3.7 预算，二者都不能被自动 continuation 绕过。

## 候选采用与工作稿隔离

- `brief_to_draft` 成功只产生不可变候选，不自动修改 Current Draft。候选采用以 `expected_current_draft_id` 为指针门禁，并重新校验 TaskRun 冻结的来源 Draft ID/revision、BriefVersion 和候选内容。
- 初始空 Draft 首次采用时原位物化；已有正文后再次采用会创建新 Draft、Operation 和 Snapshot，并在同一事务中把新 Draft 设为 Current Draft，旧稿的对象、引用、操作与快照保持不变。
- 候选存在 `result_snapshot_id` 即表示 `is_adopted`；仅当该 Snapshot 的 `draft_id` 等于 `casefiles.current_draft_id` 时才是 `is_current`。旧 Brief 候选始终不可采用。

## 哈希与 Canon

- 内容哈希由应用对 RFC 8785 Canonical JSON 的 UTF-8 字节计算 SHA-256；数据库验证格式，并要求 Canon 的 JSON 和哈希与来源 Snapshot 一致、CaseFile/Draft/Snapshot/Canon 四层 Schema 版本一致。
- Canon 只由项目所有者本人确认。数据库同时锁定 CaseFile/Draft，强制版本连续、父版本等于当前 Canon，随后更新 CaseFile/Draft 指针并写入审计事件。

## 删除

- 不得通过普通级联删除破坏不可变历史；未来项目清除必须设计专用、可审计的清除流程。
