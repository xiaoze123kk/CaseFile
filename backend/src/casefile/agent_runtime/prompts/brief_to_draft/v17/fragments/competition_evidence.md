同一 target_resolution_key 下存在两个或以上竞争假设时：
1. competing_hypothesis_keys 是派生数据：服务端按同一 target_resolution_key 下的全部假设确定性补全，你的输出会被服务端覆盖；关键是 target_resolution_key 必须正确指向该假设真正所属的 Resolution，同一 Resolution 下的每个假设都被视为互相竞争。
2. 对每个 hypothesis H，必须至少存在一条 reasoning_path.target_key == H 的路径。
3. 上述 H 的路径必须至少在一个 step.input_keys 中直接使用 information_units；后续比较矩阵的列只来自这些 information_unit 输入。
