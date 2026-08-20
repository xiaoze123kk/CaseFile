# CaseFile Agent 前端 Phase 2 Handoff

## 当前工作树

- 路径：`C:\Users\Lenovo\Desktop\CaseFile-agent-frontend-redesign`
- 分支：`codex/agent-frontend-redesign`
- Phase 1 提交：`ae6c081 feat: 重构卷宗统筹 Agent 表面`
- Phase 1 基线：`0c23be0`
- 当前工作树在写入本 handoff 前是干净的；不要把主工作区的 Verification 脏改动带入本分支。

## 已完成的 Phase 1

- `AgentSurface = "closed" | "quick" | "desk"`，不再使用 `agentOpen`。
- 顶部 Agent 与 `Ctrl/Cmd+Shift+K` 打开或聚焦 Canvas 内 Quick Ask。
- Quick Ask 保留 Object Rail、Canvas 和 Inspector；Agent Desk 只替换中央 Canvas。
- 验证问题交给 Agent、用户点击“在统筹台继续”或 `Ctrl+Shift+Enter` 时进入 Desk。
- Agent CSS 已从主 Workbench CSS 拆到 `workbench-agent.module.css`。
- Composer 已改为自动增高多行 textarea：Enter 发送、Shift+Enter 换行、IME composing 时 Enter 不发送。
- Context Chips 由真实 Workbench Focus 派生；无选中项不制造虚假上下文。
- 生产 assistant 消息使用 `data-role="assistant"` 选择器。
- Quick Ask Esc 关闭并恢复顶部 Agent Trigger 焦点；重复打开会重新聚焦 Composer。

## 已验证

- `pnpm --filter @casefile/web test`：31 files / 325 tests
- `pnpm --filter @casefile/web typecheck`
- `pnpm exec eslint .`（在 `apps/web`）
- `pnpm --filter @casefile/web build`
- `git diff --check`

## 当前本地运行环境

- Web：`http://127.0.0.1:3000`
- API：`http://127.0.0.1:8000`
- API 健康：`/health/ready` 返回 `status=ready`、revision `20260817000000`
- Worker：本工作树的 `backend\.venv\Scripts\python.exe -m casefile.worker`
- 独立数据库：`casefile_agent_frontend_redesign` 与 `casefile_agent_frontend_redesign_test`
- 本地开发用户：`X-CaseFile-User-Id: 1`
- 可直接查看：`http://127.0.0.1:3000/workbench?project=1`

`.env` 与 `var/dev` 是本机忽略文件。`scripts/start.ps1` 默认会接管 3000/8000 端口；不要回退共享的原 `casefile` 数据库。若启动报迁移版本不匹配，确认 `.env` 仍指向上述独立数据库。

## Phase 2 范围

目标是“可用的 Agent Desk”中保真版本，继续复用现有 Thread、Message、Task、Reference 和路由 API，不新增后端协议或 `/agent` 路由。

1. 调查记录式 Conversation：不再以左右聊天气泡为主，保持阅读列宽和调查记录层级。
2. Sticky Task Strip：只显示真实 Task/SSE 当前阶段、取消和完成摘要；禁止估算动画。
3. Thread Manager Popover：搜索、New、Pin、Rename、Archive、Show Archived；使用可访问的 Combobox/Listbox 语义，Esc 后焦点回 Thread Trigger。
4. Structured Result Summary：对话只保留引用、Finding、Patch 的摘要入口；引用继续定位真实对象、事件、问题和 View。
5. 窄屏兼容：Desk 继续复用 Canvas Region，不新增移动端第五个 Agent 区域。
6. 按职责拆分 Agent Presentation，必要时同步更新 `docs/frontend-code-map.md`。

建议新增/抽取：

```text
workbench-agent-desk.tsx
workbench-agent-thread-menu.tsx
workbench-agent-task-strip.tsx
workbench-agent-conversation.tsx
```

## Phase 2 不要做

- 不迁移完整 Patch Ownership 到 Inspector；那是 Phase 3。
- 不接 ordered batch simulation、`can_apply`、Finding 影响集或 Apply 生命周期重写。
- 不修改 Provider、Prompt、模型、任务调度或后端协议。
- 不复制 Workbench Focus；Workbench 继续拥有对象、事件、问题和 View 导航状态。
- 不新增独立 Agent 路由或移动端 Agent Tab。
- 不覆盖、Reset 或批量格式化其他工作树的 Verification 改动。

## Phase 2 建议验收

- Quick Ask 升级 Desk 后 Thread/Message 状态不丢失。
- Desk Header 的 Thread Popover 支持搜索、新建、Pin、Rename、Archive 和 Show Archived。
- Popover 的键盘上下移动、Enter 选择、Esc 焦点恢复可用。
- Task Strip 只展示真实 Task/SSE 阶段，并支持取消、完成、失败、断线恢复。
- Assistant 完成消息只显示结构化摘要入口，引用可定位真实 Workbench 上下文。
- `/workbench?project=1` 在桌面和窄屏 Canvas Region 下无第四栏覆盖。
- 新增组件测试、真实 API 数据测试；最后重跑 typecheck、Vitest、ESLint、build 和 `git diff --check`。

## 继续工作建议

从 `ae6c081` 创建 Phase 2 分支，例如：

```powershell
git switch -c codex/agent-frontend-redesign-phase2 ae6c081
```

先阅读 `CASEFILE_AGENT_FRONTEND_REDESIGN_PLAN.md` 第 5、9、10、11、14 节，再检查 `workbench-agent-live-panel.tsx` 中现有 Thread/Task/Message API 状态，优先抽 Presentation，不先改领域生命周期。
