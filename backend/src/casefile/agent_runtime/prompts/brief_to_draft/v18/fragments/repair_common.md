如果输入包含 targeted_repair_issues，只修正属于当前部件的问题，同时重新检查当前部件的全部引用、语义约束和中文要求。保持未被指出的对象、local_key 与字段不变。

若修复 Blueprint 且同时提供 previous_execution_plan，执行计划保持稳定：不得改动未受影响目标的 ID、依赖、范围、owner_branch 或约束；仅在被修复 Blueprint local_key 使其来源失效时最小调整相应目标引用或核对方法。重新输出当前 Schema，并重新核对当前适用的计划目标。

conclusion_slot_type_mismatch：只修正 issue 指向的 conclusion.values 项。逐项对照该 Resolution 的 required_slots：引用类型槽（entity、claim、evidence、hypothesis、event 等）必须只写白名单对象的 value_key，value 为 null；text、number、boolean 等标量槽必须只写符合类型的 value，value_key 为 null；不得同时填写、互换字段或以标题、自然语言替代 local_key。保持其他结论项不变。

conclusion_reasoning_path_scope_invalid：只保留当前 Resolution、已选 hypothesis，或其必要 Claim 链所需要且被标记为解答所需的 reasoning_path。不得用其他问题、无关假设、仅同场出现或未标记为解答所需的路径填充 supporting_reasoning_path_keys；证据不足时如实使用 undetermined 与 unresolved_gaps。

Temporal 的 value_error：逐项以 previous_output 为基线修正 issue 指向 assignment。exact、approximate、range 的值必须匹配其 precision：day 为 YYYY-MM-DD，hour 为 YYYY-MM-DDTHH，minute 为 YYYY-MM-DDTHH:MM，second 为 YYYY-MM-DDTHH:MM:SS；range 的 start/end 同一 precision；relative 的 before/after 具有非空 offset_minutes，same_time 只用 null 或 0；不得输出 unknown、时区、低位零、循环或自引用。未被指出的 assignment、basis 与 basis_refs 保持不变。
