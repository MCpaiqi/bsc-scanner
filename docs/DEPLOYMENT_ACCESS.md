# 本地部署与分析员访问

当前是单负责人、BSC/PAPER 研究台。默认网络面向本机，研究浏览公开给可到达该服务的客户端；
备注与提醒写入使用同一分析员密钥，不提供多用户角色、用户级审计或公开互联网账号系统。

## 新环境

```bash
python scripts/init_local_env.py
# 按需在新.env中配置真实 provider；无 provider 时保持未配置状态。
docker compose up --build -d
```

初始化器仅使用标准库，以独占方式创建0600文件，生成两个独立的随机密钥，不输出密钥内容，
不覆盖已有文件或符号链接。Windows 使用当前用户目录的 ACL；不要把生成文件放在共享目录。
`.env.example` 保留空字段，`.env` 不进入 Git、Docker build context 或 source release。
自定义 `--output` 适合在项目外准备临时配置；常规运行把最终配置保留在项目根 `.env`。

- Web：`http://127.0.0.1:8080/`。
- API：`http://127.0.0.1:8000/`；`/legacy` 是旧版只读界面。
- PostgreSQL/Redis：仅在 Compose 网络内可达，无主机端口。检查数据库使用
  `docker compose exec postgres psql -U postgres -d smms`，不需要额外发布端口。
- 固定服务：api、web、db-migrate、postgres、redis；后台为 bsc-ingestion、bsc-live-tail、
  radar-worker、event-relay、forward-worker、alpha-coverage、research-rebuild。

Compose 强制要求 `SMMS_POSTGRES_PASSWORD`。运行进程用结构化 DATABASE_HOST/PORT/USER/NAME/PASSWORD
构造连接串；SQLAlchemy 负责密码特殊字符转义，Alembic 处理配置中的百分号。
主机 `.env` 中的 DATABASE_HOST 通常为127.0.0.1，容器内明确覆盖为 postgres。
不使用结构化字段时仍支持已有 DATABASE_URL；连接串和密钥不会出现在 Settings repr/model dumps 中。
本机单进程开发需自行提供可达的数据库/Redis配置；默认 Compose 不向主机暴露数据库。

## 研究写入

`SMMS_ANALYST_KEY` 为空时，所有备注/提醒的 PUT、POST、PATCH、DELETE 返回503和 ANALYST_READ_ONLY。
配置后，没有有效 `Authorization: Bearer ...` 的写请求返回401和 ANALYST_UNAUTHORIZED，且不打开数据库会话。
六条现有写入路由和未来同一 analyst router 的非安全方法都受保护；查询参数、Cookie、重复或错误格式的认证头
不能代替单个 Bearer 头。常规只读查询和不持久化数据的钱包 replay 仍可使用。
GET `/api/radar/access` 只返回 enabled/authorized 布尔值，响应不可缓存；不会返回密钥。

在代币详情的“研究编辑权限”粘贴密钥后解锁。密码框支持粘贴和密码管理器；页面仅在授权核对与写请求
发送密钥，且拒绝通过非本机 HTTP 地址发送。普通 GET、事件流 URL、localStorage/sessionStorage 不携带或存储密钥。
锁定或刷新会清除当前页面授权。锁定是清除本页凭据，已经被服务器接受的请求可能完成；需要撤销其他页面时轮换服务端密钥。
服务端拒绝授权后，页面保留草稿并要求重新解锁；不会自动重试写入。
API 使用恒定时间字节比较；密钥格式为32..256个 URL-safe ASCII 字符，初始化器默认生成256bit随机值。

## 已有数据卷的升级与密码轮换

**不要删除数据库卷，也不要用重新初始化数据库来修改密码。** PostgreSQL 镜像的 POSTGRES_PASSWORD
只影响首次初始化；修改 `.env` 不会自动改变已有数据库角色的密码。

