# TS-324：Chat 签名密钥轮换与历史备份兼容

用户明确授权处理剩余复用凭据，优先 Chat 管理/入站 Token、MySQL，签名密钥先完成历史备份兼容；保留数据和现有功能，集中审查验证后部署。本卡仅 Chat 产品代码，生产切换、旧备份信任清单、共享记录和发布由父协调者单写。

基线 `6aa6e7f2765ff2702b443b1fe318b38ca91a9057`；独立任务检出。只允许 `app/config.py`、`app/main.py`、`app/api.py`、`app/backup/`、`app/services/backup_service.py`、相关现有 `tests/`、`.env.example`、`docker-compose.yml`、备份配置文档与 `docs/handoffs/TS-324.md`。不改 Memory/Companion、用户数据库 schema、原部署凭据或根任务板。

当前 key 独占新导出、worker、新转换结果和 cursor；历史 key 只验证外置可信清单明确登记的历史内容，不按不可信 kid/time/path 或全局旧 key fallback 放行。沿三个既有备份验证核心装配一个领域内显式策略，保持默认行为、旧 unsigned admin-import 语义及 HTTP/CLI 错误边界。转换的输入历史验证与输出当前签名分离。外置清单缺失/错误不能放宽历史信任；不将旧 key 引入 cursor。

旧网页导入会解析并重序列化 JSON，历史内容 pin 必须兼容该路径及流式转换。可使用“实际重算 payload checksum + 完整 manifest”的明确格式域摘要，复用现有 canonical/replay/framing，覆盖完整解析语义并保留 v4 的 payload 与 manifest 双 HMAC；不要仅相信包内自报 checksum。避免对巨大备份额外复制或先 hash 路径后重开可变文件的验证/使用差异。根协调者根据已知受信历史收据登记清单，产品不自动信任任意目录。

实现必要的清单生成/登记能力时只从已验证、管理员选择的材料产生；候选输入不能给自己增加信任。保持内存有界、现有 restore 空目标约束、媒体校验和临时文件清理。设置不包含真实 key，测试用合成夹具。

验证覆盖新 key 正常签发；登记旧 JSON/gzip/v4 验证、导入/转换/隔离恢复；未登记旧 key 重签、改 payload/manifest、伪造 kid/time 被拒；别名/HTTP/CLI 调用一致；转换只签新 key；unsigned 原语义与 cursor 不变。稳定后一次集中 diff 审查与相关测试，不循环扩大。交付 clean commit、实际命令结果、未覆盖及 handoff；不推送/部署/读取真实聊天或备份正文。


生产启动实际发现既有重复迁移 DDL 阻塞，必要恢复范围补充 `app/database.py` 和对应启动迁移测试；只跳过原 journal 已完成版本，首次升级及 rollback 重放保留。最终源码 eb81e936b2675022ee135f101477b008ab26db34 已正常推送且 app/worker 固定镜像实机验证；历史 key 仅限明确内容 pin。最终部署、凭据、备份保留和外部 QQ 登录限制以[执行记录](remaining-credential-rotation-2026-10-27.md)为准。
