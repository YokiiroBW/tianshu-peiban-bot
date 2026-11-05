# RBG：业务恢复修复已集成并部署

本批按用户批准的 [RBG 方案](../development/rbg-repair-2026-11-05.md)实施，复用 Life、Conversation、Capabilities、Memory 四个既有窗口，均为 GPT-6.1 Terra/max 普通速度。协调者集中验证、串行部署和处理原记录；当前业务数据与配置保留，没有再次空库重建。精确提交和窗口边界见 [派发记录](../development/rbg-dispatch-2026-11-05.json)。

## 发布身份与验证

- 部署源码：`a41c85d4501702694a5920f31a3e8f30140f7851`；产品代码最后变更为 `1cb1b7cde585cc97242971c45e1157e43a0d13be`，之后至发布 SHA 只有交接文档变化。
- 镜像：`sha256:9dd8a87584eab7fc43f6070291d2db4a811a8a9b5cf70e7e5c795e53e80cd114`。
- .230 容器：`c5cb3889f3413fbea2f7584a1fa8572ed3f7f1bc127cc51683608bdeca5d5eda`，2026-11-05 10:31 UTC 启动；10:57 UTC 再次读回零重启，六角色均自 10:31:36 运行。
- 数据代际仍为 `0833be12-8fba-4db7-9e22-2120e8ea13ea`。配置、环境文件与 seccomp 哈希、11 个挂载保持；迁移前后受影响原表数量一致。
- 已应用 `rbg_admission_retirement`、`rbg_result_consumption`、`rbg_plan_retirement`。已有 Sources 查询内证明复用迁移保持。
- 332 个独立必要用例通过，0 失败、0 错误、0 跳过。首次集中运行暴露的夹具问题修正后只复验受影响文件；实际天气结果链暴露的 Memory 文本分类缺口由 RBG-04 修复，再定向复验。按文件与用例名合并最终有效结果，不把重复运行累加成用例数。

本次没有追加全工作区测试、双后端矩阵、浏览器整套或烟测门禁。模型/QQ真实业务不以测试流量证明。实机检查覆盖发布身份、启动、受影响原记录与数据保留。

## 实现及实际恢复

| 范围 | 根因处理 | 原记录的实机结果 |
| --- | --- | --- |
| Life 规划与 Models 准入 | 过期/被替代的规划安全退出当前指针；Models 按原 owner、lineage 和代际封存迟到准入；历史未知单独低频观察 | 原 `life-plan:490e168e…` 于 10:31:48 退役；当前 `life-plan:4121ad9f…` 于 10:44:34 applied，watermark 已消费，下一次 due 为 12:14 UTC |
| Conversation 结果与 Life CAS | 按消费者持久化完成状态，保留总消费语义；已确认回滚的内部材料快照冲突有限重评；恢复尊重未来队列、租约、来源撤回与原回执 | 原作业 18771、receipt `077504fd-9c2e-48ca-a37a-acc964705698` 已自然消费；Life activity 明确 superseded，其余消费者 not_applicable；无需手动重放 |
| 视频证据与解析 | 规范化协议内舍入和真实输入支持的模态别名；恢复优先使用原成功模型输出，语义纠正共享原 Action 预算；持久化结果内容变化产生可追溯新回执 | 两条原 parse 失败均经正常 Actions.resume 变为 succeeded、analysis_completed=true；所有相关 intent 前缀仍只有原 2 个 Models attempt，纠正及新增模型调用为 0 |
| Memory 文本媒体副本 | 以实际已解码正文、文本 coverage、模型视图一致性识别已读文本；不再因 JSON/XML 的持久媒体副本误报未分析 | 真实结构的 Sources→ResultConsumer→Memory 天气用例已通过；没有批量重算生产历史材料 |

旧规划的 `retirement_observed_at` 已写为 10:41:50，证明周期观察实际执行。`retirement_original_status=NULL` 表示原 Models 记录仍缺失；下一次观察 11:01:50 是成功观察 missing 后的退避，不是观察失败。它不再占据当前业务位置，原结果与费用仍未知。

视频恢复保留了原失败 attempt/receipt，新能力 attempt/receipt 是原 Action 的合法后续版本：

