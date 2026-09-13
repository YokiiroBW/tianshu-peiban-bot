# TS-061 只读证据候选接口范围

2026-09-14。已核对现有 `apply_robot_message_scope` 同时覆盖实时 RobotMessage 与 QQNT 导入来源分支，join/where 必须配对。批准在隔离任务内实现 Audit 产品候选 `POST /internal/evidence/read`，尚非跨产品正式发布，不修改文字合同1.0.0。

请求按已登记 channel、原始 message_id、可选 locator 与有界 before/after 读取；不使用任意 person_id/actor_id/Core receipt或revision作授权。服务器固定 namespace、归档robot_id、群私、room及ID种类；服务独立凭据只允许精确channel子集，禁止wildcard、歧义映射、重复凭据和管理员回退，不追随adapter换号。

中心和邻居都执行同样范围，稳定排序并限制条数与字节。locator只能在既定channel/message候选中匹配/消歧，不能绕过授权。无权、未知映射、不存在或歧义保持统一404。过大文本明确拒绝完整返回，不静默截断语义；不展开媒体/回复/转发，不返回整包raw JSON或NAS路径，也不触发头像/媒体补写。

输出为已存文本、最小元数据、稳定Audit locator及真实行/内容摘要形成的归档观察回执，当前源状态明确not_checked。内容摘要不包含每次读取时间。它不证明Core当前修订/撤回或Memory遗忘/投影有效；相关权威仍分别核验。

候选交付须带请求/响应定义、实际隔离HTTP/数据范围回归和消费样例。协调审查后再决定跨产品发布与适配；不操作生产数据，不改变既有采集、管理员API或备份行为。
