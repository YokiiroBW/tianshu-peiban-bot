# TS-317 后台写作真实模型路由接点

本卡是用户已授权五项扩展的必要前置：AL05真实受保护`companion_background`身份能被原调度器识别，但Gateway动态版本入口与Platform当前provider authority只接受`companion`，仅客户端替身通过不能说明已接通。保留调度、槽位、时限、原前台及旧版本行为，补齐同一现有调用链。

唯一Gateway写者qa_takeover，复用原TS261干净worktree，在现完整d658367本地候选起新`codex/memory-extensions-20261026`分支，原分支/ignored环境和未集成Q17改动保留；已核62084e1集成main是其祖先，不从main开分支丢掉这批原候选。范围仅Gateway `src/tianshu_gateway/server.py`的动态受保护service白名单及必要现有配置/协议 helper、相关测试、`docs/handoffs/TS-317.md`。不改调度器，不让header/body声明改变workload class，不把未认证身份升权。旧companion与native/非internal/未知service负例继续有效。

根单写Platform ProviderAuthority：选择请求必须由相同已认证service发出，版本grant记录实际caller，Gateway runtime按同caller/turn/version复核。Companion唯一写者接固定background selector与Gateway身份，不借前台lease伪装；缺少后台动态接线时，只能使用明确登记的独立已发布写作版本或如实兼容旧路径。

按完整块本地提交，集中相关测试与实际本地Platform→Gateway→固定上游联合；上游为本地合成服务，0真实模型、0QQ/外站/PG/生产，不新推送部署。没有改过的前台能力复用检查，新增能力与替身/真实三端验证分开记录。TS315归档查询内核已交付，可释放产品写者后执行本卡，不改其已完成源码。
