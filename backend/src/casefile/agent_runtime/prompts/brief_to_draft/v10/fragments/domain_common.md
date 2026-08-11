根据冻结的 DraftContextPackV1 和 CaseBlueprintV1 生成当前领域的语义 IR。蓝图要求的当前领域对象必须恰好出现一次并保持蓝图顺序，不得增加、遗漏或重复对象。

所有引用字段只能逐字使用 `allowed_reference_values` 中对应字段列出的 local_key；该列表是最终允许值。`reference_contract` 用于说明每个引用字段允许指向的集合，`reference_directory` 用于核对蓝图目录。不得使用标题、自然语言别名或未声明的 local_key。允许值列表为空时，输出空数组；只有目标 Schema 允许 null 时才可输出 null。

如果 `targeted_repair_issues` 非空，只修复其中列出的失败点，同时重新检查整个当前领域是否满足引用契约；不得借修复机会改写无关作者事实。该字段为空或缺失时执行正常生成。

不得输出稳定 ID、ObjectRef.object_type、CoreMetadata、CaseFile envelope 或 extensions。自然语言使用简体中文，每个对象填写有信息量的 description。
