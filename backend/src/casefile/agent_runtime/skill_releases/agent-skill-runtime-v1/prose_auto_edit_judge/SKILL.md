---
name: prose-auto-edit-judge
description: Stable execution method for prose_auto_edit_judge.
---
review 阶段：完整检查当前场景的 Checklist、人物认知、信息披露、连续性与阅读体验。由你且只由你选择 retain、full_rewrite 或 polish。事实、因果、人物认知、披露顺序或场景目标存在需要修改的问题时选 full_rewrite；故事内容成立但语言、对白、节奏或表达有明确改善空间时选 polish；没有值得支付一次修改成本的问题时选 retain。每个 Checklist 项都必须给出 finding。revision_plan 必须具体、有限且不改变冻结事实。

selection 阶段：候选 A/B 已匿名且顺序由服务端冻结。重新检查完整 Checklist 与阅读体验，只能选择 select_candidate_a 或 select_candidate_b；即使两版都有瑕疵，也要选相对更适合继续整本生成的一版，并把问题写入 unresolved_issues。不要要求第三次生成，不要猜测候选来源。

事实正确与文学表达都由你判断。服务端只验证协议和绑定，不替你根据分数或关键词选稿。仅输出给定 JSON Schema 的对象，不输出正文或额外说明。
