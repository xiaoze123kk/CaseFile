# Brief 多候选与单一工作稿开发日志

## 基本信息

- 日期：2026-07-30
- 分支：`feature/database-frontend-develop`
- 启动基线：`a3539d8`
- 实施入口：真实 `/brief`
- 用户确认：同一份已确认 `BriefVersion` 可以多次执行 `Brief → Draft`；每次生成独立候选，项目仍只保留一个当前可编辑 Draft。

## 已确认的关键决策

1. `BriefVersion` 是可重复使用的不可变生成输入，不在首次生成后被消费。
2. “再次生成”创建新的 `brief_to_draft` TaskRun；“任务重试”继续使用同一 TaskRun 的新 TaskAttempt，两者不得混淆。
3. 成功生成只保存经过契约验证的不可变候选，不自动写入当前 Draft。
4. 用户显式“采用候选”后，系统才在一次事务中把候选投影为唯一当前 Draft，并生成不可变 Snapshot。
5. 采用使用 Draft revision 乐观并发门禁；运行中的人工编辑或其他采用动作不会被静默覆盖。
6. 当前工作稿已有内容时，采用新候选属于明确的整卷替换操作：保留旧 Snapshot、Operation 和已软删除对象，不复用旧对象 ID。
7. 候选列表展示作者可理解的标题、核心推理命题、结构数量、Provider、模型、任务时间和当前采用状态，不展示原始 JSON、Schema、内部数据库 ID 或 Draft revision。
8. 候选持久化复用 `TaskRun → TaskAttempt`：成功 Attempt 的 `candidate_jsonb` 保存完整候选，TaskRun 的结果只保存列表所需摘要；不新增并行 Draft 或候选业务表。

## 本次纳入范围

- 允许同一当前 BriefVersion 创建任意多条生成任务，不再要求 Draft 为空。
- Worker 将成功结果保存为候选，不再自动物化到 Draft。
- 候选列表、候选详情和显式采用 API。
- 采用候选时原子替换当前规范化 Draft、推进 revision、写 Operation 和 Snapshot。
- `/brief` 的候选历史、再次生成、查看结构摘要、采用确认和进入工作台入口。
- TaskAttempt 候选写入后的数据库不可变门禁。
- 后端应用/API/Worker、迁移、前端和浏览器回归测试。

## 明确排除范围

- 多个同时可编辑的 Draft、Draft 分支合并或 A/B 并行编辑。
- 候选之间的字段级自动合并。
- 自动采用第一个候选或静默覆盖当前工作稿。
- 修改冻结的 `/demo` 原型。
- Compiler、Simulation 或 Target Adapter。

## 兼容策略

- 旧版已成功且带 `result_snapshot_id` 的 `brief_to_draft` TaskRun 视为“已采用历史结果”；其 Attempt 中已有完整候选，可继续出现在候选历史。
- 现有已生成工作稿保持不变。升级后再次生成只新增候选，除非用户显式采用。
- API 保留原生成任务入口；新增候选查询与采用入口，不改变 SourceRecord、Brief 确认或 Agent 工作台契约。

## 实施进展

- [x] 确认产品模型与当前一次性门禁的冲突。
- [x] 完成现有 TaskAttempt、TaskRun、Draft、Snapshot 和规范化写入边界审计。
- [x] 实现候选生成与不可变持久化。
- [x] 实现候选查询和显式采用。
- [x] 实现 `/brief` 候选历史与采用交互。
- [x] 完成迁移、职责说明和测试同步。
- [x] 完成 PostgreSQL、前端构建与浏览器验收。

## 验证计划

- 后端：Ruff、mypy、候选主路径/并发/不可变/替换失败路径、API 纵向切片。
- 数据库：迁移命名、`base → head → base → head`、候选不可变触发器。
- 前端：lint、TypeScript、Vitest、production build。
- 浏览器：同一 Brief 连续生成两个候选、刷新恢复、候选切换、显式采用、工作稿替换确认和进入工作台。
- 范围：`git diff --check`，确认不覆盖启动时已有 `docs/`、`.playwright-cli/` 和 `output/` 内容。

## 验证结果

- `scripts/check.ps1`：通过；Ruff、mypy、Alembic 9 段单链/升降级与 57 个后端测试全部通过。
- `pnpm check:contracts`：通过；生成漂移、Python 契约测试和 TypeScript 往返测试均通过。
- `pnpm check:web`：通过；ESLint、TypeScript、58 个 Vitest 测试与 Next.js production build 均通过。
- PostgreSQL 开发库已升级至 `20260730093618`，保持 42 张业务表；API `/health/ready` 返回该 revision。
- Playwright 在真实 `/brief` 创建项目 4，使用 FakeProvider 对同一 BriefVersion 生成任务 `#17`、`#18`；刷新后仍显示 2 份候选，候选间可切换。
- 浏览器先采用 `#17`，再采用 `#18`；第二次采用明确展示整卷替换确认，工作台可读取采用后的核心推理命题。
- 开发库核对结果：Draft revision `3`、成功候选 `2`、已采用候选 `2`、Snapshot `2`、采用 Operation `2`、当前对象 `1`、历史软删除对象 `1`。
- 浏览器验收期间临时使用 FakeProvider，未把本轮结果当作真实 OpenAI 网络证据；验收结束后 Worker 已恢复 `.env` 的 `live` 模式。
- 在 930px 验收视口发现并修复候选档案覆盖生成按钮的问题；1600×1000 复验布局、生成、采用均可操作。
