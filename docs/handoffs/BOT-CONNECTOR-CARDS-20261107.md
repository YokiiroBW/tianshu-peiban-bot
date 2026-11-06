# BOT-CONNECTOR-CARDS-20261107

目标：恢复卡片式添加机器人连接器，并修复全局设置页误报无连接。

变更：QQ/OneBot、Telegram、NoneBot、AstrBot 四类入口采用既有 Drawer 卡片；复用 Identity 登记与 Channels 配置表单，登记后按服务端目录读回原对象再继续配置，失败可续接已登记账号，不重建现有连接。新连接 ID 使用既有 requestId 自动生成。原编辑表单按职责提取为 ChannelConnectionEditor，命令 payload 静态对照保持。

根因：channels/view 原来将 actor_id=None 的全局管理上下文交给要求角色匹配的 Identity.connection，所有实际连接被过滤。现在按每个角色重新取得 Identity 的现有 external.manage 上下文，再沿原连接校验；角色页仍仅该角色，未授权角色不返回，没有放宽业务连接身份校验。

静态检查：完整相关 diff、Python AST、提取前后传输保存 payload、git diff --check 已检查。补有四项公开管理读取回归用例代码；按本轮用户要求未运行测试，未创建备份，不声称用例通过。发布镜像已构建；实际生产结果如下。

保护：不改现有连接 ID、修订、凭据、配置、人物绑定、会话策略、投递/恢复调用；不迁移数据库，不主动发 QQ、不调用真实模型、不重放外部 unknown。静态检查不能替代真实网页与收发验收。

## 实际发布结果

- 实际部署源码：`c277d7ea929edccefa9fa006eebb54a59b39b57b`。
- 镜像：`sha256:ec15ff59214c7553ca3345e804727cfcfa7ddac5f39e52b9f136631a5af203bd`。
- 容器：`9a51a70906ba82b10c9625de0e4a3c39693ad9901f9d5e9336ada91037e6d489`。
- 北京时间2026-11-07 02:08:08六角色ready，HTTP200，0重启、无OOM；容量守护healthy，容量守护和巡检active。
- 当前用户真实Identity权限下，安装版本channels/view管理处理器只读返回从0条恢复为2条；配置与身份记录摘要完全一致。此项为正常只读入口核对，没有浏览器/消息投递测试。
- 配置/.env/seccomp SHA、11个挂载和其他两个容器保持；无数据库迁移、清库、配置/权限覆盖或消息/模型/未知效果重放。按此前要求未运行测试、未备份；四项回归仅有代码，不声明通过。
- 私有本地回执：`.runtime/bot-connector-release-20261107/final-receipt.json` 与 `connections-before.json`、`connections-after.json`。
