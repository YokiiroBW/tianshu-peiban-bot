# RBI-08：保留数据发布、原对象恢复与自然巡检

日期：2026-11-06。状态：**已部署并回收14:04自然巡检及14:12摘要；原业务仍有未解决项。** 用户在完成 RBI-07 本地验收后明确要求“发布部署”。唯一协调与生产写者为 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae，沿用六角色和各领域状态机。

## 发布回执与数据保留

- 分支 codex/rbi-integration-20261106 已推送 github；实际部署源码 **44590b88b4d5db4c8ea0288751bc35cb40a70348**。产品逻辑与已验证的 8034743c 完全相同，44590b88 仅协调文档收口。后续本交接的提交不改变在运行的镜像源码回执。
- .230 镜像 **sha256:fb3b0de046614e9af9a3619c1b375c0081e16bcebf0328935e06e8acc444513d**，容器 **048bd84ad13fad0ed5026f4fcb55c10ce712772d7b06535cf287528388418c78**，2026-11-06 13:47 中国时间确认六角色就绪、0 重启。
- 通过 git archive 导出准确提交，复用现有 Dockerfile 和八个固定摘要基础镜像构建。没有把工作树中的未跟踪 build/egg-info 或根原有修改混入镜像。
- 串行停止旧写者后，备份受影响的 runtime/memory/companion/life/conversation 五个 schema；备份 **444138469 字节**，SHA256 **8c2e6c408373dd61ce4b091eaa57288555c407bf38a9800d66275c3a7bd76b52**。文件位于 `/srv/tianshu-rbi-releases-20261106/44590b88b4d5/affected-domains.dump`，已核对 pg_restore 目录含五域数据，没有执行整库恢复演练。
- 六项迁移 rbi_recovery_repairs、rbi_execution_budget、rbi_memory_maintenance、rbi_growth_maintenance、rbi_activity_reconciliation、rbi_result_reconcile 已通过正常 migrate 入口应用。迁移前原生 events 约 426710 行、26.7 MB，索引随迁移正常完成。
- 停写截面的 19 张既有表行数逐项保持；数据代际仍为 **0833be12-8fba-4db7-9e22-2120e8ea13ea**。配置、env、seccomp 哈希与 11 个挂载保持，另外两个容器的 ID/镜像/状态不变。容量守护与宿主 watcher 均 active。

本次复用 RBI-07 的 45 个受影响文件、472 个现行用例通过结果，没有因文档或普通发布重跑。发布验证为镜像源码标记、迁移读回、六角色就绪、前端 HTTP 200/资源存在，以及受影响查询/原对象证据；没有发 QQ 或调用模型制造测试样本。

## 原对象结果，13:54 截面

1. 原 Memory 批 memory_batch_5c7f0abf426d4339a8094b48f274a329 经 `ConsolidationRecovery.resume_local_failure` 正常续接；真实 repair_ref 对应本次原 head 修复。前两条 Models unknown 和冻结请求/摘要保持，第二条本地 queued 已同步为 unknown。第三次原预算内尝试实际提交后返回 **upstream_http_403**，并未完成整理；14:02读回原批已failed/upstream_http_403且无retry_due。该 403 在发布前已持续出现，发布后仍出现在 gemini-native / gemini-3.8-flash-high 路由。没有猜测被拒原因、改密钥或更换配置。
2. 原 Life 活动 a94fa880-1849-5b89-9fa8-c9ab5cc31420 已通过正常原 Action 观察入口接上新唤醒，保留同一 Action/attempt/unknown；checkpoint 保存代际、job、due 和观察次数。原外部结果仍 unknown，观察租约/新 job 不等于业务完成。
3. 旧 Growth invalid_upstream_response 原尝试经 Models.status 新鲜权威事实和 `review_original` 分类，准确返回 **stop / growth_failure_stage_unknown**。原输出未保存，failure_stage/error_type/reusable 均缺证据；原失败、请求和费用事实保持，没有补造 repair_ref 或盲目重试。
4. 发布前两条旧成功回执加巡检新发现两条，共 **4 条结果仍未完成消费**。领域维护已自动找回旧 native jobs 并补合法任务，未额外 invoke 成功 Action；但 life_activity 分支继续遇到 QueryCanceled/LockNotAvailable。13:54 时其中一条已达到跨 job 的四次确定失败上限并停止，其他仍有合法队列/依赖等待；consumer attempts 不等于确定失败预算。通过 IncidentStore.get 公开记录核对了旧 Operations 恢复尝试与目标关联，没有直接写业务/预算表。

五个目标 Action 的六个历史物理 attempts 均早于发布，没有新增 invoke。原 Memory 两条 Models 请求/unknown、旧 Growth 失败回执与请求哈希已逐项核对保留。

## 尚需处理的原因与边界

