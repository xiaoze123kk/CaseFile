---
name: brief-to-draft-plan-execute
description: 让 Brief-to-Draft 先形成可追踪目标，再按目标执行、提醒和最终对账。
---

Blueprint 同次产出执行目标。Story 与 Evidence 只接收各自相关目标，并随业务产物返回独立核对记录。连续两次缺少有效核对时，运行时在下一次原有调用中注入具体 Nag reminder。最终审核逐项目标查验真实候选依据，不自动改写上游计划。

v18 保留 v17 的关系覆盖、竞争假设、中文语义、时间结构和定向修复材料；Plan-Execute 仅在其上附加 execution_plan、plan_checkin、Nag reminder 与 reconciliation。Blueprint 定向修复读取 previous_execution_plan 时，未受影响目标的 ID、依赖、范围和约束保持不变；只有修复的 Blueprint local_key 使来源失效时才最小同步调整对应目标。
