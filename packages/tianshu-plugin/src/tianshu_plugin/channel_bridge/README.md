# 天枢原生渠道桥 v2

本包只连接已安装的外部宿主 SDK，不包含旧 Core/SQLite 产品后端或 pull 兼容接口。宿主自己的 SQLite journal 仅保存提交身份/待 ACK 事件/真实 SDK 回执；重启 submitted→unknown，原 call_id 不重发。核心仍使用唯一 PG Action/Attempt/Receipt。

NoneBot：安装 `tianshu-plugin[nonebot]`，固定 nonebot2==2.5.0、nonebot-adapter-onebot==2.4.0 和 FastAPI driver。加载 `nonebot.load_plugin("tianshu_plugin.channel_bridge.nonebot")`。在宿主配置写入 `tianshu_bridge_instance_id/connection_id/self_id/token/core_url/data_dir`（均带 `tianshu_bridge_` 前缀）。RPC 在宿主 FastAPI `/tianshu/channel-bridge/v2/`，原 matcher 完成后才转交；任何外部 matcher 或未计入的 event postprocessor 保留外部回复归属。按实际固定账号调用原 bot.call_api，不创建另一 QQ 连接。

AstrBot：固定 4.27.3/aiocqhttp。安装 SDK 包，将本目录 `astrbot.py` 放到插件目录为 `main.py`，`astrbot_metadata.yaml` 为 `metadata.yaml`，`astrbot_conf_schema.json` 为 `_conf_schema.json`。填写固定 instance/connection/self/token/core_url 与 RPC 监听。一般消息是只观察；显式 `/tianshu` 高优先级 handler stop_event 后只转交该消息。原宿主其他命令由宿主回答，不猜测其不可见规则链已完成。

核心 channels 连接 adapter=nonebot/astrbot、transport=bridge，endpoint 指向宿主 HTTP，bridge_instance_id/self_external_id 精确匹配身份登记。宿主 token 与核心连接 token 同值，通过各自正式配置写入，任何主密钥或数据库不跨宿主。

核心固定 call_id 调 `POST calls`；GET calls/{id} 和 POST calls/{id}/cancel 只控制原提交。核心持久化终态后 ACK，宿主擦除私人响应正文，保留真实 message_id/retcode。宿主另以同 token 将晚到回执 POST 到核心 `/channels/bridge/{connection_id}/receipts`，核心确认落账才清 outbox。HTTP 超时、SDK 取消与宿主重启均不会自动重发。

状态为 implemented_not_tested：没有启动宿主、连接真实 QQ、发送消息或执行本目录用例。TLS 信任遵循平台默认，未关闭证书校验；需要本地 CA 时应由宿主部署环境安装。
