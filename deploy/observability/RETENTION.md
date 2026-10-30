# 运行日志留存与清理

只处理运行日志；聊天、记忆、业务回执、模型请求状态与配置不属于此范围。

`retention_days` 控制 Loki 按采集时间保留的天数，默认 30 天。原仅用于日志完整性账本的 `log_max_events` / `ledger_max_events` 和 `--max-events` 已退役；不会再以 10,000 条作为日志准入或查询上限。Vector 原生 checkpoint、ack、磁盘缓冲及重试保持，缓冲满时阻塞而不是丢弃。

日志查询通过既有 TLS 角色网关直达 Loki。collector 错误、Loki 推送拒绝、Vector 缓冲、源目录容量、磁盘容量和清理失败继续监控；日志完整性逐条核对、待确认和缺序指标已退役。历史 `integrity.sqlite` 不再打开或清空，作为受控回退材料保留。

源文件只有在原生段已闭合、Vector 0.58 的文件指纹唯一匹配、持久 checkpoint 位置等于文件末尾且文件未变化时，才允许回收。guard 还校验有效 Vector 配置中的文件指纹、起点、ack、Loki 磁盘缓冲及阻塞策略；条件不满足则保留源文件并报告清理失败。活动文件和未完整确认文件不会回收。

手动清空由容器内 writer 管理命令发起：

```text
docker exec <天枢容器> /opt/tianshu-venv/bin/python -B /opt/tianshu/application/deploy/observability/manage_logs.py clear --settings /config/observability/guard.json
```

操作持久保存一个毫秒级截止点；查询同时过滤截止点之前发生或采集的日志，因此旧源文件与 Vector 缓冲后来重放也不会重新显示。Loki 对已采集日志的物理删除异步进行；截止点之后才采集的旧事件依靠查询过滤隐藏，并由 Loki 的时间留存最终回收。时间过滤使用日志原始 RFC3339 时间和 Loki 采集时间；时间戳错误或未来时钟不保证与旧账本的首次发现时间完全一致，毫秒边界内可能多隐藏不足一毫秒的新事件。

切换时保留原配置、挂载、Vector 数据、Loki 数据和旧 `integrity.sqlite`；如旧版已有非零清空截止点，须先迁入操作级 `clear-control.json` 并验证过滤，再切换。`clear-control.json` 和告警数据库不涉及业务数据，须随 guard 数据目录保留。回退到旧版前，若新版已执行清空，应由发布者先将截止点映射回旧版清空控制，再恢复旧 guard；否则不能保证清空结果保持。
