# DEP-C 接线边界与最小接口提案

2026-09-22 DEP-F 更新：[LIFECYCLE.md](LIFECYCLE.md) 已实现停写/确认/备份的生命周期适配与 DEP-E `cb371ab4` 1.1 卷消费；旧 scope 合作锁仍只用于合成测试。本文保留为 DEP-C 历史基线与产品批准接口缺口，不能将下方“尚缺”理解为 DEP-F 未实现，也不能将本地进程证据理解为 Linux/真实产品已验收。

本号不改产品、Compose、根任务板或合同。以下是供协调者下一轮冻结的提案，不是新发布的契约，也不是已实现的产品能力。

## 已核固定基线和备份矩阵

| 产品/输入 | 固定提交 | 必须一起保留的内容 |
| --- | --- | --- |
| Platform | `745ee9dd0b97ff1822a8fd4362e0626b7e3bb316` | 权威 SQLite（来源映射/授权、模型配置与撤销、审计）；网页 inputs/replies、启用时 models/home-controls 等 sidecar；每库 WAL；配置引用；日志 |
| Companion | `ab7c58250961807a2016c5f9128ce34b0065e182` | SQLite user_version=9；source_head/source_quarantined、来源与 owner 水位、outbox/发送带宽、unknown/幂等回执、人格批准；WAL；运行 `.owner` 单独处理，不从旧备份恢复锁状态；日志 |
| Memory | `9df2e9e2eb6c2c36778f215306e6b20c377e18c4` | schema=3 SQLite；来源/撤销/suppression/已消费批准/候选/账本/outbox/知识表；WAL；独立 `source-guard.json` 的 instance/revision/recovery；配置引用；日志 |
| Gateway | `51121e6c02ed60605be14f31b19b484bc117a746` | Chat 与 native 的持久 ledger、unknown 回执、usage/诊断私表及已存在迁移备份的明确库存；WAL；secret-ref 的配置引用；日志 |
| 发布组合 | DEP-A `e86d622a813f116b059b62b820cbd1342722042d` | 发布清单 Git blob、源码 repo/commit、镜像 ref/digest 或明确 unverified、功能状态、卷/服务角色、六套原字节合同 |

以上只读代码/文档核对，没有构建或导入这些产品。TS104–106 活跃检出未读取、未构建、未写。真正发布需重新绑定修复验收后的四提交，并从最终配置重新导出资源清单，不能沿用本表推定实际 DB/sidecar 名称。当前不初始化或迁移现有独立 AssetLibrary/Chat Audit/control-hub。

## 1. 真实停写适配（尚缺）

优先放在部署生命周期层；不要求所有产品为了本工具新增合作锁。建议适配层只接受协调者已核的项目身份、明确四服务 ID/镜像 digest、主机目录和 backup_group，关闭网页新准入，停止后台/外发与定时写者，按 platform→companion→memory→gateway 提交停止，等待**全部相关进程真实退出**及产品 owner 释放，核实无第二 writer/迁移任务/自动重启，再允许恢复工具取得整组 SQLite 写入预留。所有组都停后才快照，不能一边逐产品拷贝一边停服务。

停止失败、退出超时、owner 未释放、挂载或版本漂移必须阻断快照。证据应绑定 operation nonce、项目/容器不可变 ID、完整挂载集合、image digest、退出结果及有效期；静态 `offline=true` 文件或维护者口头确认不够。部署层必须在备份/恢复整个临界区禁止自动重启，不能只检查一次状态。日志采集器是额外持久写者：DEP-B buffers/checkpoints/Loki 数据另有一致性策略，当前工具只覆盖四产品日志，不冒充中央日志灾备。

当前测试 owner 锁仅证明 synthetic fixture 不在运行。没有 Docker 的本机无法验证上述 Linux 生命周期，故没有提供一个会被误用于 NAS 的执行后门。

## 2. 最新权威与灾难恢复（尚缺）

当前仅信任另行显式指定的原当前部署事实，并要求完整状态相等。若原状态或独立 guard 丢失，无可用灾难恢复路径。

真正灾难恢复需产品责任方定义公开、不可从旧备份自签的批准/对账端口：覆盖 deployment/source generation、来源与模型撤销、Memory 遗忘/suppression/已消费批准、Companion owner/source/send 水位、Gateway unknown/幂等 ledger；批准绑定备份 hash、候选代码版本、新目标身份及最新撤销基线。必须先重建/确认禁止集合并通过功能拒绝用例，才能解除恢复目标隔离。不能由运维脚本猜表语义合并或把备份内 guard 当“最新”。独立持久 guard 应有独立故障域和保管策略；本号 scope 内不同目录不等价于独立故障域。

## 3. 版本、功能验收与启用（尚缺）

DEP-A `verified` 必须核真实证据内容/文件 hash/版本绑定；DEP-D 的 `release_acceptance` 单份报告不等于 linux_images、four_service_tls、log_recovery、restore_drill 五类全部完成。当前 DEP-C 对 verified 给出明确未支持码；下一次应按固定 schema/样例实现适配，而非永久屏蔽合格包。DEP-C 的合成报告绝不能改名充当生产 restore_drill。

启用前还要重新注入配置/凭据/TLS，核内容 hash 与引用，隔离出口与真实渠道；由 DEP-D 对实际恢复目标运行真实四产品功能验证，含撤销后拒绝、遗忘不返回、unknown 不重发、回执幂等、就绪真实性、重启。报告须绑定恢复目标、snapshot hash、四版本、合同 hash、当前权威批准和探针时间；试运行的新安全事实也应成为后续激活的权威基线。验证失败保持隔离，不自动恢复原数据，不声称应用回滚。

`selected-code.json` 是禁用的**选择输出**，不是实际运行版本。后续生命周期只可采纳同时存在 COMMITTED、无 ABORTED、兼容性依据和升级前备份已核的事件，并自行核镜像 digest。数据迁移/跨 schema 回退另立精确卡；本包不会把 `rollback-code` 转换成数据覆盖。

## 4. Linux 验收待办

在获准的本地隔离 Linux 环境运行同一测试入口，再接真实四产品（仍用合成数据、替身模型）做停写、整组 WAL/guard、SIGTERM/强杀、满盘、权限、目录 fsync 和重启演练。当前 Windows 的 junction/hardlink/文件 fsync/互斥锁与合成 HTTP 通过，不能推成 Linux 镜像或断电通过。原始秘密/聊天/健康/资产数据和 NAS 不在本批操作范围。
