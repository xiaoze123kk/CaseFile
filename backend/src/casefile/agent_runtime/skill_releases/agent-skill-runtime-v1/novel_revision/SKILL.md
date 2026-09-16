---
name: novel-revision
description: Stable execution method for novel_revision.
---
动作是候选处置，不是对话结束标记：retain = 接受并保留当前候选，结束修订且允许进入后续步骤；stop = 当前候选不可接受，无法在剩余预算内解决，交给作者。全部满足要求、没有未完成问题时必须 retain，绝不能因为“结束流程”选择 stop。local_revision/full_rewrite 必须有真实待修订问题与具体操作；没有问题或 revision_plan 为无需修订时不得选择这两个动作。预算耗尽不改变上述含义。
