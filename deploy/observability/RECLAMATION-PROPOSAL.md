# 应用日志安全回收最小提案（未冻结、未实现、不可据此删段）

现diagnostics/v1只有12字段事件，没有封段清单、产生水位、确认世代、中央持久证明或应用CAS回收入口。Vector位点、sink HTTP204、Loki查询命中及本包hash账本都不能单独授权删应用段。当前产品目录默认1GiB，保留未确认文件并在耗尽时停止新业务；这是持续日常使用的明确阻断。

## 唯一所有者

- 跨产品合同负责人：根协调者，只有他能在根contracts发布新版本，并安排生产者/消费者样例联合验收。本提案只在DEP-B包内，不增改diagnostics/v1字段。
- 段生产/目录/业务准入负责人：四产品日志适配器各自负责人，Memory同时负责memory-knowledge的实例归属。只有应用持有自己的日志目录写/删权限。
- 中央接收证明负责人：后续DEP-B接收账本服务负责人，单写独立持久确认账本；不能根据扫描EOF自行虚构封段事实。
- 最终接线/容量/保留/恢复世代负责人：部署协调者；DEP-A记录卷/身份/版本，DEP-C核中央与源恢复世代，DEP-D消费反例和真实证据。回收接口需要独立产品卡，本号不越界实现。

## 生产者封段描述（建议字段，尚无schema）

`protocol_version, deployment_id, source_service, instance_id, producer_epoch, segment_id, seal_revision, contract_manifest_sha256, byte_length, content_sha256, event_count, first_sequence, last_sequence, event_set_sha256, sealed_at, previous_segment_id, previous_segment_sha256`。

segment_id是随机不可复用ID，不是可被重命名/路径复用混淆的文件名。content_sha256覆盖实际UTF8+LF原字节，不重序列化JSON、不规范化换行。event_set摘要覆盖按sequence排序的(event_id,sequence,canonical_event_hash)，明确与文件hash不同用途。first/last不代替逐条连续性证明，count也不代替集合一致。封段流程必须先flush+fsync文件、写不可变描述并fsync目录，再将后续事件写到新段；活跃段永不可回收。描述不携路径、账号、正文或secret。

应用另发布生产水位：最后分配/已落地序号、已受理未落地集合或范围、当前活跃段、封段世代。未确认受理/终态不能由中央凭已有最大序号推定不存在，进程启动新instance_id不掩盖旧实例尾部缺口。

## 中央持久确认

建议收据：`receipt_id, deployment_id, collector_epoch, storage_generation, source_service, instance_id, producer_epoch, segment_id, seal_revision, byte_length, content_sha256, event_set_sha256, event_count, verified_sequence_ranges, commit_id, committed_at, query_verified_at, retention_policy_id, retain_until, key_id, signature`。

确认者须读取生产者已签封段描述，校验完整字节与逐条身份，得到实际检索集合一致，然后把确认账本及被承诺保留的**可恢复副本**持久化。Loki HTTP204可能只表示单机WAL/缓存接受；查询成功仍可能在随后失盘/旧备份恢复时消失，不能等同可恢复副本提交。若选择仅单机WAL作为承诺，必须明确该故障域和不覆盖的磁盘损坏/回退；不能暗称冗余归档。

最低建议是独立不可变段归档+fsync/校验成功，以及Loki可检索证明，两者都完成才提交收据。归档仅已去敏事件，不接收不合约原始行。明确副本保留期限至少覆盖源回收后声明的恢复窗口；TTL过期是已授权保留策略，不冒充意外缺失。收据的签名只证明来源与完整性，不替代持久事实。

## 重复、乱序、重启和恢复

幂等键为(deployment_id, producer_epoch, instance_id, segment_id, seal_revision, content_sha256)。同键同内容返回原收据，重试不重建归档或改变保留起点；同段不同hash/revision冲突拒绝并告警。事件重放保持event_id/sequence，原始重复计数不抹除；跨段序号冲突阻断。后段先到可保存未确认事实，不能跨未完整前段推进连续回收水位。

收集器重启从持久账本恢复；收到请求但未提交账本崩溃，只能回答unknown或再次核验，不能凭内存成功回复。应用收到收据前后崩溃都可按幂等键查询。中央回退旧备份必须更换storage_generation并撤销/隔离旧世代的未消费收据；不允许恢复后的旧账本继续签更高水位。部署ID、世代及撤销水位必须作为恢复前置条件，时间戳或UUID大小不决定新旧。

## 应用自身CAS回收前置条件

应用在单写所有者锁内，用预期(producer_epoch, segment_id, seal_revision, file_identity, byte_length, content_sha256)比较当前封段事实；必须全部成立：

1. 真正封段、没有写句柄/未完成fsync/尾部半行；段仍是该实例所有，路径限定在自己的目录，非symlink/junction/hardlink逃逸目标。
2. 收据签名/角色/deployment_id/合同hash/源身份/两方世代和撤销水位均有效；内容hash、集合hash、字节数和事件数完全一致。
3. 中央恢复世代仍有效，保留期限覆盖约定窗口；没有缺失/冲突/unknown/未确认尾事件、还未满足的前序依赖或保留pin。
4. 应用先在自身回收台账fsync `reclaim_prepared`，再在目录内原子移入仅应用可写的待删区并fsync目录，再删除、fsync目录并记录`reclaimed`。重启按事实恢复，不重复扣容量、不删除同名新文件；任何CAS失败保留原段。

不能让Vector挂载rw后直接unlink，不能把日志TTL/offset==size/最近查询结果当删除信号。批量回收限制单次数量/字节，执行后重新计算可用额度并通过真实持久写探测恢复就绪，不能仅删除计数后报恢复。

## 独立反例（后续生产者/消费者双边强制验收）

| 反例 | 必须结果 |
|---|---|
| EOF后继续append，旧hash收据迟到 | 活跃/变长CAS失败，文件保留 |
| 同路径换inode或重建同名段，符号/硬链接逃逸 | 身份不匹配/逃逸拒绝，不触及目标 |
| 只上传前N行、缺最后事件、缺中间序号 | 数量/范围/集合或源产生水位不符，零回收 |
| 同event_id不同内容，同sequence不同event_id | 冲突告警，零确认/回收 |
| 第二段先到，第一段未完整 | 不越过前序缺口推进连续水位 |
| 重复上传/重复确认/重复删除请求 | 同收据同结果，容量只扣一次 |
| Loki204后杀进程/损WAL/丢盘，查询曾成功 | 无可恢复持久副本证明就无回收许可 |
| 写收据前崩溃；回收prepare后/rename后/删除后崩溃 | unknown或幂等恢复，不假确认、不误删新文件 |
| 中央恢复旧快照；应用旧收据回放 | storage_generation/撤销水位失败，拒绝 |
| 跨部署、错service、错instance、错合同或过期收据 | 身份/期限失败，不泄漏其他产品存在性 |
| 收据未消费就被撤销/保留pin变化 | CAS重核失效，零回收 |
| 满盘时写回收台账/目录fsync失败 | 保留或进入显式不确定恢复态，不能洗绿 |

验收必须含真实Linux文件身份、fsync/rename、关机中断和隔离恢复，不以本提案或本包query对账测试替代。冻结前现有保留策略继续有效：采集器只读、应用不删未确认段，容量保护及阻断保持。
