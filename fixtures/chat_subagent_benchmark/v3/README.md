# Suite v3：修正生成器默认时间与输出格式歧义

继承 v2 的 24 个任务、16 份大卷宗、人工判定、修复授权和独立性标注，保留 v2 文件不覆盖。
正式运行前修正以下工程问题：

- 目标事件不继承通用 fixture 的 20:00～20:03；结构时间未录入则为 unknown，原文时间照常有效。
- 审计题仅选择事实冲突/认知差异/信息不足/相容，查证题仅选择支持/不支持/信息不足。
- 兼容【A】支持、【A】结论：支持、【A】主题：支持三种格式。仍逐支线判断，不允许同时给出互斥结论。

校准：24 正例、69 错误变体、32 合法格式变体。新一批独立冻结后用 6 个进程重跑 72 次，
Agent policy 仍为 v4。v2 的已消费运行保留为题集缺陷诊断，不将其成绩当作有效模型质量基线。

命令（backend 目录）：

```powershell
uv run python -m casefile.benchmark.chat_subagent_v4_parallel --suite-version v3 --workers 6 --output-dir var/benchmark/v4-suite-v3-UNIQUE --budget-cny 11
```

保留原始候选、原始评分、校准与运行前后源码哈希。不得用不同题集成绩直接宣称模型提升。
