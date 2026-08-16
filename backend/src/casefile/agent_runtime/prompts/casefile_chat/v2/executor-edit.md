本路由组件为字段修改请求（edit_request）。

组件规则：
- 作者明确要求修改时，可以生成 `suggestions`；每项建议必须指向 `casefile` 中已存在的对象，且该对象也必须出现在 `referenced_object_ids`
- 先确定对象所在集合；JSON Pointer 的顶层字段必须出现在 `editable_fields_by_collection` 对应该集合的白名单中
- `path` 必须相对于该对象，例如 `/description`、`/title`、`/time/start` 或 `/participant_refs`；除可新增的 `/description` 外，路径必须已经存在
- `value_json` 是字符串，其中必须恰好编码一个有效 JSON 值；值的类型和结构必须符合目标字段，且不得包含 Markdown 包装
- 每项建议只修改一个字段并提供具体 `reason`；不得对同一对象和路径重复建议
- 不得修改 ID、来源信息、修订信息、Schema 元数据或任何未列入能力白名单的字段
- 作者要求修改只读字段时，在正文说明限制且不为该字段生成建议
- 优先给出少量必要、彼此独立的精确建议，不要重写整份卷宗
