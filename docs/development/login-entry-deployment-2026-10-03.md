# 独立全屏登录：已部署 NAS

Platform `5cd034be690a96456a28a06aac4fb9d9c55898b7` 已推送 GitHub main 并部署 NAS。

未登录打开任意入口均显示独立全屏登录；登录后进入原目标页面，默认工作台。退出、会话失效返回登录页。首次设置沿用已有账号流程。复用既有 AuthProvider、AccountPage、主题和路由校验，消除提交与外壳同时跳转的竞争。

- 本地 TypeScript、构建通过；桌面/手机相关浏览器 37 项通过，1 项桌面不适用用例跳过。陈旧连接数量断言改为验证实际登录目标页面后定向复测，没有反复全量。
- NAS 10 服务运行、5 核心健康、guard ready；实际匿名浏览器入口 200，无页面错误，确认不存在工作区侧栏或外壳。HTTP 41 份资源与镜像逐一匹配。
- 未使用真实账号口令登录；登录与退出流程用隔离合成账号验证。无配置或数据迁移，其他产品镜像保持。
- 镜像：`127.0.0.1:19556/tianshu/login-entry-platform@sha256:fb63f81215c7ce9b0111f4b24aa7fa7b6071f8cbc58a63de06decac2c8da9bb3`。
- 冷备：`/volume2/tianshu-v2-resident-updates/login-entry-20261003-core/production-generation-b0f1ba6c10a24423ba36fe9900d1dbef/cold-snapshot`；隔离副本验证后切换，Dockge 快照同步。

入口：http://192.168.31.210:18446/
