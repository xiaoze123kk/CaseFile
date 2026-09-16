---
name: brief-to-draft
description: 将冻结 Brief 转为可验证、等待作者采用的 CaseFile 候选；按阶段生成并有界修复。
---
# Brief-to-Draft
忠实使用冻结 Brief。按蓝图、时间、Story 与 Evidence、矩阵、Governance、编译校验的依赖执行。
模型负责当前组件内容，Workflow 负责阶段推进和修复预算；所有结论保持候选状态。

## 阶段资源索引
阶段指令由 manifest 引用 Prompt Package 的 planner、temporal、story、evidence、matrix、governance 片段。
关系覆盖和竞争假设材料按组件输入加载；修复调用才加载 repair_* 片段。
该索引供程序发现；模型每次只接收当前激活的阶段材料。
