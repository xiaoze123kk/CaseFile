# CaseFile

面向个人创作者的互动推理内容结构化设计与验证平台。

## 包管理器

| 环境 | 工具 |
|------|------|
| 前端 | `pnpm`（workspace monorepo） |
| 后端 | `uv`（Python 3.12+） |

## 前端目标

- 所有后续前端新增、重构与视觉验收默认只面向桌面 Web；不设计、不新增、也不维护移动端断点或移动端专属交互，除非用户后续明确改变这一产品约束。

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