| Action | 原模型及能力 attempt | 新能力 attempt | 成功 receipt |
| --- | --- | --- | --- |
| `9758dc58-018f-5cb9-82c3-b9dab9ee1f68` | `292729fd-ef4d-41cf-9e1e-5fc2a197720e` | `0dca04dc-e8df-4d6a-b8bd-28fbbf1fba84` | `60aeb489-4aac-47a9-a4f2-213d158e0fa9` |
| `a92eb70a-2ab5-5e32-920d-9faa9045190b` | `23b0df24-5aca-4eae-b598-0eebf7ed5a7b` | `b41814b5-1e40-4c79-af14-9941494e69ed` | `40a108a4-28b5-4974-9326-7be5ebb20922` |

两者 `requested_completed=false` 保留：完成的是实际取到的片段分析，不能表述为整段视频已看完。不要再次恢复这些已完成 Action；原失败回执仍分别为 `24d810ef-686a-407d-b870-54edb10ed946`、`eb53d21e-d19f-421d-95ed-49abddb44d5e`。旧结果的 superseded/not_applicable 也不代表已发送 QQ 回复。

## 运行观察与未决范围

10:33:38 至 10:46:12 UTC，同六进程生命周期内，18 个可观测池的新增 acquire_timeouts 均为 0。三个实际 queue 池缺少对应活动计数，不能当零；另三个角色未配置 queue 池。后台 business 的 transaction_errors 增加 374，independent sync 增加 3；这是事务内异常次数，尚不能据此归因为 SQL 故障。

巡检账本中 transport_not_sent、成长重试预算耗尽、事务异常增长、未关联的 model_completion_identity_conflict 仍为独立未决项，未因本批恢复整类关闭。10:37 UTC 定向证据中的 8 个 Memory 隔离批及另一个 blocked Growth 批未被本次修复冒称完成；之后状态以巡检原 ID 证据为准。旧 Life 作业 18182 的五次超时缺少原堆栈，不能与上述旧规划混为一条故障，也没有为其扩大所有执行超时。

## 清理、回退材料与巡检

已清理本批专属测试容器 `tianshu-rbg-test-pg-20261105`、`/srv/tianshu-rbg-validation-20261105`、构建目录内的 `source` 和 `source.tar`；按 owner、精确容器 ID 与解析后的目录核验，其他容器身份和状态保持。生产、回退镜像、发布清单、工作树和备份保留。

受影响 Models、Conversation、Life、Capabilities 四域备份为 `/srv/tianshu-rbg-releases-20261105/a41c85d45017/affected-domains.dump`，89,776,974 字节，SHA256 `c13727dbecfb9aba5c88d3679eb1f559aadb325e17f7254a0d517777d106452e`。这是本批迁移前备份；回退不得覆盖发布后新增业务数据。

现有 automation 已更新并读回：ACTIVE、每半小时、原巡检窗口、Terra/max 普通速度。使用 RBG 探针，沿用既有 JSONL 账本及增量索引，观察当前业务推进和原记录恢复；历史 unknown 保持。状态无实质变化时保持安静，新的问题、恶化、恢复或需用户处理时提醒。

## 证据入口

本地去敏证据目录：`C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbg-execution-20261105/`。

- `validation-final.json` 及 `central-1/2/3.json`：有效用例、实际运行源与初始失败。
- `release-status.json`、`final-preservation.json`、`cleanup-result.json`：发布、保留与清理收据。
- `full-chain-final-business.json`、`postrelease-pool-delta.json`：受影响业务与同进程池计数；前者的实测时间为 10:46 UTC。
- `domain-recovery-resume_video-20261105T103751Z.json`、`domain-recovery-inspect-20261105T104611Z.json`：正常领域恢复、原/新回执、模型调用总数及覆盖范围。
- `automation-update.json`：已更新定时的读回；既有汇总仍位于相邻 `rbf-execution-20261105/monitor-summary.json`，问题记录仍位于 `rb-execution-20261105/monitor-issues.jsonl`，不重建平行账本。

以上时间均为 UTC，中国时间加 8 小时。本交接为已部署范围及证据边界；文档收口提交无需再次部署产品。
