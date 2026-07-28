# CaseFile v1 → PostgreSQL 映射审计

本文冻结根目录 `contracts/schemas/casefile/` 与 PostgreSQL 当前态的映射边界。CaseFile
v1 是唯一机器契约；数据库允许规范化异形，但 `Candidate → 当前态 → Snapshot` 必须无损。

## 顶层映射

| v1 字段 | PostgreSQL 落位 | 结论 |
|---|---|---|
| `schema_version`、`title`、`status` | `casefiles` / `drafts` | 直接映射；基线升级为 `1.0`。 |
| `casefile_id`、`version` | CaseFile/Draft 内部 ID 与 revision 的稳定投影 | 不新增额外 public ID。 |
| `brief_ref` | 新增 `briefs`、`brief_versions` | 已确认版本不可变。 |
| `project_profile` | `projects.profile_jsonb` | 完整按 v1 ProjectProfile 校验。 |
| `content_notices`、`extensions` | `drafts` 开放叶子 JSONB | 不承载对象身份、关系或版本。 |

## 对象映射

| v1 对象 | 当前态落位 | 缺口与最小迁移 |
|---|---|---|
| `Entity` | `casefile_objects` + `entities`; `knowledge_states` / entries | aliases、goals、secrets、capabilities 放实体叶子属性；知识引用继续规范化。 |
| `Location` | `casefile_objects` + `locations` | 从旧 Entity 扩展兼容演进为正式 Location 对象；保留旧行，不破坏降级。 |
| `Relationship` | 新增 `relationships` + `casefile_refs` | 当前引用边缺少关系身份、标题、方向、真值和可见性。 |
| `Event` | `events` + `casefile_refs` | 时间精度与所有多值引用需补齐映射。 |
| `InformationUnit` | `information_units` + `casefile_refs` | availability 叶子与 phase/entity/path 引用分离保存。 |
| `Claim` | `claims` + `casefile_refs` | 补齐 title、claim_type、materiality。 |
| `Hypothesis` | `hypotheses` + `casefile_refs` | proposition 复用正文；补齐 target、falsifier、竞争假设引用。 |
| `ReasoningPath` | `reasoning_paths`、`reasoning_nodes` + `casefile_refs` | 每个 v1 step 映射为稳定 node；输入/输出引用保存在统一边。 |
| `ResolutionSpec` | `resolution_specs`、`resolution_slots` + refs | v1 允许多 Spec，移除旧单 Draft 唯一门禁；补齐 conclusion_mode 等叶子。 |
| `Phase` | `narrative_phases` + refs | entry/completion/action 为叶子；visible information 为引用边。 |
| `Constraint` | `casefile_constraints` + refs | scope/conflict 为引用边；statement/title 为正文。 |
| `StructureLock` | 新增 `structure_locks` | 正式身份、目标对象、字段路径、原因均需持久化。 |

## 公共元数据

- `id`、`revision`、`confidence`、`confirmation_status` 由 `casefile_objects` 承载。
- `tags`、外部 `source_fragment` 引用和 `created_by` 保存在注册表来源元数据中；它们不改变
  CaseFile 对象间关系。
- CaseFile 内部 `ObjectRef` 一律投影为归属受约束的单值外键或 `casefile_refs`；禁止把内部
  关系静默放入内容 JSONB。
- `updated_at` 使用数据库时间戳；Snapshot 组装后按 v1 规范输出。

## 验证门禁

1. 根 Schema 与生成的 Python/TypeScript 类型不得漂移。
2. 运行时只加载由根 Schema 生成的包内镜像。
3. 所有 ObjectRef 必须存在且类型一致；`source_fragment` 是外部来源引用例外。
4. Agent Candidate 入库后立即按同一 revision 组装 Snapshot，重新通过 v1 校验并比较规范哈希。
5. 任何字段无法往返都视为任务失败并整体回滚。
