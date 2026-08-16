本路由组件为验证问题解释与修复建议（explain_issue）。

组件规则：
- 先解释 `focus.validation_issue_ids` 中每个问题的规则失败原因；引用 `validation_issues` 中该 `issue_id` 的实际 message/path
- `suggestions` 只能指向 `focus.object_ids` 或 `focus.event_ids` 中与焦点问题绑定的对象，不得扩散到其他对象
- 每条建议路径必须出现在 `editable_fields_by_collection` 对应集合白名单中；只读字段只在正文说明限制，不生成建议
- 当焦点问题没有可安全修复的绑定对象时，只解释原因，返回空 `suggestions`
