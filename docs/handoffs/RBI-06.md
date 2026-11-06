# RBI-06 业务进度、物理未知与逐项覆盖

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbi-observability/tianshu-peiban-bot`；分支 `codex/rbi-observability-20261106`。
- 准备基线：`18d640c41bb3354ef82ffea41ff2e78b1338e3b3`；代码基线 `b32a606df8238e398c9dcd58e2e145f2ecff9fb3`。
- 本卡代码和必要回归用例提交：`c869be9b7aa916082b57a424fbef6bb8b52c2bc9`；未集成、部署或验证真实业务。

## 实现

- 既有 `scripts/ops/rbf_monitor_summary.py` 支持可选、按 `domain/kind/id/version/data_generation` 精确分组的业务观察；旧账本字段、增量游标、恢复记录和原 JSONL 保持。`version` 是字符串。同一对象的后续观察未包含原回执时保留先前回执；出现不同原 attempt ID 时并列保留并标明身份冲突。恢复后的未知原结果或费用独立呈现，不再自动等同业务阻塞。
- 每项业务观察使用领域 `business.state=blocked/recovering/recovered/waiting_sample/terminal/unknown` 及可定位 `business.evidence`。有证据的准确对象可分别显示结果；问题整体仅在本轮 `coverage.target_sets[fingerprint]` 明确完整、有集合证据、无未关联或无范围事件且所有对象均已恢复时，才标记整体恢复。非重叠批次或同对象其他版本不能关闭旧对象。缺证据或本轮没有重新观察该对象为 unknown。
- `execution.live_job`、`valid_due`、`consumer`、`consumer_lease_until`、`observer_lease` 分别保留。只有已核验目标与代际的有效 todo/doing job、领域确认的未来 due 或 running 消费者有效租约可支撑 recovering；仅 Operations 观察租约不算执行。全部执行字段明确采样且均无有效工作时才判断 blocked；缺项/格式不明保持 unknown。消费者 `revision`、`last_progress_at` 独立展示。
- 巡检问题支持 `event_kind=occurred/recurred/evaluated/resolved`、显式 `last_evaluated`/`last_recurred` 及 `unscoped_occurrences`/`uncorrelated_occurrences`；重复检查不会自动算复发。无法关联对象或问题的事实分别保留，不猜测对应失败原因。
- 输出 `coverage.ledger_input`、`coverage.producer`、`coverage.real_samples`、`coverage.queries`、`coverage.queries_by_scope`、`coverage.target_sets` 和 `coverage.fact_projection`；它们互不代替。`QueryCanceled` 保留为该查询 unknown，同名或另一条单对象找到、有限页为空均不改变整体覆盖。原有 `input_coverage` 兼容保留。
- 老 SQLite 派生索引可能已丢弃新字段：这类记录计入 `coverage.fact_projection.legacy_*_records` 且该投影为不完整，不能把完整的 JSONL 输入误称为新增事实已覆盖。需要重建时使用**新的独立 SQLite 文件**重新读取现存原 JSONL；旧索引和账本保留。若原日志已轮转/缺失，重建只能覆盖实际可读来源，不能据此声称完整历史。

## 现有巡检写入格式与约束

根在既有追加写入模板中依据真实领域查询可选添加下列字段；不得从健康、空队列、普通 job 成功或 Operations 观察租约制造领域提交证据：

```json
{
  "business_observations": [{
    "fingerprint": "issue-fingerprint",
    "target": {"domain":"memory", "kind":"batch", "id":"batch-1", "version":"1", "data_generation":"generation-1"},
    "business": {"state":"recovering", "reason":"waiting_dependency", "evidence":{"kind":"domain_state", "ref":"batch-1"}},
    "original_receipt": {"attempt_id":"attempt-1", "status":"unknown", "cost":null},
    "execution": {"live_job":{"job_id":123, "state":"doing", "valid":true}, "valid_due":null,
      "consumer":{"id":"memory", "state":"running", "revision":4, "last_progress_at":"2026-11-06T00:00:00Z"},
      "consumer_lease_until":"2026-11-06T00:05:00Z", "observer_lease":"2026-11-06T00:05:00Z"}
  }],
  "coverage": {"producer":{"complete":false,"missing":["memory"]}, "real_samples":{"count":0},
    "queries":{"memory_original_observation":{"scope":"overall","status":"unknown","error_type":"QueryCanceled","sqlstate":"57014"}},
    "target_sets":{"issue-fingerprint":{"complete":true,"evidence":{"kind":"domain_scan","ref":"target-set-1"}}}}
}
```

`live_job.valid=true` 只能来自对同一对象、版本、代际和有效租约或合法队列的真实核验；`valid_due` 是领域确认的未来时间。`business.evidence` 可用 `{kind,ref}`、`{kind,path}` 或可定位短引用。`target_sets.complete=true` 必须有本轮完整目标集合证据；单对象、空页或账本字节已读完均不够。未取得逐项事实时省略，输出保持 unknown；不得填零费用。对象状态是最近观察的事实，`last_evaluated` 指出该观察时间，不等于对当前业务的实时保证。

## 有界原请求探针，根接入既有执行器

新增 `tianshu.operations.rbi_memory_observation` 只提供 SQL/参数和结果包装，不创建调度器或修改业务数据。`current_query(after='',limit=64)` 扫描分区活动指针；`current_holds_query(after='',limit=64)` 扫描已隔离批，覆盖没有活动指针或模型尝试的 hold；`history_query(after=None,limit=64)` 按 `(batch_id,number)` 读取有限候选后，用 Models 原 attempt 的主键读取权威状态；`target_query(batch_id)` 和 `target_attempts_query(batch_id,after=0,limit=64)` 专查指定批及其原尝试。`target_episode_query(episode_id)` 按主键返回原材料的版本、序列、当前提取状态、缺失和不含正文的游标元数据；`target_batch_episode_query(batch_id,episode_id,revision=...,sequence=...)` 只检查该准确版本是否包含在指定批的冻结清单。最大页为 256，均使用参数化 SQL；批和尝试的现有主键用于 keyset，未新增 DDL。历史和指定对象输出中的 `model_status=NULL` 是未取得 Models 回执，不代表未提交或成功。

同步 psycopg 接线示意（在现有只读连接、4 秒 statement_timeout 和巡检单写者中调用）：

```python
from tianshu.operations.rbi_memory_observation import history_query, page_result, target_query, target_result

