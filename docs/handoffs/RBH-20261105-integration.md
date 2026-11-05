# RBH 连接恢复与完整唤醒集成交接

六个既有窗口已完成实现，协调者已串行集成并集中验证；当前状态为待发布。当前唯一协调与生产写者：`01a1ac04-8013-7620-8d29-a2f2656d850b`。

复用现有 Runtime RecoveryPolicy、Models 权威回执、各领域原批次账本和 Operations 周期：分离计算/连接次数，保留原序号与 unknown，按完整证据有界恢复原批；Memory 合并分区等待，Life 提前唤醒补排，对话使用真实 Memory projection 合同。所有恢复继续核对来源、权限、版本、CAS 和代际，来源失效仍保留旧回执。

487 个不同用例通过，当前相关失败 0、错误 0、跳过 0。另 38 个旧测试失败均在 `ec245f9e` 原基线同环境重现，保留为既有局限，未记作通过：17 个 Growth 旧夹具缺代际，20 个 Memory 旧协议/队列身份/旧接口预期，1 个 display 旧连接替身。原过时 Growth legacy 文件因达到首次失败上限而未展开剩余历史覆盖。成功且输入未变的结果复用；Memory 两个真实显式恢复回归和新增 fixture 缺项已定向修正复验。

本批增量迁移：`rbh_not_sent`、`rbh_growth_transport`、`rbh_transport_recovery`。不修改原失败为成功，不清空业务数据，不覆盖配置，不主动发送 QQ 测试。

发布前只读快照（2026-11-05 22:22 中国时间）：仍为 `a41c85d4`，数据代际 `0833be12-8fba-4db7-9e22-2120e8ea13ea`，11 个挂载；原 15 个 transport_not_sent Growth 批次有 60 条原失败与 71 个未审核输入版本。另 1 个 invalid_upstream_response 批次独立保留，不属于连接恢复。

精确各域 SHA、返修与装配见 `docs/development/rbh-dispatch-2026-11-05.json`，各域说明见同目录 RBH-01 至 RBH-06 交接。本地去敏证据：`C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbh-execution-20261105/`，包括 `validation-final.json`、`central-1/2/3.json`、两次修复复验、三次定向基线、`business-before.json` 和发布脚本。实机发布与原材料实际结果尚待下方发布收据补齐，不以队列恢复或健康替代业务完成。
