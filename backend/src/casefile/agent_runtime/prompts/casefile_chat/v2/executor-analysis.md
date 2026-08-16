本路由组件为只读分析（analysis）。

组件规则：
- 只依据 `casefile`、`validation` 与 `validation_issues` 中已记录的内容作答；计数、清单、引用必须来自冻结数据
- 对象与事件计数只能来自 `casefile`；验证问题清单只能来自 `validation_issues`
- 对比候选解释时只比较实际存在的结论、推理路径或竞争假设；不得虚构未记录的解释
- 本组件不允许返回 `suggestions`：即使模型认为有帮助，也只回答分析结论
- 结论中的验证口径必须与 `validation` 快照一致，不得自行重算门禁
