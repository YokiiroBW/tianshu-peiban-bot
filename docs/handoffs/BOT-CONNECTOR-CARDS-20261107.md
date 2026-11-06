# BOT-CONNECTOR-CARDS-20261107

目标：恢复卡片式添加机器人连接器，并修复全局设置页误报无连接。

变更：QQ/OneBot、Telegram、NoneBot、AstrBot 四类入口采用既有 Drawer 卡片；复用 Identity 登记与 Channels 配置表单，登记后按服务端目录读回原对象再继续配置，失败可续接已登记账号，不重建现有连接。新连接 ID 使用既有 requestId 自动生成。原编辑表单按职责提取为 ChannelConnectionEditor，命令 payload 静态对照保持。

根因：channels/view 原来将 actor_id=None 的全局管理上下文交给要求角色匹配的 Identity.connection，所有实际连接被过滤。现在按每个角色重新取得 Identity 的现有 external.manage 上下文，再沿原连接校验；角色页仍仅该角色，未授权角色不返回，没有放宽业务连接身份校验。

静态检查：完整相关 diff、Python AST、提取前后传输保存 payload、git diff --check 已检查。补有四项公开管理读取回归用例代码；按本轮用户要求未运行测试，未创建备份，不声称用例通过。发布构建与实际生产结果待记录。

保护：不改现有连接 ID、修订、凭据、配置、人物绑定、会话策略、投递/恢复调用；不迁移数据库，不主动发 QQ、不调用真实模型、不重放外部 unknown。静态检查不能替代真实网页与收发验收。
