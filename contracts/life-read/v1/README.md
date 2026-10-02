# 角色生活计划与经历只读协议

本包补充 Companion 既有 life-read 的 actors、snapshot、diaries、revision 读取链，新增 `POST /internal/v1/life-read/today` 与 `/timeline`。不改变旧端点返回结构，不新增写操作。生命周期与时间节点驱动生活，任何读请求都不能触发 tick、模型、初始化或事实修改。

## 来源与授权

沿用 bearer 确认调用服务，life_readers 绑定固定 reader_id 与 actor_ids，再实时核对 life_access。部署映射可声明 runtime_roles=true，让该独立读服务覆盖 RoleRuntime 管理的角色，actor_ids 可为空；它不是客户端传入的通配权限。角色生命周期为尚无生活读者记录的新角色登记该部署读者，已有显式读者记录和撤回保持不变。普通静态读者的行为不变，所有读取仍实时检查 life_access，读取请求不补建授权。reader_id 不来自请求正文。未部署或无权查看的 actor 按原语义返回 404，不泄露其是否存在。请求 JSON 上限 16384 字节，响应 262144 字节，超限返回 429，不截断后假装完整。会话、主体、凭据和原始私聊内容不出现在投影中。

`fictional` 恒为 true，`state_basis` 恒为 last_persisted。时间戳使用 UTC epoch 秒，可带小数；日期、minute 则按对应世界时区解释。计划是意图，时间线是持久经历，未来活动不能作为已经发生的事实输出。读端只读短事务，不执行迁移或 DDL。

## 今日计划

today 请求只包含 schema_version=1 和 actor_id。响应 day 来自已持久计划日期，不在查询时补建新日程。plan.version 是持久整数版本，plan_id 按 actor_id、当地日期、schedule_version 和 world_version 稳定生成；phase_id 由 plan_id 和 minute 稳定生成。entries 按 minute 升序且 minute 唯一，每项为一个连续活动阶段。

plan.state 为 active、paused、completed 或 superseded。阶段 state 为 planned、current、elapsed 或 skipped。generation_state 为 queued、generating、completed、unavailable、failed、interrupted、superseded 或 skipped；completed 表示已拿到有效生成结果，不能把基础作息标为模型生成成功。generated_by 区分 baseline 和 gateway。模型不可用时仍保留基础作息及明确 unavailable 状态，不阻断时间推进。

## 历史经历

timeline 按 actor_id 和当地 day 查询，limit 默认 20、范围 1 到 50。after 使用上一页返回的 position 和 known_id；按 (position DESC, known_id DESC) 查找后续项，禁止跨 actor 或 day 拼接缓存。position 是持久 learned 序；客户端不自行编造或增加游标。

记录包含事件、获知身份、kind、summary、发生与获知时间、via，以及可为空的 phase_id、plan_id。generated_by 区分 baseline、gateway 和 simulation。只返回该 actor 已获知且读者有权查看的生活事件。日常模型只使用角色自己的生活素材和允许使用的内容影响，不能把用户真实聊天原文或他人身份混入公开生活经历。

## 消费和兼容

Platform 沿现有 WebReader 与 LifePage 消费本投影。切换角色或日期时清空旧游标，忽略迟到的旧请求；前端不创建第二份权威日程。既有错误语义与已发布日记的访问范围保留。

schema 与实例的本地验证、Companion 生产者和 Platform 消费者的实际回环验收分别记录。未进行实机无人对话时间跨度验收之前，不把本地时钟/模型替身验证称为线上完成。
