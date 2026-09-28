# 机器人网页适配器：联合验收状态

当前未合入产品 main、未部署 NAS、未启用真实机器人。线上仍为设置分区版 Platform 88fb778。用户尚未指定真实 QQ 测试对象。

## 固定候选

- Platform 后台 04f9c874c701cb8fa20fe31e18646013aee99f81，含 f7e8ae3 的持久化意图、幂等事务和并发停用修复。
- Platform 网页 a9662fc15820a3a817cffe28dcc5e844e46551cb。
- Companion Core df2b7a2f7ec0478943dc1fe59a949031f6e1210e。
- Companion 插件候选 27ce2f8，含 0565185 的 AstrBot Dashboard 密钥页/显式私网监听，以及 6e2c475 的消息版本与轮询大小修复。
- 集成工作树位于 `worktrees/ADAPTER-INTEGRATION/tianshu-platform` 与同级 `tianshu-companion`；分支均为 `codex/bot-adapter-integration-20260928`。
- 联合验收后的 Platform HEAD ebac8e5ce586b3eb05a2142836f5aac65a690e53；Companion HEAD e85987147b1109c8a41b3782e6fc3ccc33e0e249。

## 已有证据与边界

后台定向 65 tests OK / 5 skip；Core 定向 73 passed / 4 subtests。后台全量曾有 14 failure / 79 error，仅两类代表性失败在原始基线同环境复现，不能声称全量通过；详情见产品 ADAPTER-P 交接。

网页向导完成合成 API 桌面/手机流程与错误、恢复状态测试；原旧连接真实本地 Platform 夹具通过。网页接入真实新后台的浏览器验收尚在执行。

插件真实 AstrBot 4.27.3、NoneBot 2.5.0/OneBot 2.4 框架加载、HTTP、权限、重启、消息边界定向 7 项通过；旧回归 16 passed / 4 skip。AstrBot 已有 Dashboard 管理员读取密钥和显式 LAN/container 监听；NoneBot 没有统一宿主管理页面，仍需宿主安装/加载插件及本机取密钥，不需要逐连接编辑文件。

双宿主联合测试 2 项通过（31.907 秒），脚本为集成 Platform `tests/backend/test_bot_adapters_host_joint.py`，报告 `docs/handoffs/ADAPTER-JOINT.md`。实际 Platform/Core TLS、实际宿主框架与插件 HTTP，QQ SDK/Memory/模型为合成夹具。覆盖入站到生成到原回复台账到 SDK 回执、重复事件不多生成/发送、SDK 超时 unknown 不重发、Platform 服务重启封闭及状态恢复、停用拒收和启用回执丢失后的显式恢复。不覆盖真实 QQ/真实模型、NAS、宿主/Core 进程重启。

联测曾真实发现插件把 binding revision 当成消息 revision、Platform 会话标识缺 kind 前缀；已分别修复并重测，不能用修改测试输入掩盖差异。规则见 bot-adapter-self-service-2026-09-28.md。

## 当前派发

ADAPTER-U 原可见任务 `01a0e701-adbe-7683-9e47-847ccbcd2338`（Sol/xhigh）负责真实网页联合验收。独占新增：`apps/web/tests/bot-adapters-live.spec.ts`、`apps/web/playwright.bot-adapters-live.config.ts`、`scripts/dev/bot_adapters_live_fixture.py`、`docs/handoffs/ADAPTER-WEB-JOINT.md`。不改产品或锁，不操作 NAS。

总控继续审查与打包准备；网页联合通过后才决定正式串行合并、部署配置装配及镜像验证。原管理员、默认模型和其它服务保持现状；不得重复首装、恢复旧容量 state 或把当前隔离测试称生产验收。
