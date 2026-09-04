# B3 公开评审协议对照

此目录只冻结公开开发实验，不新增或读取私有资格样例。`experiment.json` 引用 v1 的 8 组任务、Gold、审定和内容指纹，绑定固定 DeepSeek Flash 参数、原双位置基线与四调用候选，并列出完整 48 个组/Trial 的交替顺序。旧 v1/v2 fixture 和生产活动 Prompt 不变。

候选先独立评估两份匿名正文，然后携带各自评估执行双位置比较。每次比较都会交换正文、Evidence 目录和评估，不共享上一轮比较结论。两次比较共享单稿评估，因此不是独立评审员共识。单稿观察为带 Evidence 的简短缺陷描述，不要求输出内部推理。

运行入口（从仓库根目录）：

```powershell
scripts/prose-quality-benchmark.ps1 -Mode DiagnosticFake -AttemptId diagnostic-fake-unique
scripts/prose-quality-benchmark.ps1 -Mode DiagnosticLive -AttemptId diagnostic-live-unique
```

默认旧 `Fake` 行为不变。Live 从 `CASEFILE_DEEPSEEK_API_KEY` 或 `DEEPSEEK_API_KEY` 读取凭据，需要干净源码。每组每 Trial 重新调用，不复用历史响应；总上限 144 次调用。协议失败终止当前组/Trial 后继续，基础设施失败终止整个 Attempt，保留全部固定分母。

输出位于 `backend/var/benchmark/prose-quality/diagnostic-v1/{fake|live}/{AttemptId}/`，包括独占创建的 manifest、逐组结果、`report.json` 和 `report.md`。报告保留预测、Gold 标签、失败、tokens、延迟和哈希，不保存凭据、正文、原始响应或请求 payload。Live 额外以实验指纹创建一次性消费记录；更换 Attempt ID 无法重复消费同一实验。后续变更必须冻结新版本与新实验指纹，不能覆盖旧结果。

每组指标固定使用 24 Trial、120 维度判断为分母，旧开发门槛仍按每个 8 题 Trial 展示。只有候选镜像一致率严格增加、两个位置的整体和五维准确率均不下降、完整执行且零失败，才标记值得继续验证。Fake 永不产生此能力结论；所有运行均为 `qualified=false`，不修改 B3 资格门槛，不激活候选。
