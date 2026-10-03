# 地点显示与和风地址修复：已部署 NAS

Platform `edab33cbbc395fd2bed25b0424ca07d62051fb52` 已推送 GitHub main 并部署 NAS；包含 `b10d1f8` 的地点显示修复。中间 life-location 候选只构建，未部署。

生活页顶部及此刻卡片统一将 opaque 世界/房间引用显示为“角色生活空间”，无房间引用时显示“暂无位置记录”；不猜具体位置、不改持久化 ID。和风 host_url 支持官方多层专属域名（例如 abcxyz.re.qweatherapi.com），保留 HTTPS、官方域名、有效 DNS 标签及无路径/参数限制。页面更新示例与中文错误提示。

- 本地 TypeScript、构建、ruff 与 diff 检查通过。地点桌面/手机 2 项、天气设置桌面/手机 2 项、天气后端 11 项、外部连接 HTTP 5 项通过。HTTP 测试首次缺测试环境变量，按已有配置补齐后通过。无全量回归。
- NAS 冷备和隔离副本验证后切换；10 服务、5 核心及 guard 健康。安装源码检查及实际容器 host_url 多层地址校验通过，49 静态资源指纹匹配。匿名真实浏览器入口 200 无脚本错误；Dockge 同步。
- 未使用真实用户 API Key 调用和风。用户原先保存被校验拒绝，需重新保存连接再搜索/选择城市。没有修改现有配置或数据，没有迁移。
- 镜像：`127.0.0.1:19559/tianshu/weather-host-platform@sha256:08dc1e48916690396936fd5f36be4f02b6632d53d035c18a49ab8220a63260dc`。
- 备份：`/volume2/tianshu-v2-resident-updates/weather-host-20261003-core/production-generation-79dd2478817e40b08036dbcea5de607c/cold-snapshot`。

官方格式依据：https://dev.qweather.com/docs/configuration/api-host/
入口：http://192.168.31.210:18446/#/companion/1
