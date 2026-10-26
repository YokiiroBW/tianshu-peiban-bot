# 群整理话题只读投影候选

POST /internal/v1/memory/browser/topics。沿现 browser 的注册 reader、当前 group scope 与来源闭包；subject 群必须等于当前 scope 群。Platform 从服务器登记群 entry 派生 origin，不从浏览器自报权限。schema 引用既有 text-dialogue/v1 common 资源，不改已发布包。

- Current browser caller/origin/registered actor+group permission and fresh source closure; no raw ledger/event/manifest exposure.
- Key topic by (segment_id,topic_id), no cross-segment semantic merging or fabricated social edge. person_ids means model topic attribution, not agreement.
- Resolve receipt -> actual batch/manifest author and current records/source closure. A negative dependency suppresses the complete affected topic, never silently removes a cited person and returns old topic prose.
- Only actual group sources for this actor/conversation. Time range and stable cursor bound to request/scope/source revision. Private associations do not broaden this read.
- Counts describe returned/committed consolidation coverage, not all archive history/activity/heat. Chat owns raw interaction/heat statistics.
- No new table, queue, model call or backfill. Existing write ledger is internal immutable receipt, not read authorization.

真实 member role 只有 human/bot；person/actor 身份互斥，reality 保原值，模型的同话题归属不表示共同同意。图布局和点云坐标只是前端显示，不写回为事实。无新模型、队列、图数据库或回填。

运行负例：不同 subject room、当前 origin/角色授权失效均拒绝；任一来源遗忘后完整受影响 topic 省略并标 omitted_invalidated；外 actor/cursor 拒绝不重绑。
