# RBH 连接恢复与完整唤醒：已验证并发布

本批六个既有窗口的实现已由协调者串行集成、集中验证并保留数据发布；原 15 个连接失败 Growth 批次已通过正常队列恢复完成。当前协调聊天为 `01a1ac04-8013-7620-8d29-a2f2656d850b`。

## 发布与数据保留

2026-11-05 23:31 中国时间发布至 .230。源码 `aa5662b74e172facf2cda62294adfeb23a15a178`，镜像 `sha256:c10784a4919ebaefce6460396e1b94813ab80f0140c1ab36af15eeef43cee303`，容器 `b93e66ec137c9743eacbe361b66eb5e08f345d84384cb9e4ccb0c1e430c1c34b`。

23:38 读回六进程均就绪、零重启，HTTP/前端 200，配置、环境文件与 seccomp 哈希、11 个挂载保持。数据代际仍为 `0833be12-8fba-4db7-9e22-2120e8ea13ea`。新增迁移仅 `rbh_not_sent`、`rbh_growth_transport`、`rbh_transport_recovery`；迁移前后受核原表数量一致。

受影响 Runtime/Memory/Companion/Life 数据域备份：`/srv/tianshu-rbh-releases-20261105/aa5662b74e17/affected-domains.dump`，159,754,673 字节，SHA256 `ee3385292018c3b81fb9c01a3dbc30498001c15391ea4ea8042bbb91400e3086`。备份属于迁移前截面，回退不得覆盖发布后的新业务数据。

## 实现与实际结果

| 范围 | 实现及验收 |
| --- | --- |
| Runtime / Models | 复用 RecoveryPolicy 和原 ModelReceipt。完整建连/发送/输出证据证明未发送才登记连接次数；原 attempt 序号不改，计算额度和每 lineage 16 次连接额度分离。单探针、冷却、当前版本、权限、来源及 unknown 边界保持。TLS 不凭类型新增永久阻断。 |
| Growth / Memory / Life | 原冻结请求绑定和同事务来源复检后，按公共预算安排原队列；失效时保留旧回执。历史扫描有界；Memory 以分区最早 due 合并等待，Life 提前唤醒补回有效后续任务并服从作息。 |
| Conversation | 两处真实 Memory projection 调用去掉不存在的 reflection_limit，真实服务联合用例覆盖个人、群、无人物、来源撤回与当前学习规则。 |
| 原 Growth 批次 | 23:36 核对：原 15 批全部 completed，71 个原输入版本均 reviewed，缺失输入 0；60 个旧 failed attempt、原 ID、请求摘要和 Models 失败回执完全不变，新增分类恰为 60。恢复由既有 Operations 周期和领域队列自动完成，没有直接更新业务行或人工模型探测。 |

Life 当前两角色都保留持久唤醒；夜间唤醒依真实作息延后，不把未来 due 当卡住。旧 RBG unknown 继续原观察，未以本批健康推断其外部结果。

23:32—23:38 同一六进程生命周期内，18 个可观测 business/control/sync 池新增 acquire_timeouts、transaction_conflicts、sql_errors、commit_unknowns 均为 0；transaction_errors 合计新增 1，未据此归因为 SQL 故障。其他 queue 池活动计数缺失仍为 unknown。

## 必要验证及局限

合并实际结果后，487 个不同用例通过，本批相关失败、错误、跳过均为 0；未变成功结果直接复用。首轮发现的两项 Memory 显式恢复回归已修复为同事务数据库时钟；其他新增回执/迁移夹具补齐后只复验受影响范围。

另有 38 个失败在 `ec245f9e` 原基线同一隔离环境重现，完整保留、不计通过：17 个 Growth 旧夹具缺代际，20 个 Memory 旧协议/接口/队列身份预期，1 个 display 旧连接替身。过时 Growth legacy 文件首次达到失败上限后未展开剩余历史覆盖；当前代际和原批恢复由本批真实调用链用例覆盖。没有追加全工作区、双后端或整套浏览器验证。

原先另 1 个 `invalid_upstream_response` Growth 批次仍 blocked，未将它当作未发送恢复；未关联 completion identity、历史未知结果等其他巡检问题未整类关闭。没有主动发送 QQ 测试。

## 收尾与证据

已按 owner、精确容器 ID 与解析后的目录清理本批验证 PG、构建 source 与 source.tar；生产、非目标容器、回退镜像、备份、工作树和收据保留。原半小时巡检已更新为本版源码和 RBH 探针并读回 ACTIVE；状态未变时安静，只报告实质变化。

精确域提交与装配见 `docs/development/rbh-dispatch-2026-11-05.json`，域交接见 RBH-01 至 RBH-06。本地证据位于 `C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbh-execution-20261105/`：`validation-final.json`、central-1/2/3、memory-fix-1、life-fix-1、baseline-1/2/3、`release-status.json`、`business-final.json`、`original-preservation.json`、`final-preservation.json`、`pool-delta.json`、`cleanup-result.json`、`automation-update.json`。收口文档提交不改变已部署产品，无需再次构建发布。
