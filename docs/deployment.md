# 部署说明

将 `.env.example` 复制为 `.env`，检查配置后执行 `docker compose up -d postgres redis` 启动 PostgreSQL 和 Redis。随后可执行 `docker compose up app` 启动应用容器。

禁止提交 `.env`，也不要把私钥写入源代码或日志。实盘交易保持禁用。生产数据库升级必须在线执行 Alembic 迁移前置检查；离线生成的 SQL 不会执行数据检查。
