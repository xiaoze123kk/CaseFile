# CaseFile System Prompt Registry

本目录是 CaseFile 生产 Agent 系统提示词的唯一事实源。每个 Agent 功能拥有独立、完整、可单独演进的 `system.md`，运行时代码不得再内联生产 System Prompt。

## 目录契约

```text
prompts/
├─ registry.json
└─ <agent_id>/
   └─ vN/
      ├─ manifest.json
      └─ system.md
```

- `registry.json` 只声明各 Agent 当前启用的完整版本号。
- `manifest.json` 固定 Agent、版本、提示词文件、前置版本、变更摘要和文件 SHA-256。
- `system.md` 使用 UTF-8 与 LF 换行；哈希按文件原始字节计算。
- 面向中文用户的生产 System Prompt 默认使用简体中文；字段名、工具名、枚举值和其他机器标识符保留契约中的原文。
- 版本目录和完整版本号分别使用 `vN` 与 `<agent-id>-vN`，其中完整版本号把 `agent_id` 的下划线替换为连字符。

本仓库只管理 System Prompt。用户输入构造、输出 Schema、工具定义和 Provider 结构化输出适配继续由各自代码与版本机制维护。

## 发布新版本

1. 复制当前版本为新的、单调递增的 `vN` 目录。
2. 修改新目录中的 `system.md`，不得修改或删除任何已发布版本。
3. 更新新版本 `manifest.json` 的 `version`、`previous_version`、`change_summary` 和 `system_prompt_sha256`。
4. 把新版本及其哈希加入 `test_prompt_repository.py` 的不可变发布清单；已存在条目不得改写。
5. 运行后端 Prompt Repository 测试和仓库检查，先提交未启用的新版本。
6. 评审通过后，单独修改 `registry.json` 的 `current_version` 指针并再次运行检查。

回滚只移动 `registry.json` 指针，不修改历史版本内容。运行时遇到未知版本、缺失资源或哈希漂移会直接失败，不会静默回退到当前版本。

首次正式发布前允许在明确授权下修正当前基线内容，但必须同时更新 Manifest 哈希、变更摘要和测试中的固定哈希；正式发布后仍严格遵守不可变版本规则。
