# 观察策略部署计划（执行前草稿）

用户已要求持续推进至 NAS 更新完成。当前状态仍为本地实现与验收，不是部署成功记录。

目标为现有天枢十服务组合和已安装的 NoneBot，保留 192.168.31.210:18446 入口。更新 Platform、Companion、Memory 与 NoneBot 插件 0.3.0；Knowledge 继续使用原镜像。不安装或启用另一个 AstrBot 实例，不操作 SnowLuma 其他账号，不发送真实测试消息。为已接入 QQ 1365939091 启用群/私默认仅观察。

## 准备与执行顺序

1. 实现任务固定三产品与合同提交，独立审查关闭已知问题；真实 HTTPS 联合、宿主、网页及迁移验证通过。只合入明确任务提交，不带无关锁文件或工作区改动。
2. 固定 Git archive 与文件哈希，NAS 隔离目录 observation-20260929 构建镜像；三产品推入现有回环 registry，记录 digest。NoneBot 基于当前已验证宿主镜像替换插件 wheel，不改变框架依赖；记录基础镜像、产物哈希、最终镜像 ID。
3. 停机前再次核对容量状态、精确容器身份、Compose 配置。NoneBot Compose 除镜像外必须与原配置语义完全一致。保存配置、容量状态、Docker inspect 私有快照。
4. 停止容量守护令其正常停止十服务，再停止 NoneBot。完整备份部署目录与 NoneBot 持久目录，记录哈希。执行 Memory 显式 migrate-observations，单独保存迁移前数据库备份及回执；一次性迁移容器自动移除，避免影响精确服务盘点。
5. 安装最小配置增量：Platform memory-reader 增加 observation.verify；Companion bot_observation_enabled=true；Memory 增加 observation_source 回查配置及 companion 的 observe_ingest/observe_query 操作。密钥复用现有受管服务凭据，只在 NAS 私有配置中读取/保存。
6. 替换 Platform/Memory/Companion；Gateway 因现有来源凭据续签流程重建。其余六服务 ID、全部原挂载、账号数据保持。更新容量配置并重新 arm/start，同步 Dockge。
7. 更新 NoneBot 镜像与 Dockge 构建 wheel/Dockerfile。验证版本 0.3.0、真实观察协议、原 instance_id/连接密钥一致、账号在线。
8. 通过运行中 Platform 的受鉴权 observation-admin 接口登记默认仅观察，不另构造 Platform 实例、不手写业务 SQLite、不重置网页登录账号。
9. 验收五核心 ready、十服务容量 guard ready、NoneBot 真实能力、群私 observe_only。仅读取计数核对宿主捕获/持久 ACK/Companion 归档状态/Memory 来源归档及无回复认领/发送；不输出真实聊天正文。若现场没有新事件，明确区分链路就绪与尚无实际归档样本。

## 失败处理

不重跑首装，不静默覆盖一次执行标记。任何未知结果先读当前状态再决定精确续行；备份、失败回执、旧镜像及旧容量状态保留。需要回退时协调停止当前精确容器，恢复匹配版本的数据和配置后重新核对/arm，不能只恢复旧镜像而保留不兼容数据库。此草稿不声称回滚已演练。

脚本位于主协调工作区 .runtime/observation-*.py，尚待固定产物后的最后核对。运行记录与最终验收另写部署报告，不能以本计划作为完成证据。
