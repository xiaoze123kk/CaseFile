# B3 节奏与无效重复开发对照

固定 `deepseek-v4-flash`、temperature=0、thinking 关闭、8192 输出 tokens、无重试和双位置比较。基线仍使用活动 `prose-quality-pairwise-v1`；候选 `prose-quality-pairwise-v4` 在 v1 全文后只增加一段节奏判别标准，不加入测试例句、任务名或 Gold。两组每个 Trial 均调用两次，不使用上一轮四调用候选。

12 题 × 3 Trial × 2 组 × 2 调用＝144 次上限。8 个旧公开样例及 Gold 完全保留，另加档案室、渡口两个场景各一组无效重复与一组功能性重复。新样例有共同的前后文，只改变中间的表达组织。较优稿 A/B 各半；无效重复组较短稿优，功能性重复组较长稿优，避免以位置或长度直接判胜。

新场景是人工编写并由 Codex 审阅的合成开发 Fixture。`synthetic_source`、明确事实清单、对应正文 Evidence 及语义审阅 hash 都可复验；其 Consensus 形状用于复用既有协议校验，**不是实时 Council 运行结果，也不是生产 ScenePlan 编译结果**。`reviewer_independence=false`，不构成正式资格证据。`generate.py` 可确定性重建新任务，不能重新生成或改写旧任务。

`gold-review.json` 记录全部新样例的审阅方式，以及旧 `pacing_original` 与 `tradeoff_tie` 的复核意见。后者的整体 tie 有主观权衡，原 Gold 保留用于历史回归，但其偏离不能直接解释为客观模型缺陷。所有样例均是公开开发数据，不得进入后续私有 Holdout。

运行命令：

```powershell
scripts/prose-quality-benchmark.ps1 -Mode DiagnosticFake -DiagnosticExperiment pacing-v1 -AttemptId pacing-fake-unique
scripts/prose-quality-benchmark.ps1 -Mode DiagnosticLive -DiagnosticExperiment pacing-v1 -AttemptId pacing-live-unique
```

Live 使用已配置的 DeepSeek 环境变量凭据，要求干净源码；每个冻结实验仅运行一次，不补跑、不换名称重跑。默认仍指向历史 `independent-v1`，其已消费状态与所有旧报告保持不变。输出位于 `backend/var/benchmark/prose-quality/pacing-v1/{fake|live}/{AttemptId}/`。

继续验证条件在 Live 前冻结：正反位置的新题节奏判断均严格改善，其中无效重复组也必须严格改善；功能性重复组不退步；旧题两个位置整体准确率、节奏准确率与镜像一致率均不退步；其他四维逐维、逐位置在旧题以及完整集合上均不退步；新题镜像一致不退步；源码/数据稳定、完整执行且零协议或基础设施失败。每组固定分母 36 Trial/180 维度，旧题单独保留 24/120 和原开发门槛，新两个组各 6 Trial。

Fake 仅验证统计与协议，不能证明模型能力；Live 只决定是否值得进一步验证，始终 `qualified=false`，不激活候选、不修改 Polisher、不启动正式 B3/B4。
