---
name: scene-writing-plan-execute
description: 按冻结场景计划生成完整正文，并随产物核对当前执行目标。
---

先读取 plan_context，再写当前场景。正文必须落实当前 Scene 的 Beat 顺序、人物认知、地点时间、允许的信息释放和来源范围；不得把未来目标提前完成，也不得泄露 forbidden reveal。

输出完整场景正文，不输出补丁或解释。plan_checkin 与正文 artifact 分离：逐项说明已落实、未落实、当前只需维持，或需要上游计划调整。fulfilled 与 maintained 引用正文 block 的 JSON Pointer；核对记录不能替代后续 Judge 或最终对账。

出现 Nag reminder 时，在本次原有调用中回应对应目标。不能为了让提醒消失而声称完成、添加未经来源支持的事实或改变已确认计划。
