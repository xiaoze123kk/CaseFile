# 数据一致性规范

本文涵盖所有数据库表的设计和操作必须遵守的一致性规则。

## 归属与所有权

- `projects.owner_user_id` 是唯一所有权根且不可变；后代表通过 `project_id` 和复合外键保持同一 Project/CaseFile/Draft 归属。
- 一个 Project 只有一个 CaseFile，一个 CaseFile 只有一个当前 Draft。

## 对象标识与存储

- `casefile_objects.object_id` 在 CaseFile 内唯一且删除后不复用；注册表不含 `payload_jsonb`。核心对象正文只写对应专用内容表，注册类型、基类类型和 Person/Location/Evidence/Testimony 扩展类型由数据库触发器保持一致。
- 所有专用当前态表携带 `project_id`、`casefile_id`、`draft_id`；单值关系使用带归属列的复合外键，多值跨类型关系使用 `casefile_refs`。已知 `ref_kind` 的端点类型由数据库触发器验证。

## 推理与结论

- `resolution_specs` 可在同一 Draft 中保存多条推理命题且 Slot ID/顺序在各自 Resolution 内唯一；Reasoning Edge 的两端必须属于同一 Path。旧物理 Knowledge State 仍保留 Entity/Phase 唯一约束用于迁移兼容，但目标无关 v1 只暴露 `as_of_event_ref`，不得重新把 Phase 写回机器契约。

## 并发与版本

- Draft 编辑使用乐观并发控制。成功写入 `draft_operations` 时，数据库锁定 Draft、验证 `base_revision` 和连续 `sequence_no`，再原子推进 revision；锁定 Draft 禁止编辑。
- Snapshot 只能固定当前 Draft revision，由项目所有者创建；插入时同时锁定 CaseFile/Draft，并要求 CaseFile、Draft、Snapshot 三层 Schema 版本一致。Snapshot、Operation、Canon 与 Audit 只追加，普通 UPDATE/DELETE 必须被数据库拒绝。

## 哈希与 Canon

- 内容哈希由应用对 RFC 8785 Canonical JSON 的 UTF-8 字节计算 SHA-256；数据库验证格式，并要求 Canon 的 JSON 和哈希与来源 Snapshot 一致、CaseFile/Draft/Snapshot/Canon 四层 Schema 版本一致。
- Canon 只由项目所有者本人确认。数据库同时锁定 CaseFile/Draft，强制版本连续、父版本等于当前 Canon，随后更新 CaseFile/Draft 指针并写入审计事件。

## 删除

- 不得通过普通级联删除破坏不可变历史；未来项目清除必须设计专用、可审计的清除流程。
