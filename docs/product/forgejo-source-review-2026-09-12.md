# Forgejo 现有代码补充核对

核对日期：2026-09-12。正确地址为 [Forgejo](https://forgejo.yokiirobw.top)。沿用本机 Windows 凭据管理器已有 HTTPS 凭据，以只读 API 查询 18 个仓库清单和以下固定提交的相关文件；本记录不包含凭据。SSH 并非本轮实际使用的通道。

本轮是源码与架构核对，没有启动这些项目、运行其测试、下载媒体或操作设备。以下“已有”指相关实现可见，不表示新天枢已经完成集成。历史本地 astrbot_plugin_jmdowloads 目录中的 feed_monitor 不能替代远端 Download Hub 证据。

| 仓库及快照 | 已核实能力 | 对新架构的意义 |
| --- | --- | --- |
| [astrbot-plugins / c5483960](https://forgejo.yokiirobw.top/YokiiroBW/astrbot-plugins/src/commit/c5483960d8706e8c717d1e15083f885eee063b2f/astrbot_plugin_download_hub) | Download Hub 跨 Suwayomi 源名称搜索、章节选择、系列归并、分类归档、ComicInfo、PDF/CBZ、SQLite 归档索引、Komga 查重与扫描、JM 后端 | 优先提取客户端与归档模块；补持久任务、取消/恢复、并发互斥、文件原子发布和扫描后确认 |
| [astrbot_plugin_jmdowloads / bae13814](https://forgejo.yokiirobw.top/YokiiroBW/astrbot_plugin_jmdowloads/src/commit/bae1381480f55e0cf07f363e24b6ab455612259a) | 数字 ID 下载、重试、CBZ/PDF、ComicInfo、临时文件替换发布和 Komga 扫描 | 独立仓库是单来源后端；按名称跨源搜索在上面的 Download Hub，不能混为一套能力 |
| [astrbot_plugin_moviepilot / 890e0dbc](https://forgejo.yokiirobw.top/YokiiroBW/astrbot_plugin_moviepilot/src/commit/890e0dbc45cfdbab8d273f8d4087546a4ffb62b1) | README 说明影视搜索、订阅和通知；源码可见下载任务列表及启停/删除客户端、独立服务模块 | 作为影视连接器复用；不是覆盖所有小说、音乐和视频来源的统一后端 |
| [astrbot_plugin_nas_ops / 4bd39eea](https://forgejo.yokiirobw.top/YokiiroBW/astrbot_plugin_nas_ops/src/commit/4bd39eea3ca616a959726edd67f024d1198761f0) | 服务模板、空闲端口选择、卷与目录规划、Compose/容器检查、日志及部署后状态验证 | 执行能力已有积累；约一万行主文件仍与 AstrBot 绑定，应按实际功能提取，重新简化交互与权限 |
| [astrbot_plugin_dev_handoff / 7c4910fb](https://forgejo.yokiirobw.top/YokiiroBW/astrbot_plugin_dev_handoff/src/commit/7c4910fb71a6cbb210e1229b807c645b57b5ac93) | 项目、机器、任务、历史与交接命令，经 UDS 调用独立 controller | 是薄桥接器，不能视作完整项目工作台；controller 源码仍需另定位 |
| [ai-routing-gateway / f73569ed](https://forgejo.yokiirobw.top/YokiiroBW/ai-routing-gateway/src/commit/f73569edd14f7a92e7b8417344004b499f52554f) | 线路与客户端绑定、应用/检查/回滚、原生请求规划、控制接口；文档记录离线测试 | HTTPS/SSE 传输和数据入口尚未实现；模型与思考字段目前被线路值覆盖，要按新需求重定优先级 |

## 下载器的直接代码证据

在 astrbot_plugin_download_hub 中，main.py 的 search 调用 suwayomi.search_all，选择结果保存在实例内存；_download_and_archive 先查询本地 catalog，再查询 Komga，随后下载页面、打包、记录归档并请求扫描。archive/catalog.py 的主键为 source_id、manga_id、chapter_id，保存成功归档的位置，不记录完整任务生命周期。

archive/pack.py 的 pack_cbz 直接打开最终输出路径写 ZIP；这与独立 JM 仓库 archive.py 中使用临时文件和 os.replace 的实现不同。统一迁移时应复用更可靠的发布方式。同来源章节的并发请求也需要任务级互斥，单纯的全局并发上限不保证查重与写入不可交错。以上为代码路径推断，没有注入并发或中断实测。

代码中 SQLite 记录和 Komga 扫描是两个步骤。若写入记录后扫描失败，再次执行可能直接命中本地存在分支；因此新任务模型应分别保存“文件已发布”和“阅读库已确认”，允许从失败步骤继续。不能因为扫描请求已受理就向用户报告已经能在库中阅读。

## 网关的直接代码证据

src/aigateway/dataplane.py 在 prepare 阶段将 payload 的 model 设置为 route.model；_apply_reasoning 分别覆盖 Responses 的 reasoning.effort、Chat 的 reasoning_effort 与 Anthropic 的 output_config.effort。当前行为是线路绑定驱动的参数策略，不满足默认保留客户端指定值的目标。

建议让模型映射和思考参数各自明确选择“保留客户端值”“仅补默认值”“强制指定”，在界面可见。原生协议保真与配置策略分开验证；不因现有控制层已实现，就默认保留它所有的数据表、证书管理和审批机制。

docs/status/development-bootstrap.md 明确说明：原生请求规划已实现，pinned-IP HTTPS/SSE 传输与数据入口尚未实现或启用。文档中的历史测试通过记录只支持其当时的离线范围，不证明真实上游转发、流式取消、模型和思考参数已接通。

## 取舍更新

独立陪伴核心、天枢平台、天枢记忆与 NoneBot 接入的职责建议保持不变。此次证据主要降低了资料归档和 NAS 执行能力的重写范围；开发工作台及模型网关仍有明确缺口。更新后的蓝图以实际能力为复用单位，不要求继承整个旧插件或旧架构。
