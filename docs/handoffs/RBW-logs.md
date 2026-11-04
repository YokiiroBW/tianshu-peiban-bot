# RBW-logs 交接

## 身份与范围

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 本包代码和回归用例提交：`55e4fcdb4e62c0ef05eee1d4c6965ebc2f1ef672`；本交接文件位于其后的独立文档提交，集成时需包含两个提交。
- 分支：`codex/rbw-logs-20261105`。只修改日志白名单及本交接；未修改 Supervisor、共享路由、产品装配、公共迁移、配置或其他窗口。
- 未操作生产、清理数据、推送或部署。

## 本包实现

- `EventLog` 复用原进程 JSONL 追加文件、实例和序号。业务发出事件只做去敏及内存入队；后台按最多 128 条批量追加和 `fsync`，原偏移及写入字节比对后恢复部分写入，暂时失败自动退避重试。正常关闭先停止入队再排空，最长等待受 `drain_timeout` 约束，未排空留下非干净状态。
- 默认软提示 4096 条、硬上限 262144 条和 64 MiB 编码字节；在写线程很慢或磁盘不可用时不让无关业务等待 `fsync` 或产生日志 503。资源上限耗尽时新日志会发生实际缺口：状态记 `missing_count` 和 `possible_missing_first/last`（后二者是可能范围，并不声称范围内每个序号都缺失）；不采样且不把该缺口报为完整。查询本窗口当前进程可读取实时 `status()`；其他进程只读每批的持久 checkpoint，不能当实时生成计数。
- 每个 JSONL 配套 `.state.json` 保存生成序号的最近观察、已 `fsync` 字节及序号、缓冲字节、错误和 `clean_shutdown`。强退或写失败时该侧文件可能滞后，显示可能未持久范围；业务执行和外部效果仍以领域事务/原回执为准。
- Python 日志以单行 JSON 保留真实 `level`、模块、允许的关联 ID、错误族和栈帧，脱敏环境凭据与常见敏感字段；队列库仍只记录安全任务元数据。`output_record` 使非结构化输出显示 `UNCLASSIFIED`，不把未知级别伪装成 INFO。
- Vector 继续从原始 JSONL、使用原持久 checkpoint 和磁盘 buffer；两个输出 sink 启用端到端 acknowledgement。8 MiB 文件行上限配合监督输出 1 MiB 字符上限及 JSON 转义；无效时间戳不再因强制解析直接丢弃，保留标记。Vector checkpoint 可证明的阶段需要按实际版本验证；本包不将其解释成逐条 Loki 成功回执。
- 日志页查询固定原始范围、过滤器、所有可见源文件的 inode 和字节边界，服务端按 `(timestamp_ns, instance_id, sequence, event_id)` 排序和分页，从原始文件完整枚举同时间戳；每页最多 1000 条，候选数据约 4 MiB，单条可独占一页。只在确需续页时保存快照清单，24 小时后游标过期。刷新开启新快照，以捕获迟到追加或新来源。无 checkpoint 尾部仍可查，但记录 `persistence=unverified`；原文件缩短、替换、格式损坏或未干净退出均显示缺口。
- 增加 Operations 用的逐来源字节位置增量读口，轮流读取已 `fsync` 且有 checkpoint 的安全结构字段，消费者可按 `event_id` 幂等。增量读取不交付日志正文，也不使用全局最大时间或最大 ID 推进；没有 writer checkpoint 的旧 Supervisor 文件和未 checkpoint 尾部不会伪称已持久。

## 接口与装配

### 原日志 HTTP

共享入口保持 `POST /api/v1/system/logs`，需要协调者/Capabilities 将 body 转发给：

```python
await diagnostics.logs(
    start_ns=body["start_ns"], end_ns=body["end_ns"],
    role=body.get("role"), level=body.get("level"), module=body.get("module"),
    correlation_id=body.get("correlation_id"), trace_id=body.get("trace_id"),
    job_id=body.get("job_id"), attempt_id=body.get("attempt_id"),
    keyword=body.get("keyword"),
    exclude_conversation=body.get("exclude_conversation", False),
    cursor=body.get("cursor"), limit=body.get("limit", 100),
)
```

时间为 Unix 纳秒，开始包含、结束不包含，单次范围最大 24 小时；角色过滤接受六类新角色及实际监督子进程名。可选字符串空值视为无过滤。游标续页必须保留相同时间范围和过滤器，`limit` 可改变；不同过滤器返回 `log_cursor_query_mismatch`，过期返回 `log_cursor_expired`。旧 `streams` 字段保留且内容已经脱敏，`next_end_ns` 恒为 `null`，禁止继续以时间减一分页。

