# CaseFile

面向个人创作者的互动推理内容结构化设计与验证平台。

## 包管理器

| 环境 | 工具 |
|------|------|
| 前端 | `pnpm`（workspace monorepo） |
| 后端 | `uv`（Python 3.12+） |

## 前端目标

- 所有后续前端新增、重构与视觉验收默认只面向桌面 Web；不设计、不新增、也不维护移动端断点或移动端专属交互，除非用户后续明确改变这一产品约束。

## 小说编译链路职责边界

- 本节约束仅适用于小说编译链路，不得外推到其他服务端模块、业务链路或通用架构。
- 小说的主要规划、连续性判断与内容修订交给 LLM；服务端不承担小说创作或主观叙事裁决。
- 服务端 Schema 与确定性校验只守住可执行、可追踪的硬底线，例如协议格式、对象引用存在性、依赖结构、版本、哈希、预算与证据绑定。
- 无歧义的结构引用遗漏可由服务端归一化或交给 LLM 确认修复，不应轻易导致整本终止。
- POV 焦点、信息重复、人物认知变化等可能存在文学解释的连续性问题，应采用分级的 LLM 审核；优先交给 LLM 做有界局部修订，不由服务端规则直接判为不可恢复。
- 产品交付模式允许非致命的小说瑕疵继续完成整本；严格评测模式可以保留更严门禁。两者的完成率与无冲突率分别统计。

## 常用命令

| 命令 | 说明 |
|------|------|
| `scripts/dev.ps1` | 启动前端本地开发服务器 |
| `scripts/bootstrap.ps1` | 初始化本地环境（PostgreSQL、迁移、可选种子数据） |
| `scripts/check.ps1` | 统一质量门禁（依赖、迁移命名、编译、Ruff、mypy、Alembic、pytest） |
| `scripts/generate-contracts.ps1` | 从 Schema 生成 Python/TypeScript 契约包 |
| `scripts/new-migration.ps1` | 创建 Alembic 迁移文件 |
| `scripts/benchmark.ps1` | 运行 brief_to_draft 评测 |

## 编码指南

修改代码前必须阅读以下文档，了解每类代码的落位规则和架构约束：

- [架构边界与模块规则](docs/architecture-boundaries.md)
- [后端代码职责地图](docs/backend-code-map.md)
- [前端代码职责地图](docs/frontend-code-map.md)
- [跨语言契约与 Fixture](docs/contracts-code-map.md)
- [数据库迁移规范](docs/migration-standards.md)
- [数据一致性规范](docs/data-consistency.md)
- [代码质量与 Git 提交规范](docs/code-quality-git.md)
