# 数据库迁移规范

本文涵盖所有 Alembic 迁移必须遵守的规则。违反任一规则将导致 CI 检查失败。

## 命名与格式

- 文件名必须为 `VyyyyMMddHHmmss__lower_snake_case.py`：大写 `V`、14 位 Asia/Shanghai 真实时间戳、双下划线和小写 snake_case 描述。
- Alembic 内部 `revision` 使用与文件名一致的 14 位时间戳，不含 `V`；`down_revision` 指向上一时间戳，所有迁移保持单头单链。
- 必须优先使用 `scripts/new-migration.ps1` 创建迁移，并用 `scripts/check-migration-names.ps1` 验证。不得手工伪造日期、复用时间戳或创建分叉。

## 迁移内容规则

- 自动生成只产生候选迁移；复合外键、JSONB、触发器、索引、约束、数据迁移和 downgrade 必须人工复核。
- 已进入共享环境的迁移不得重写；修正通过新的时间戳迁移完成。

## 执行约定

```bash
# 根据 ORM 变化生成候选迁移
uv run alembic revision --autogenerate -m "change description"

# 人工检查迁移文件后升级
uv run alembic upgrade head

# 查看当前数据库版本
uv run alembic current

# 仅用于本地开发验证的单版本回退
uv run alembic downgrade -1
```

- 不使用 `Base.metadata.create_all()` 代替正式迁移。
- 每次数据库结构变化必须在同一变更中提交对应的 Alembic 迁移。
- 数据迁移必须支持已有数据库升级，不能只保证空库初始化成功。
- API 和 Worker 启动时检查数据库版本；版本落后或不兼容时明确失败。
- CI 同时验证空库执行 `upgrade head`，以及上一基线数据库升级到 `head`。
- 生产环境优先通过新迁移向前修复；可能丢失数据的 `downgrade` 不作为常规回滚方案。

## 安全规则

- 可破坏的 `downgrade base` 只允许连接显式 `CASEFILE_TEST_DATABASE_URL`，且数据库名必须以 `_test` 结尾。
