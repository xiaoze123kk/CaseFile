# CaseFile 代码职责地图

详细的架构规则、文件职责和编码规范已按主题拆分到以下位置：

- [AGENTS.md](./AGENTS.md) — 项目入口、常用命令和文档导航
- [docs/architecture-boundaries.md](./docs/architecture-boundaries.md) — 架构边界与模块规则
- [docs/backend-code-map.md](./docs/backend-code-map.md) — 后端代码职责地图
- [docs/frontend-code-map.md](./docs/frontend-code-map.md) — 前端代码职责地图
- [docs/contracts-code-map.md](./docs/contracts-code-map.md) — 跨语言契约与 Fixture
- [docs/migration-standards.md](./docs/migration-standards.md) — 数据库迁移规范
- [docs/data-consistency.md](./docs/data-consistency.md) — 数据一致性规范
- [docs/code-quality-git.md](./docs/code-quality-git.md) — 代码质量与 Git 提交规范

新增、删除、重命名受 Git 跟踪的源码文件，或实质性改变文件职责时，必须同步更新对应的 docs/ 文档。

## 产品交互约束

- 采用渐进式披露控制界面信息密度。
- 所有后续前端新增、重构与视觉验收默认只面向桌面 Web；不设计、不新增、也不维护移动端断点或移动端专属交互，除非用户后续明确改变这一产品约束。
- `/visual-intake` 是独立的桌面端建案视觉实验，只使用本地 Fixture 验证三条起案入口、Brief 失效、冻结与修订的表现；它不接入生产 API，也不替换 `/`。

## 当前数据库表索引

当前 76 张个人产品业务表为：`users`、`projects`、`user_provider_settings`、`source_records`、`brief_intakes`、`brief_intake_questions`、`brief_intake_candidates`、`briefs`、`brief_versions`、`casefiles`、`drafts`、`casefile_objects`、`casefile_refs`、`casefile_contract_refs`、`draft_operations`、`narrative_phases`、`entities`、`relationships`、`people`、`locations`、`events`、`imported_documents`、`information_units`、`evidence_items`、`testimonies`、`claims`、`hypotheses`、`idea_candidates`、`reasoning_paths`、`reasoning_nodes`、`reasoning_edges`、`resolution_specs`、`resolution_slots`、`casefile_constraints`、`structure_locks`、`knowledge_states`、`knowledge_state_entries`、`parse_items`、`exposure_plans`、`exposure_plan_revisions`、`exposure_plan_entries`、`exposure_plan_entry_refs`、`exposure_plan_obligations`、`exposure_plan_obligation_refs`、`agent_threads`、`agent_thread_context_states`、`agent_messages`、`agent_message_contexts`、`agent_message_context_refs`、`agent_goal_sessions`、`agent_goal_revisions`、`agent_goal_obligations`、`agent_goal_obligation_dependencies`、`agent_goal_deliveries`、`agent_goal_observations`、`agent_goal_task_runs`、`agent_goal_transitions`、`agent_patch_sets`、`agent_patch_operations`、`task_runs`、`task_attempts`、`task_events`、`agent_step_runs`、`agent_model_calls`、`draft_snapshots`、`canon_versions`、`audit_events`、`verification_runs`、`verification_findings`、`verification_finding_refs`、`verification_finding_reviews`、`verification_finding_patch_operations`、`compiler_profiles`、`compiler_profile_versions`、`compile_runs`、`compile_artifacts`。具体职责与生命周期以 [backend/migrations/README.md](./backend/migrations/README.md) 为准。
