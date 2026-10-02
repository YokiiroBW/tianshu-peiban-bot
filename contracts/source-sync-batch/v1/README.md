# 来源同步完整范围分批协议

本包是 source-sync/v1 1.0.0 的兼容消费者扩展，不修改该已发布包、生产者路由或单次消息 schema。绑定本包的 Memory 可以核对超过 256 个历史 selector 的完整 scope；旧消费者仍遵循旧包的单轮上限。每次生产者请求最多 256 项，不得截断 scope 或漏掉历史撤回、修订和墓碑。

## 完整轮次

Memory 在本地 m0 版本下收集完整 coverage。把当前显式请求的 selector 排在前面，每批最多 256 项且不同批不能重叠；turn_ids 仅放第一批。各批依次请求 Core snapshot 和 Platform current access，沿用原包的 schema、source_snapshot 和 current_access 关系校验。单批保留旧同步行为。

多批收集完毕后，以相同 viewer、空 admissions 请求 Platform current，最后再次读取 Core head。全部批次的 Core generation 和 sequence 必须等于最终 Core head，全部 Platform generation 和 sequence 必须等于最终 Platform head；viewer_context 也必须完全一致并在应用时未过期。绝不拼接不同版本的快照。`rules.batch_barrier(observation, now)` 检查跨批关系；它不替代每批原合同检查、真实认证或 scope 授权。

任一 owner 移动、viewer 变化或请求失败时，整轮丢弃，按已有有界重试规则重新读取；不提交已完成的部分批次。只有全部成立后，Memory 在一个 Store 事务内复核 m0 和完整 coverage，按原失效语义应用所有批次并推进检查点。任一失败回滚整轮。事务内仍执行持久 receipt 唯一归属核验，不允许不同 selector 共用同一 receipt。

## 包结构和验证

`schemas/batch.json#/$defs/barrier` 定义 Memory 内部聚合证据，不新增网络入口。`rules.py` 提供跨批纯校验。依赖原包的完整 schema registry；不得从网络解析引用。manifest 采用 UTF-8 文本 CRLF 归一化为 LF 的哈希规则。生产者和消费者联合用例记录在本轮交付中，模拟来源不等同真实账号验证。

扩展部署时与原 source-sync/v1、text-dialogue/v1、profile-memory/v1 一起提供；不要覆盖原包文件或其固定指纹。
