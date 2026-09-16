---
name: novel-chapter-rewrite
description: Stable execution method for novel_chapter_rewrite.
---
requirements.preserve 是作者明确要求保留的情节、人物动机、伏笔等；requirements.allow_changes 说明可调整的内容。它们与 instruction 一起构成本次要求，冲突时保留既有关键事实并在说明中交给作者决定。不得把允许调整理解为必须全部改动。
revision_context 非空时，以其中 candidate 为本轮候选，按 review.revision_plan 和 findings 做一次有界修订，已满足要求的部分保持不变。仍然输出完整新章，不重复错误版本，不自行开始额外的审核或修订循环。
