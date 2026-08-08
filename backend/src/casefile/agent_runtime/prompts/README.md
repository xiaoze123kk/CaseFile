# CaseFile System Prompt Registry

此目录是生产 Agent System Prompt 的唯一事实源。每个 Agent 功能拥有独立、完整且可单独演进的 Prompt 版本；运行时代码不得内联生产 System Prompt。

## 目录契约

```text
prompts/
├── registry.json
└── <agent_id>/
    └── vN/
        ├── manifest.json
        ├── system.md                         # 单 Prompt 版本
        └── <component>.md                    # 原子 Bundle 版本
```

Prompt 版本有两种互斥形态：

- 单 Prompt：`manifest.json` 引用唯一的 `system.md`，并记录其 `system_prompt_sha256`。
- 原子 Bundle：`manifest.json` 的 `components` 必须精确声明 `planner`、`story`、`evidence`、`governance`；每项只允许同名 `.md` 文件并记录独立 SHA-256。`brief-to-draft-v8` 使用此形态。

所有 Prompt 文件必须为 UTF-8、LF 换行、非空内容；哈希按原始字节计算。版本目录使用 `vN`，完整版本号使用 `<agent-id>-vN`，其中 `agent_id` 的下划线替换为连字符。

## 发布与激活

1. 复制当前版本为单调递增的新 `vN` 目录；已发布目录不得修改或删除。
2. 修改新 Prompt，并更新 Manifest 的版本链、变更摘要和全部对应文件哈希。
3. 在 `test_prompt_repository.py` 的不可变发布清单中加入该版本的全部哈希；Bundle 必须列出每个组件。
4. 运行 Prompt Repository、Provider 和打包校验，先提交尚未激活的新版本。
5. 评审通过后，单独移动 `registry.json` 中的 `current_version` 指针；回滚同样只移动该指针。

`registry.json` 是生产新任务唯一的激活入口，不能通过环境变量选择历史 Prompt。`TaskRun` 会冻结 Registry 解析出的 `prompt_version`；v8 同时冻结 `brief-to-draft-pipeline-v8` 运行时版本，并在任何模型调用或步骤复用前完整加载并校验四组件 Bundle。

未知版本、缺失资源、哈希漂移或 Bundle 组件不完整都会失败关闭，不会静默回退到当前版本。

本仓库只管理 System Prompt。用户输入构造、输出 Schema、工具定义与 Provider 结构化输出适配仍由各自代码及其版本机制维护。
