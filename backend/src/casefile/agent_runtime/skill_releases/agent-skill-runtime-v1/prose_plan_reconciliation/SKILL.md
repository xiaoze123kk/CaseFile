---
name: prose-plan-reconciliation
description: Stable execution method for prose_plan_reconciliation.
---
对 execution_plan 中每个目标恰好输出一项。根据 accepted_scenes 的实际正文证据判断 fulfilled、partial、not_fulfilled 或 unknown；模型此前的 plan_checkin 只能帮助定位，不能证明完成。fulfilled 与 partial 必须引用输入 evidence 中真实存在的 JSON Pointer。证据被预算裁剪或不足时使用 unknown。涉及已确认计划的问题只提出 suggested_plan_change，不改写计划或正文。
