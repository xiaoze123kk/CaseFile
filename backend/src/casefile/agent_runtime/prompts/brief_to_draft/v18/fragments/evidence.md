生成 EvidenceLogicIRV2 artifact，完整覆盖 information_units、claims、hypotheses、reasoning_paths。evidence_assessments 输出空数组，由后续矩阵评估。推理路径 target_key 与 required_information_keys 必须忠实于 Blueprint，推理步骤只能使用白名单对象。

information_units 的 title、description、content、acquisition_conditions，claims 的 title、description、statement，hypotheses 的 title、description、proposition，以及 reasoning_paths 的 title、description 都必须使用自然准确的简体中文。四类 local_key 必须分别逐字覆盖 Blueprint 同名集合，禁止临时新增对象。

每个 reasoning step 的 output_key 是单个 claims 或 hypotheses 白名单字符串，不是数组；不得写 resolution_specs local_key。reasoning_paths 的 target_key 必须逐字复制 Blueprint 同 local_key 路径的 target_key，Blueprint 声明的 required_information_keys 必须在至少一个 step.input_keys 中实际输入。不得把叙事暗示或常识直接写成已证实结论；不存在竞争组的假设不需要机械生成比较矩阵。

落实 owner_branch=evidence_logic 的目标。若 plan_context 含 nag_reminder，必须在本次 plan_checkin 中逐项回应；证据不足时如实标记，不得补造信息或结论。
