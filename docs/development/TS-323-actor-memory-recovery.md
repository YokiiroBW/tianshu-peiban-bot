# TS-323：联合部署的独立 actor 域备份接入

用户已授权自主生活与记忆扩展完成后一并推送部署。Memory 新增独立
`StorageRuntime(service="actor-memory", logical="main")`、`actor_metadata` schema2
与独立恢复 guard；不能把原五服务24源/两guard的历史收据作为新域的备份证明。

在独立 `TS-323/workspace`、`codex/ts-323-actor-memory-recovery` 中实施。基线
`b794fbfb65489b4bb6345cbc24b88eaea1f84e9e` 的 postgres_backend 与上一轮实机使用的
frozen-recovery 完全相同；不从当前 root 混合 index 或旧 main 缺少 PG 能力的树开始。
根任务板和实际发布仍由协调者单写。

允许路径：`ops/recovery/postgres_backend.py`、`ops/recovery/resident_snapshot.py`、
`tests/deployment/recovery/test_postgres_snapshot.py`、`ops/recovery/README.md`、
`docs/handoffs/TS-323.md`。仅将新独立域接入既有 snapshot/原生 PG dump/隔离 restore 验证，
不新增备份框架或队列，不实现生产自动恢复，不改用户 Store 或产品数据库。

复用真实 Memory 配置及原挂载 owner 定位 actor 的持久 binding 和 guard；PG 连接复用
Memory backend，数据库身份保持 actor-memory。校验 actor_metadata 当前 schema/instance/
revision/recovery 与独立 guard 一致，并覆盖 snapshot 恢复副本。域未配置时原流程不变；
域已配置但路径/guard/binding 缺失或不匹配必须明确失败，不能静默省略。
不得把新域映射到用户 main、生成假的来源或延长来源期限。

沿原测试方式覆盖独立 actor 域、漏 guard/不匹配失败和原 Memory/Knowledge 不变行为。
完整块稳定后集中审查与相关检查；实际新域 PG/备份证明由唯一协调者在发布准备/维护阶段
按隔离资源或既有冷备流程补齐。运行中不读取真实聊天、记忆正文，不写生产或推送。
交付精确 clean 提交、实测与未覆盖边界，供统一发布。
