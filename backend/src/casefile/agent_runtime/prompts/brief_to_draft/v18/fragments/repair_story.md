当输入包含 targeted_repair_issues 时，这是定向修复：保持所有未被指出的对象与字段完全不变——特别是 entities 集合必须与 Blueprint 完全一致，不得增删任何实体；只修正被指出的字段。

关系修复时，relationships 的 local_key 集合必须与 Blueprint.relationships 完全相等：只能修正已有 Blueprint relationship 的字段，绝不新增、删除、改名或临时生成 relationship local_key。若 issue 涉及关系覆盖，使用 Blueprint 已声明的关系对象补全其 from_key、to_key、relationship_type、title 或 description；不得以新增关系规避问题。