响应包含 `entries`、`next_cursor`、`streams`、`next_end_ns`、`coverage`、`freshness`。`coverage.complete` 仅针对 `scope=observed_local_sources` 且当前页已到末尾；`snapshot_complete` 说明可枚举性，`has_more` 指示续页，`generated_coverage` 与 `collection_coverage` 明确为 `unknown`。`freshness` 提供采样和快照时间、快照过期时间、`late_arrivals=refresh_with_new_snapshot`、writer checkpoint、磁盘可用字节、Vector/Loki 的 `available/unavailable/not_configured`；Vector 的逐源 `collected_sequence` 和 `delivered_sequence` 为 `null`，不能从可访问性推断已交付。默认导出可使用同一脱敏 `entries` 及游标取全范围，无额外导出端口。

### 复用 writer 的 Supervisor 改动

Runtime 窗口负责修改共享 `supervisor.py`，同一 writer API：

```python
writer = EventLog(self.logs, role, prefix="stdout-", uid=self.uid, gid=self.gid)
await writer.open()
# 在逐行读取循环内：无文件写入和 fsync 等待
value = await writer.emit_output(line, self.values)
# 正常结束读取后：
await writer.close()
```

`value` 是已去敏、包含原等级与模块的完整 schema 2 记录，可供父进程的其他输出使用；父进程 stdout 镜像仍须避免向子进程管道反向施加无限等待。`EventLog.status()` 是日志观测状态；不能用日志写失败、Loki 断开或 `ready` 作为其他业务入口不可用的依据。若要在 API 进程显示该进程的实时内存状态，装配时传 `RuntimeDiagnostics(database, client, settings, events=events)`。

### Operations 统计读口

```python
batch = await diagnostics.log_changes(checkpoint=checkpoint_or_none, limit=500)
```

返回 `entries`、`next_checkpoint`、`coverage`、`freshness`。条目是时间、事件/实例/序号、角色、级别、模块、结果、错误族、路由和 job/attempt/trace 等受限标量字段，不含正文。checkpoint 对每个文件保留身份和消费偏移；新增源和旧时间戳的迟到追加会在后续读取。消费者必须按 `event_id` 去重，并将 `next_checkpoint` 与自己的增量投影同事务提交；`coverage.complete=false` 或 `caught_up=false` 时不得称统计完整。该读口无日志页的 24 小时范围限制，供统计投影按分钟/小时增量处理。

## 迁移与验证

- 本包无 DDL、数据库迁移或公共 migration 注册；仅部署 `deploy/core/observability/vector.toml` 的域配置文件。需确保原日志目录、Vector checkpoint、Vector 磁盘 buffer 与容量守护的归属和持久挂载由协调者装配。
- 已编写 `tests/runtime/test_rb_logs.py`：慢写/部分写恢复、条数与字节耗尽、等级和去敏、超 1500 条同纳秒多源续页、快照晚到/断线、强退尾部、增量位置、源变化和响应字段等；这些均为已写用例，不是运行结果。
- 仅做静态检查：完整改动审阅、`git diff --cached --check`、Python AST 与 TOML 解析通过。最初系统 PATH 无 `python`，静态解析使用 Codex 附带 Python。**本窗口未运行测试、构建、烟测或实际 Vector/Loki 验证。**

## 待协调者集中完成与风险

1. 合入本包后接好上面的 Supervisor、HTTP 参数和 `events` 实例；新前端使用 `next_cursor` 和刷新新快照，Operations 接入 `log_changes`。本包未越界修改这些共享入口，当前单独分支并不能宣称完整页面或 Supervisor 接线已完成。
2. 集中运行本包及受影响调用方测试，并对实际 Vector 版本验证 VRL、ack、磁盘 buffer 已满、Loki 断线/恢复补传、源 checkpoint、进程强退和真实恢复。`Vector available` 不等于逐条已入 Loki；由于缺少可公开验证的逐源 Loki 回执，本实现始终将该序号标为未知。
3. 查询直接读取现有本地追加文件而非用 Loki 截断的查询结果，缺少本地源或历史不在本地时只报覆盖缺口，不能宣称覆盖 Loki 中额外/历史数据。每页会扫描所选快照的全部文件以保证任意同时间戳和乱序可枚举，内存有页大小上限，但大量长文件时磁盘 I/O 和延迟尚未实测；应在集中验收评估，不以快照 cursor 本身宣称高性能。
4. 对任意非结构化第三方输出，去敏依赖已知凭据和敏感字段识别，无法证明任意自由文本均不含私人内容；业务/依赖自身不应输出聊天正文和完整提示词。超过监督输出上限的原行会明确显示为已脱敏的超大行，不声称原文完整保留。
5. 实际资源耗尽时会产生已标明的日志缺口；进程被强退且 checkpoint 也不可写时无法推断精确最后生成序号，必须保留 `unknown`。本提交未验证容器、真实消息或业务收敛。