sql, params = history_query(after=history_cursor, limit=64)
try:
    rows = conn.execute(sql, params).fetchall()
    history = page_result("history", rows, after=history_cursor, limit=64)
except psycopg.Error as exc:
    history = page_result("history", None, after=history_cursor, limit=64,
                          error_type=type(exc).__name__, sqlstate=exc.sqlstate)

sql, params = target_query(batch_id)
try:
    target = target_result(conn.execute(sql, params).fetchone())
except psycopg.Error as exc:
    target = target_result(None, error_type=type(exc).__name__, sqlstate=exc.sqlstate)
```

当前活动页、隔离页、历史页和指定批尝试页分别维护游标。`next_cursor` 非空时保存后继续；页到尾时 `wrap_required=true`、`last_scanned` 保留此次水位，下一轮有界扫从初始游标开始，避免遗漏稍后插入在前面的随机 ID。查询取消时保留原游标。`page_complete` 只说明该页到当前尾部，`overall_complete` 一直为 null：这些是动态 live keyset 页，跨页不构成冻结快照。准确版本包含于冻结批也只证明该片段参与，仍需对照原材料当前 revision/sequence、提取状态、缺失、代际及应用证据；`completed_with_missing`、批 applied 或一条 Models 回执本身均不能证明原材料完整完成。

## 必要验证与限制

已编写 `tests/operations/test_rbf_monitor_summary.py` 的对象版本、原 unknown/费用、观察租约、合法工作、复发时间、缺失指标、无真实样本、查询取消、未关联事实和旧派生索引用例；`tests/operations/test_rbi_memory_observation.py` 使用项目 PostgreSQL 夹具及实际 Memory/Models 迁移，覆盖有限页、隔离 hold、Models 与本地状态分离、指定目标及取消结果。建议根集成后运行：

```text
python -m pytest tests/operations/test_rbf_monitor_summary.py tests/operations/test_rbi_memory_observation.py -q
```

后者需要 `TIANSHU_TEST_POSTGRES_ADMIN_DSN` 指向明确授权的临时测试 PostgreSQL；跳过不等于通过。已做本卡完整 diff 静态审阅、`git diff --check` 与四个 Python 文件的 AST 解析；这不是功能测试。根同时检查实际部署的 SQL 计划是否使用现有主键，并负责现有 probe 与写入模板的装配、跨域真实字段核验及必要验证。实现窗口未运行测试、构建、烟测、生产、真实模型或 QQ，未变更活跃巡检文件、历史账本或任何共享入口；旧日志缺字段、没有真实样本及尚未关联的事件仍按 unknown 报告。