**来源校验性能仍阻塞真实消费。** 现场 `sources.check_groups` SQL 可持续 12～15 秒以上，业务事务接近 30 秒并造成其他事务锁等待；同类 QueryCanceled 的 first_seen 早于本发布。两条旧活动分别为 195/196 个唯一来源节点、8252/8442 条支撑边，边 locator 均为空；只读拓扑核对均无环，最大依赖高度恰为64，拓扑满足现有plain快路径的安全条件，不应猜测一定发生深度/环回退。当前函数仍设置 jit=off，函数摘要 d965f4d6f564dcdb0b2fb5cf21faf54a。这些证据证明真实密集图上的校验/争锁尚未解决；应针对真实参数继续定位实际快路径/查询计划的执行成本，不能仅凭图规模宣布某一内部步骤为根因。后续应在 Sources 唯一校验归属优化，保持权限、撤销、范围、组语义、深度与 unknown；随后经真实修复证据和保留预算的正常入口处理原消费者，不能重置四次上限或换 receipt 绕过历史。

**Memory 上游 403 与本地恢复机制分开。** 续接路径已在原批实际执行，但 HTTP 拒绝尚未解释或解决；应从该实际路由的访问/服务端证据定位，不能将 403 自动记为未发送、免费或可无限重试。旧 Growth 缺历史证据、Life 外部 unknown 则继续准确停止/观察。

## 巡检接线

既有 automation 已通过正式工具更新，仍每半小时、同一巡检聊天和普通速度；新探针为 `C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbi-execution-20261106/chain_probe.py`，首轮发布直接检查所有查询无错误，Memory 五个参数化范围均可执行。该次属于发布检查；随后14:04自然巡检另行确认，见下节。

原 JSONL 观察/问题账本保持；新可重建 SQLite 索引为 `.runtime/rbi-execution-20261106/monitor-summary.sqlite`，摘要 JSON 仍用原路径。巡检窗口已确认接线并准备真实 business_observations 与 coverage 模板，本次未额外补跑探针。单页、单对象、账本输入完整都不能替代完整目标集合及逐对象业务证明；旧行不会自动补齐不存在的证据。无可处理变化保持安静，不重复提醒未变状态。

## 14:04 自然巡检快照

既有巡检任务于14:04自然执行，新快照 full-chain-20261106T060454Z.json 已确认实际版本44590b88、六角色全部ready、0重启、四个领域维护回调ready，探针内部各查询均无错误。原Memory target显示failed/upstream_http_403，target_attempts读到原两条unknown和第三条403，四条成功结果仍pending/life_activity未完成。current扫描6项到页尾、current_holds和history各扫描64项尚未到页尾；动态整体覆盖继续unknown，不能将本轮查询无错等同整类问题关闭。本轮JSONL与SQLite汇总已于14:12写入，累计77次观察、209条问题事件、57个指纹；本轮实际新增1次观察、9条已有指纹变化，没有新增问题类型。新SQLite首次重建的new.read包含历史，不能当成本轮新增。账本输入完整；producer覆盖仍partial，缺memory.index.recovery，业务整体覆盖仍不完整。

14:10定向补查确认四条结果的其他消费者已not_applicable，life_activity仍pending且缺proof；其中两条已达四次确定失败上限并无合法后续job/due，另两条保留有效todo任务及due，仍不能称业务完成。池等待快照见一次来源校验约26秒且另一事务锁等待，旧问题继续保留，没有用新窗口预算替换旧预算。

巡检回执补充：发布后共有9次已提交Memory.extract请求返回403，这是物理尝试数，不能换算成失败材料数。同容器且前后台worker_id未变的13:46—14:04窗口，后台business acquire_timeouts增加6、transaction_errors增加279、transaction_conflicts增加235；前台business acquire_timeouts增加35、transaction_errors增加26。前台control新增1次commit_unknown，具体事务影响未知；模型代理control当前值1缺同进程身份，不能当新增。sql_errors仍0不等于无数据库异常，queue池活动仍未知。26.048秒Sources查询/32.393秒事务与锁等待只是时点证据，不将所有失败归为同一原因。池等待证据为 .runtime/rbf-execution-20261105/pool-waits-20261106T060645Z.json。最近24条turn无需要回复的真实样本，QQ连接可用没有证明端到端送达。

## 证据

本机私有运行证据目录：`C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbi-execution-20261106/`。

- build-input/result、release-input/status、release-inventory/after、final-preservation、original-preservation：准确制品和保留证明。
- business-before-verified、business-after-start、business-after-original-resume、acceptance-first：原对象/预算/物理尝试和真实阻塞。
- memory-original-resume、life-original-observe、growth-original-review、result-incident-authority：实际正常入口与公开权威读回。
- locks-after-start、source-query-shapes、source-metadata、source-graph-safety：来源 SQL 与锁等待，不含正文/凭据。
- automation-update、首轮 full-chain-20261106T054626Z：已切换接线与逐查询结果。自然巡检已完成，见monitor-detail-20261106T0604Z、monitor-result-jobs-20261106T0610Z、natural-patrol-receipt及原路径monitor-summary.json。

本批构建导出目录及 source.tar 已按owner、精确路径、tar摘要和在运行镜像核对后清理，只删除44740711字节临时导出文件；build-cleanup.json保留回执。备份、回退镜像、旧业务文件、配置、工作树和账本保留。没有数据库清理、未知副作用重放、QQ 测试或临时调大预算。
