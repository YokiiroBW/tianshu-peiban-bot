# 天枢当前运行全链路复盘

检查窗口为 2026 年 11 月 4 日 18:14—18:34，时区 Asia/Shanghai。最后一次业务快照为 18:33:50，使用同一 PostgreSQL 只读、可重复读事务。对照点为今天 13:33 的检查记录。

**结论：运行基础正常，业务链路仍有明确缺口；下午又出现了新的失败。** 主要问题集中在不完整模型响应的资源占用、媒体缺失后的整条输入失败、日程续接合同，以及语义错误的恢复覆盖。其他分区仍在推进，不能把这些局部失败写成全系统停摆，也不能用健康检查通过代替业务成功。

实际运行源码为 `03130711fd6961dde31335a300aae23bbf58d9b7`，容器 `tianshu-core`，镜像 `sha256:34622ad09167658e59a0f6cffa748de0c3b46d76069e9126b250e5f5f356311e`；自今天 03:32 启动后 restarts=0，11 个挂载保持。本轮只读诊断，未修改产品代码、配置、数据或部署，未重试失败任务、调用新模型或发送 QQ 测试消息。鉴权读取使用正常登录与退出。

| 链路 | 本次核实结果 | 结论 |
| --- | --- | --- |
| 入口、进程与连接 | 页面及静态资源可读；三进程 ready，两个 QQ 连接 available；容量守护 active/healthy | 运行基础正常 |
| 输入接收 | 启动后 8 条 channels.ingest 失败且事件仍 accepted，较 13:33 增加 1 条 | 部分消息未进入后续处理 |
| 模型执行与调度 | 下午 23 次不完整响应；9 次后台排队超过 30 秒，最长 229.12 秒 | 失败影响共享资源，见下文 |
| Memory | 启动后 276 批、1192 条材料已提交；2 个失败批共 6 条材料；2 个反复不完整批共 15 条材料；另 1 个 2 条材料批处于恢复过程中 | 正常推进与局部失败并存 |
| Growth | 主处理分区没有 blocked_material；独立重试表有 1 个 blocked 批、4 条材料；普通待处理为 2 条新材料 | 不能只查主分区或只查重试表 |
| Life 规划与执行 | 澄汐今天仍无日程；智乃下午新增 4 次规划拒绝，但旧日程仍在推进，21 个活动时段 completed、1 个 partial、8 个 planned | 规划恢复缺口，不是调度器整体停止 |
| 日记与索引恢复 | 两角色 11 月 2、3 日日记均 reviewed；7 个经历过第二次索引尝试的任务均 succeeded；现存 6 条无向量经历为 completed_with_missing | 未发现原日记收口和索引恢复问题再次出现 |
| 检索相关 | 下午 77 次 rerank 全部 succeeded；706 次 embed 成功、1 次 timeout_unknown；当前向量 SQL 窗口仍为 2 秒 | 当前样本未再现重排超时；本轮没有发起新的完整检索模型调用 |
| 表达与发送 | 启动后有 1164 条 observed、不要求回复的 turn；没有新的 expression plan 或完整回复发送样本 | 当前版发送闭环缺少自然样本，不能据此判定故障或验收成功 |
| 外围与监控 | 插件、媒体、图像等读取入口可用；基础设施 compose.inventory/docker.stats 仍 partial；Loki、Vector、Prometheus up，最近日志距采样约 6 秒 | 外围实际执行不由读取成功证明；监控存在业务覆盖缺口 |

1. **优先处理：重复不完整响应占用后台资源。**

   两个批次分别有 3、12 条材料，已连续发生 14、8 次 `upstream_response_incomplete`；它们仍有后续重试时间。另一个批次发生过 1 次相同错误，合计 23 次物理请求。它们均为 `gemini.generate`，已记录 `output_observed=true`，但 `result` 和 `usage` 均为空。观察到返回内容不代表提案完整；不能据此确定上游缺失原因或实际收费次数。

   当前该配额组 concurrency=4、interactive_reserve=2、background_max=2，unknown_hold_seconds=300。9 次超过 30 秒的 Memory 排队，都在同组某个 unknown 的 hold_until 前后约 0.3 秒内启动；等待范围为 33.00—229.12 秒。结合调度器保留 unknown 占位的实现，证据支持这些等待由未知请求占位释放触发。未观察到交互对话被拖慢，不能把后台影响直接扩大到全部功能。

   Models 的异常分支只保存 status/error_code，没有保存已收集的响应诊断和 usage，限制了不完整原因分析；统一重试只封顶退避间隔为 900 秒，不会因相同材料反复出现同一错误而转入不同恢复策略。建议在既有 Models 账本保存有界、去敏的响应形状、完成判据和可取得的 usage，将同批重复失败纳入退避及公平准入。只有证据能证明上游已终止时才调整占位；真实 unknown、迟到观测和既有取消语义继续保留，不能直接取消所有 unknown 占位。

   对应现有队列：SD-04、SD-05、SD-08、SD-20。源码：`models/broker.py:502,618`、`models/scheduler.py:152`、`models/retry.py:52`。证据：`closing.json` 的 incomplete_models、model_quota_limits、long_queues_unknown_overlap。

