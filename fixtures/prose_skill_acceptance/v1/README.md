# Skill 默认发布的公开质量验收样本

这是开发验收集，不是私有 Holdout 或正式资格评测。

- 可读原文来自 `fixtures/casefiles/restart_loop.casefile.json`。
- 结构骨架来自既有 ScenePlan 开发输入；缺少原文的补充人工痕迹等说明在生成器
  `AUTHORED` 中逐项声明，不声称恢复了乱码原始文字。
- 通过正式 CaseFile → NarrativeIR 投影、SceneCompiler 输入与确定性编译生成新绑定。
  将历史倒叙骨架改成三个线性步骤：观察痕迹、读取日志、确认根因与组合条件。
- `REFERENCES` 为作者编写的上一场上下文，不是现场生成通过 Judge 的正文。
  修订输入人为增加一句否认重启发生的矛盾，用于验证局部纠错与原内容保留。
- 生成模型和评审模型均为当前 Flash；作者和评审不具备独立性，不据此宣称统计显著改善。

离线验证：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m casefile.benchmark.prose_skill_acceptance
```

真实运行必须显式提供 `--live --output-dir <新目录> --prices <已核对的人民币价格文件>`，
以及 `--budget-cny`。读取本地环境密钥，调用前持久化预算预留，最多 100 次物理调用；
所有生成、Judge、Critic 与传输重试共享同一预算。输出目录不允许覆盖。

每个版本生成和修订三个场景各两次，共 24 份正文；生成通过结构检查后执行事实 Judge，
事实通过后再执行质量问题检查。先保存原始结果，再做人工可读复查；自动 Judge 通过
不替代对提前揭示、重复上一场和无依据事实的复核。不会因评测失败自动切回旧版本。
