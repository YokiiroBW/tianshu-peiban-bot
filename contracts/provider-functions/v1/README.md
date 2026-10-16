# Provider functions v1

2026-10-11 发布的 provider-self-service/v1 增量。原发布包字节保持；本增量随 Platform 和 Companion 配套更新，平台先于陪伴启动。网关继续 companion.text 固定版本协议，不新增权限。

内部 select 请求允许可选 function_id，缺省 chat。六项为 chat/tools/code/search/writing/memory；chat 优先角色模型再系统默认，其他项优先功能绑定、角色模型、系统默认。绑定固定供应商修订，不可用时报错而非默默回退。每个生成有独立 turn_id，同一 grant 不能用于不同功能，已有任务不重新选模型。

POST /api/web/providers/function 使用原管理会话、CSRF、租约、CAS与幂等回执。null provider/revision 表示继承；chat 必须选择模型并复用原 default。提供 provider 时必须给出可用且已测试的修订。view 在原响应增加 functions，字段及消费者语义见配套 docs/platform/model-functions.md（本发布附于 delivery.md）。

聊天、生活、日记已有消费者；工具/代码/搜索/记忆整理仅配置，不表示自动委派、执行或两阶段回复已实现。长篇作品继续静态配置。生产配置不自动选择新模型。

schema.json 提供请求/响应形状；互相关联约束由原业务服务验证。examples.json 同时包含旧聊天请求与 writing 请求。验收包括平台实际 HTTP 与固定模型 grant 读取、陪伴生成参数，以及浏览器持久配置；无真实付费模型调用。
