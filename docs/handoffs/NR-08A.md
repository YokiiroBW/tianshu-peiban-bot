# NR-08A Memory 原回执恢复

基线：`59603da3a45138b6c273f3f7ef7bc0e2a52d708b`。分支：`codex/nr08-memory-recovery`。旧提交 `3bc3152f09352069724ec44f1c4ceba93dea6be5` 保留在 `codex/nr07-memory-recovery`。

## 实现

- 新增 Memory 同域 `model_outcome.py`，通过冻结 request 的原 attempt/context 读取 Models result/status。RPC `internal_error`/`broker_error` 仅触发权威补查，不加入全局重试白名单，不直接授权新请求。
- `ModelSteps` 在生成 RPC 异常后先持久化原 step 的跨边界 unknown，再补查。原成功结果可在同次调用消费；queued/running/succeeded但无有效结果只观察；确认暂时失败返回权威错误码，让现有 `consolidation._failure` 接入 defer。永久配置、来源/权限和明确取消保持终态。
- `CancelledError`/`GeneratorExit` 不进入补查。补查过程中取消直接传播，原 step 已持久化 unknown；没有吞取消或重新提交原请求。
- `defer` 和 `choose` 共用相同权威读取。控制面全部 missing/unavailable 持续观察，下一轮不再使用存储的 RPC 包装错误猜测新尝试；只有确认原暂时失败才分配独立 attempt。原已成功候选优先，rejected 语义输出不重新选择。
- 既有 `extraction/status` / 幂等 `extraction/resume` 为原 failed RPC 批次提供定向恢复：同 batch 先进入原 attempt 观察，不先重建另一批。`manifest.rpc_recoveries` 保存原 failed/error、原 step unknown/error、请求身份及最新原权威观察；attempt `first_failure` 保留 RPC 与权威状态。旧 Models 物理回执不改写。
- 仍执行原 Sources/Identity、全部 episodes revision/sequence/cursor、读集和 selected-step CAS；源撤回及迟到候选只可能应用一次。原 Models succeeded 的语义校验失败沿用有界修正/显式恢复，不走 RPC 恢复入口。
- `ModelSteps` 没有依赖 memory.extract 消息字段；同样适用于非 query embedding，保留各自执行预算。RPC 审计只更新已有对应 consolidation attempt，embedding 没有对应行时不新增任何记录。

## 集成

无需 DDL、Models 全局修改、队列/HTTP 注册或 B 范围接线。没有改 `consolidation.py`、`materials.py` 或既有 pipeline 测试。已告知 B 四个旧 lost-success fixture 用例改为同次消费原成功结果；真实 unknown 的 HeldConsolidator 语义保持。

根可继续使用既有 extraction resume 命令，携带原 `episode_id`、当前 `expected_revision`、新的幂等 `client_request_id`。两个受影响 episode 属于同 batch 时，首次恢复即处理该 batch；后续 resume 仅观察同一批，不重建原请求或伪造成功。

## 精确测试目标及证据

新增 `tests/core/test_memory_rpc_recovery.py`：RPC 包装对账成功/未知/排队/运行/永久失败/取消；确认未知独立重试和迟到结果一次应用；missing/unavailable 多轮保持观察；原 failed 批次正常 resume、旧错误审计、来源隔离和全批 cursor CAS；回执恢复后撤回；语义失败不重新选择；生成及补查阶段取消/GeneratorExit；embedding 原结果恢复及 30 秒预算。

根集中验证：上述新文件，并复用受影响的 `tests/core/test_consolidation_pipeline.py`、`tests/core/test_memory_index_retry.py` 和现有 embedding 用例。无需恢复未变的全量工作区测试。

只执行无导入、无 pyc 的 `compile(source,path,'exec')` 静态语法检查（5 个 Python 文件）及 `git diff --check`；均通过。没有运行 pytest、构建、应用导入、真实模型、QQ、生产恢复或部署。真实 RPC/原失败恢复与生产验收由根按当前授权集中执行，静态通过不等于业务通过。
