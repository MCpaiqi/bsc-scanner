# 部署说明

首次部署时从 `.env.example` 准备 `.env`；保留已有环境的配置和数据。检查 BSC RPC、工厂地址和外部证据服务配置，并保持 `ENABLE_LIVE_TRADING=false`。先用 `docker compose config --quiet` 检查服务配置，再执行 `docker compose up -d --build`。`db-migrate` 完成后，API 和各工作进程才会启动；前端入口为端口 8080，API 为端口 8000。

`bsc-ingestion` 处理历史追赶，`bsc-live-tail` 处理实时边界；`radar-worker` 执行审计，`forward-worker` 处理收益观察，`alpha-coverage` 补齐覆盖证据，`research-rebuild` 重建失效研究结果。`event-relay` 必须同时运行，负责把已提交事务中的事件投递到 Redis，再由 API 转发到 SSE。可用 `docker compose logs --tail=100 event-relay` 查看投递工作进程日志。

事件与候选更新、审计状态或规则通知在同一数据库事务中入队。回滚不会产生可投递事件；传输失败会重试，工作进程中断后的领取租约在 60 秒后可重新领取。事件 ID 和原始时间在重试时保持不变，投递语义为至少一次。事件载荷记录的是发生时的状态，可能晚到；浏览器只把事件作为刷新提示，金额、风险和评分来自重新读取的接口快照。outbox 投递成功并不表示浏览器已收到。

SSE 的默认内存历史为 1000 条，每个订阅队列至多 256 条。保留中的游标可补发匹配筛选的事件；未知游标、超量补发、慢客户端或 Redis 链路缺口会收到 `STREAM_RESET`，要求重新读取当前快照。控制消息始终绕过业务事件筛选，`data.reason` 描述原因，`data.resume_from` 与 SSE 的 `id` 指定后续恢复游标；空游标会清除浏览器原游标。API 重启或切换实例后也可能要求重新同步。这不是完整、永久的事件日志回放。

Redis 订阅会自动重连，断线和重连都会使旧内存历史失效。异常消息会触发重新同步，非法 UTF-8、NUL 或无法编码的游标不会进入历史。浏览器按最近 1024 个事件 ID 去重，区分连接、正在同步、实时链路中断、刷新失败与快照已刷新；恢复时会拒绝旧的在途请求结果，刷新选中详情的研究数据并保留笔记草稿。控制消息的 `data.live_available` 反映订阅链路状态。

只读接口 `GET /api/research/event-deliveries?limit=50` 返回事件原始载荷和投递状态，支持 `before_id` 翻页。`PENDING` 表示等待或正在尝试投递；`DELIVERED` 表示本地和 Redis 发布成功。检查 `attempt_count`、`lease_until` 和 `last_error_code` 可定位重试。Alembic0044 创建持久队列；已有事件或投递历史时，降级会拒绝删除该表。

禁止提交 `.env`，也不要把私钥写入源代码或日志。实盘交易保持禁用。生产数据库升级必须在线执行 Alembic 迁移前置检查；离线生成的 SQL 不会执行数据检查。
