# 模型配置来源受限续期 v1（已冻结）

状态frozen。2026-09-22协调核验TS109/110双方实现、11个平台专项与26个网关/真实TLS联合测试通过后冻结；仅本地合同与合成链验收，不代表已部署。仅POST /internal/v1/model-config/origin/renew，HTTPS；开关平台model_origin_renewal_http、网关platform_origin_renewal均严格bool且默认false。

请求与成功响应分别服从request.schema.json和response.schema.json，必须严格类型、拒绝重复JSON键/非有限数/未知字段，并检查日期和UUID格式。失败沿用text-dialogue/v1 common#error及既有401/403/400/413/503，不重新定义错误封套。响应request_id和assertion_ref必须与请求相同，不接受空值、回显不同身份或延长scope。

服务器单事务认证kind=service/service=gateway/config.snapshot，并验证当前断言未撤销未到期、entry摘要/owner和caller未撤销、路由恰为gateway→platform/config.snapshot专用entry。不接收用户指定TTL/entry/owner。仅更新同ref至min(now+entry.ttl_seconds, entry.expires_at)，不生成子ref、不延長主体有效期，过期entry失败。始终受现有快照实时撤销/模型版本检查。存续身份与范围不变，审计对象使用去敏摘要。

首次引导仍由操作者已有公开流程执行，不给gateway管理员origin.issue权限。已过期/撤销/ref缺失不得自动bootstrap；401/403永久关闭本次进程续期能力，需明确重新引导或重启。当前ref撤销后不得靠续期新ref逃逸。

网关在有效期内提前单飞续期；单次总请求3秒、响应4096字节、每轮最多3次有界退避，整个循环不能越过当前凭据到期时刻。失联时只可在已证明有效期内消费仍有效且可撤销检查通过的配置；到期严格拒绝。初始过期ref准确失败。chat/native缓存均检查实际来源寿命和状态，不借缓存绕过撤销/续期失败，不重复模型副作用。关闭取消不遗留任务。

时间解析必须有时区，UTC响应使用Z，日历日期实际合法；时钟容差不可延長服务器授权。日志不记录凭据/断言ref/聊天。HTTP200只证明续期操作完成，不证明模型/对话/记忆已完成。

examples.json只含合成UUID/ref和日期示例，不是可用凭据。负例要求双方测试拒绝。冻结证据见docs/development/reviews/TS-109-110-contract-accepted-2026-09-22.md。schema和实例保持候选原字节，只有此说明与manifest状态/说明hash更新；产品测试须显式重绑新manifest。
