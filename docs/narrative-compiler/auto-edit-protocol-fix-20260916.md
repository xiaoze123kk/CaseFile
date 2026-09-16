# 自动审编 Judge 协议修复

真实补跑暴露的两个协议问题已在 `prose-shadow-runtime-v14` 修复，未再次调用真实模型。

- `decision_stage` 在动态响应 Schema 中只能取当前阶段。
- `unresolved_issues` 必须是数组，不再允许 `null`。
- `findings` 数量必须等于当前 Checklist 数量，`check_id` 只能来自当前 Checklist。
- 唯一一次协议修复会收到具体的缺失、重复、非法编号、字段类型和阶段错误，以及完整的
  `expected_check_ids`，不再只收到通用提示。
- 失败记录同步保存结构化 `validation_errors`，便于产品诊断和离线回放。
- v13 运行时和 `prose-auto-edit-json-object-v1` 请求构造仍可恢复；新任务使用 v14 和 v2。
- `AgentStepRun.component_version` 使用任务冻结的 runtime 版本，不再把恢复中的历史步骤写成
  当前版本。

验证：20 个自动审编及模式单元测试通过；自动审编 PostgreSQL 集成测试 4 passed，连同完整
正文 Worker 回归共 40 passed；4 个核心模块 mypy 通过；Ruff 与 diff whitespace 检查通过。
