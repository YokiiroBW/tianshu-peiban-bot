# TS-177 平台基础设施页面与观测接入

执行模型 `gpt-6.1-sol / max`。主工作区 `C:/YOKI/Codex/tianshu-peiban-bot`；任务检出 `worktrees/TS-177/tianshu-platform`，初始固定基线 `3ad32d769501509f054110208ddc6600bd26c3ed`。

**接口只读核对已通过，独立实现已启动，现放行最后装配。** 协调核对 TS-174 代码固定于 `185e860e165b5605f44988ad3ae0f6d709786f76` 且产品 tracked 工作树干净；对方已进入部署验收。先合入这份精确历史、保留媒体修改，再完成 `service.py` 接线。若对方验收后另有修复，由协调串行对齐。不要新造多个凭据库或万能连接器层。

实现范围：`services/platform/infrastructure/`、`apps/web/src/features/infrastructure/`、对应 `tests/backend/test_infrastructure*.py` 与浏览器测试／专属fixture，`apps/web/playwright.infrastructure.config.ts`、`tests/backend/infrastructure_fixture.py`、`docs/platform/infrastructure.md`、`docs/handoffs/TS-177.md`；必要薄接线可改 `service.py`、`server.py`、`web_console.py`、`external_catalog.py`、`tasks.py`、`apps/web/src/app/{App.tsx,modules.ts}`、设置页中节点连接入口。上述共享文件由本任务单写，其他能力保持。新增依赖需说明实际用途，锁文件由同一实现者统一维护。

交付完整第一批：管理员配置节点、测试连接、后台有界采样、当前与旧数据新鲜度；NAS／Linux／OpenWrt 节点详情；Docker三栏列表、节点／应用筛选、健康与运行分列、CPU／内存／网络／IO、端口／挂载／限定日志。消费固定候选执行器，后端缓存与刷新独立于页面，不把浏览器轮询当后台采集。`#/home/1`容器、`#/home/2`节点；HA及游戏服语义保持。已认可设计在根 `output/ui/docker-status-v3` 和 `docker-management-v1`，复用令牌，不另建壳。

首批无真实设备控制按钮假成功，无历史曲线假数据；后续动作／历史由协调另发精确卡。平台只调用注册执行器，不挂Docker socket。候选接口允许本卡隔离测试，正式生产包与节点部署单列。

相关回归接线允许更新 `apps/web/playwright.config.ts` 的专属套件排除列表，以及 `apps/web/tests/shell.spec.ts` 中旧容器占位页的断言／合成未配置响应；这是既有路由实现后的受影响测试，不修改其他壳行为。

验收真实平台HTTP与执行器合成链，不只mock前端。覆盖管理员／会话／CSRF、并发保存、撤销连接、节点切换迟到响应、进程超时关闭、离线旧值、超过64容器、无健康检查和未知指标。跑现有格式／类型／构建及受影响页面回归，记录任何既有失败。交回提交、验证、截图／本地证据位置和交接，由主协调者最终验收，不能推送或部署。