2. **优先处理：日程候选与执行校验存在衔接冲突。**

   澄汐 07:00 的原提案选择了 waiting_reason=`goal_not_active` 的旧活动，当前目标已完成；原冻结请求仍可纯解析复现 `planner_activity_unavailable`。错误未进入有界纠正，repair_due_at 为空，今天仍没有日程。旧活动可以作为背景出现，但不应同时被提供为可继续执行的候选。

   智乃最新拒绝可精确复现：系统给出的 continuation_key=`discovery_page` 对应下一步 `content.discover`，旧检查点为 `content.read`；模型选择了下一步能力，规划校验却要求 capability_id 等于旧检查点能力。提示词确实要求填写旧能力，但候选中又暴露下一步能力，给同一选择留下两种含义。原结果因此整份拒绝，而不是执行下一页。下午新增的 4 次拒绝中，3 次可复现这一冲突。

   建议在 Life 归属内统一执行资格和候选构造：模型选择已冻结的续接 key，由程序从原回执取得真正的能力与参数；目标失活、等待人类、未知外部结果等不合格活动只作为背景。已知完整但语义无效的提案应进入适用的有界纠正，不能当成成功规划指纹长期跳过。保持已有合法时段、原活动身份、来源检查和外部动作去重，不恢复已完成目标，也不放宽任意能力执行权限。

   对应 SD-12、SD-13、SD-14，直接关联 PF-06、PF-09。源码：`life/continuation.py:142`、`life/planner.py:203,400`、`life/planning_engine.py:308,449`。证据：`followup.json` 的 planner_rejections、`closing.json` 的 planner_watermarks/life_slots。

3. **优先处理：媒体不可用导致整条消息失败。**

   原 7 条问题仍在，17:58 又新增 1 条，合计 8 条失败输入。新增事件有 39 个输入片段，其中两段视频为 `provider_media_unavailable`、media 为空，同时有 8 张图片已成功取得。媒体适配层已经记录不可用状态，Conversation 的验证仍对任意媒体片段 media=None 抛出 `input_media_required`，使整条消息无法继续。

   建议统一已有媒体缺失表达：保留可用文本、图片和原顺序；不可用部分形成明确的缺失记录，不推测其内容。需要完整媒体才能完成的具体能力按缺失情况返回，不让一个片段下载失败阻断整个输入。恢复既有 8 条记录时沿用原 event/turn 身份并遵守去重，不能重造消息或顺带重放外部发送。

   对应 SD-06、SD-18。源码：`channels/ingress.py:154`、`conversation/service.py:720`。证据：`followup.json` 的 ingest_failed/ingest_part_metadata。

4. **需要补齐：Growth 的一次纠错没有覆盖 JSON 语法错误。**

   智乃 18:00 新增一个 4 条材料的独立 blocked 批。原反思模型调用 succeeded、正常 stop，但返回 JSON 在第 1 行第 135 列解码失败。复用原请求与原结果，`prepare_reflections` 再次得到 `reflection_output_invalid`；repair_count=0、due_at 为空。

   当前自动纠正白名单只包括 support 引用与重复提案两类错误，JSON 语法错误不在其中；其解析异常也未附带位置诊断。因此，这是现有纠错覆盖缺口，不能描述成“已经执行过一次纠正仍失败”。后续其他材料仍能处理，不是整个 Growth 被堵住。

   建议在已有 Growth 恢复归属中覆盖已知完整响应的 JSON/schema 可纠正错误，保存有界诊断并继续沿用一次纠正上限；修复后的输出仍经过完整身份、证据和写入校验。权限失效、来源撤回、真实 unknown 不可混入这一分支。

   对应 SD-10、SD-15。源码：`companion/reflection_output.py:28,260`、`companion/growth.py:395,494`、`companion/growth_retry.py:76`。证据：`detail.json` 的 growth_parser、`followup.json` 的 growth_correction_coverage。