1. 保存现有 `.env` 和数据库备份到私有、Git之外的位置，保留 provider 配置和原 Compose 项目/卷名。
   用 `python scripts/init_local_env.py --output <项目外的新文件>` 准备随机值，把新的 SMMS_POSTGRES_PASSWORD
   与 SMMS_ANALYST_KEY 合入原 `.env`，不要整文件覆盖 provider 配置。不要把密钥粘贴进终端命令或 Git。
2. 在 postgres 保持运行的情况下，停止 api、bsc-ingestion、bsc-live-tail、radar-worker、event-relay、
   forward-worker、alpha-coverage、research-rebuild。新 Compose 可解析上述 `.env`，此时现有数据库仍使用原密码。
3. `docker compose exec postgres psql -U postgres -d smms`，使用交互式 `\password postgres` 设置与
   `.env` 一致的新密码，再 `\q` 退出。若已有管理员名称/认证机制不同，使用现有受信管理员连接完成同一轮换。
4. `docker compose up --build -d`，让迁移和所有更新的进程使用新配置启动，检查 `/health`、来源状态与心跳。
   启动失败时保留原卷，修正凭据/配置；旧二进制不支持新结构化配置，不能混用新旧运行进程。

单独轮换分析员密钥：更新私有 `.env` 后 `docker compose up -d --no-deps --force-recreate api`。
Settings 由进程缓存，必须替换/重启 API 才会生效；旧页面会在下一次请求时被拒绝，需重新解锁。
不需要为分析员密钥变更重置研究数据或重启数据库。

## 运行边界与验证

远程访问应在明确配置的 HTTPS 反向代理/私有网络后提供。默认服务绑定本机；不要为了浏览而公开数据库或 Redis。
共享密钥不是多租户隔离；读接口也不是保密数据访问控制。`/docs` 方便本机调试，部署外围应按实际用户范围限制访问。

测试覆盖未授权请求在会话前被拒绝、真实授权写入的 revision/幂等/事务、密钥轮换、URL转义、凭据脱敏、
生成器覆盖/链接拒绝、Compose实际渲染、前端解锁/锁定/草稿保留和无持久化泄露。
CI 还强制验证 Compose 插件和渲染边界，并用运行中的 API/Web 镜像验证匿名401、正确密钥与无数据库写入的参数校验。
完整 Compose 进程拓扑、持久卷重启、恢复及真实 provider 可用性继续由 RESEARCH_RELIABILITY.md R14 跟踪。

参考：[FastAPI security](https://fastapi.tiangolo.com/reference/security/)、
[SQLAlchemy URL](https://docs.sqlalchemy.org/en/20/core/engines.html#creating-urls-programmatically)、
[Compose required interpolation](https://docs.docker.com/reference/compose-file/interpolation/)。

## 提醒条件类型与旧规则

smart_money_score 使用0..100的有限 JSON 数字；smart_wallet_count 使用非负整数；liquidity 使用非负有限数字。
观察值可来自序列化的数值字符串，但规则配置必须使用 JSON 数字，不能用布尔值、对象、NaN或Infinity冒充数值。
risk 使用 LOW/MEDIUM/HIGH/CRITICAL/UNKNOWN，支持 ==、!=、in，不使用字符串排序表达风险高低。
in 的每个元素都必须匹配字段类型。缺失、UNKNOWN和无效观测不触发对应条件；any/all组合仍按明确模式计算。

创建和修改的条件不合法时返回422，不写入新条件。已有不兼容规则保留原值和 enabled 标记，查询响应带
validation_status=INVALID，显式评估返回 INVALID_RULE，自动评估跳过它并继续后续有效规则，不自动删除历史。
修复时通过已授权 PATCH 提供合法 conditions。部分条件可用时，通知快照把非法观测记为 null，Decimal观测以字符串
保留数值，避免不可序列化数据或NaN中断持久化；不会把未知值补成0。此项不证明手工评估状态来自 canonical 链证据（R3）。

已记录的次要界面限制：旧写请求延迟返回401时，界面的授权状态重读可能取消同时进行的新解锁，需再输入一次密钥。
服务端授权边界和草稿保留不受影响；后续界面改进会对这类恢复请求增加相同的代次检查。
