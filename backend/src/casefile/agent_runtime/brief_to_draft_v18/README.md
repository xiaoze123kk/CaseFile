# Brief-to-Draft v18 Plan-Execute

v18 复用 v17 的业务图、Skill 装配、Hook 与修复预算，并增加冻结执行目标、分支核对、Nag Reminder 和一次最终对账。Registry 默认仍为 v16；v18 只通过显式冻结版本使用。

- Blueprint Planner 同次返回 Blueprint 与来源绑定的执行目标。
- Story 与 Evidence 读取同一冻结计划，分别返回业务产物和可选 `plan_checkin`，各分支独立计数。
- 成功业务产物缺少有效核对时只累计遗漏；连续两次遗漏后，下一次原有相关调用携带具体 Nag。超时、传输失败和无可用产物不计数。
- “未落实”“当前维持”“需要调整”都是有效回应；只有最终对账负责汇总落实状态。
- 最终对账位于原质量门禁之后，只调用一次且不触发重写。恢复可复用已保存报告；曾发送但结果不确定时显示对账未完成，不再次发送。
- 作者侧只读取 `planning_summary`；完整目标、核对和提醒保留在步骤审计中。

离线验证覆盖 `test_plan_execute.py`、`test_generation_skills_hooks.py`、Prompt Package 与历史版本回归。真实 Provider 验收使用：

```powershell
scripts/acceptance-brief-to-draft-v8.ps1 -PromptVersion brief-to-draft-v18 -Provider deepseek -ModelId deepseek-flash -Repeats 18
```

真实验收需要隔离 `_test` 数据库和本地已配置凭据，不会切换 Registry 或自动采用候选。