5. **需要改进：Memory 的提案纠错仍有实际失败样本。**

   新增 4 条材料批的原提案重复创建别名；一次纠正调用虽成功结束，返回 JSON 却在第 7 行第 10 列语法错误，最终 `memory_consolidation_output_invalid`。原 2 条材料批仍因纠正输出的重复 create 失败。两批都是原回执纯解析复现，未发起新模型请求。

   需要修正前次记录的一处表述：旧 2 条材料批的原调用首先违反 support_groups 的 maxItems，纠正输出才是 duplicate_create；不能说原调用与纠正调用都是 duplicate_create。最终旧故障仍在的结论不变。

   建议复用当前冻结短引用与一次纠正机制，把创建别名的作用范围、互相引用和 schema 诊断表达清楚；用这两条真实失败形状做定向验证。不能靠放松证据校验、截掉材料或无上限追加模型调用使任务表面成功。

   对应 SD-08、SD-15，保留 PF-11。源码：`memory/consolidation.py:568,638`、`memory/extraction_contract.py`。证据：`followup.json` 的 failed_memory_parsed。

6. **性能问题：智乃日程读取稳定出现约 8.7 秒耗时。**

   两次鉴权只读请求耗时 8.609 秒、8.750 秒，均返回 40 个时段、9 个未完成活动；澄汐对应为 0.437 秒、0.453 秒。两次样本足以确认这次页面读取慢，不能据此给出长期 p95。数据库无锁等待、主机负载低；具体耗时尚未分解到 SQL、来源证明和序列化，不能把原因武断归为缺索引或 N+1。

   建议先在当前读路径做阶段计时，确认瓶颈后复用批量来源证明及紧凑读投影。只读取日程应避免反复携带大体积检查点；来源撤回和权限生效不能因缓存而延迟。

   对应 SD-12、SD-19、SD-20。源码：`life/day_plan.py:76,97`。证据：`http-current.json`、`http-day-plan-recheck.json`。

7. **监控覆盖：进程和日志健康没有表达业务阻塞。**

   当前 Prometheus 三个目标 up，活动告警为空，Loki 持续收日志。已配置规则主要覆盖日志采集、存储和管道错误；与此同时，已存在失败输入、独立 Growth 阻塞和反复失败的 Memory 批次。因此“没有告警”不能用来判断这些业务成功。pipeline_errors 查询结果为空向量，本次不将它写成已测得零错误。

   建议在已有诊断和监控中补充少量业务结果：失败输入年龄、两个 Growth 阻塞位置、同批重复失败次数、unknown 占位与队列等待、规划 rejected 与 applied 分别计数。模型 succeeded 不应直接变成 Memory/Growth/Life 成功指标，不另建平行监控系统。

   对应 SD-19、SD-20。证据：`observability.json`、`closing.json`；规则位置 `deploy/core/observability/alerts.yaml`。

历史修复的保留情况也已核对：日记旧日期正常 reviewed，临时索引失败能完成第二次尝试，原 unknown 没有被擦掉；Growth 主分区没有重现原预算阻塞；智乃已有 21 个完成活动时段，没有退回“全天最多四项”。本轮观察到的长等待发生在排队阶段，不等于“排队又扣除了执行预算”。这些结论来自当前记录及调用链，不等同于重跑所有历史测试。

后续实施顺序建议为：先把 Models 不完整响应诊断与重复失败资源影响查清，同时在独立归属内处理输入缺失与 Life 续接合同；再补 Growth/Memory 定向纠错；最后按实测阶段耗时处理页面读取，并补齐现有业务诊断。以上可纳入现有 SD 卡，不新增另一套任务编号。本轮未改变开发队列状态，也未启动实现。

本次复用原请求和原回执做纯解析，没有触发业务执行；检查覆盖主机、进程、数据库、队列、模型账本、输入、记忆、成长、日程、日记、外围读取及日志监控。真实 QQ 回复发送、图片生成及其他外部副作用没有新增样本；语音仍按原配置未绑定。完整成功与真实使用效果继续按实际样本判断。

非正文证据保存在 [本次检查目录](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814)。主要文件为 [最终业务快照](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/closing.json)、[原请求纯解析与输入元数据](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/followup.json)、[实际容器与健康状态](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/host-corrected.json)、[接口读取](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/http-current.json)、[日程读取复核](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/http-day-plan-recheck.json)、[监控读取](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/observability.json)。证据不包含聊天、日记、模型正文或凭据。初次探针使用旧服务名/路径及两个错误 SQL，均已纠正，不将这些探针错误算作生产故障。

源码路径均相对于当前统一检出 `C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu`；deploy 路径相对于该检出根目录。以上均为本次时点结论，后续自然任务会继续改变数量。
