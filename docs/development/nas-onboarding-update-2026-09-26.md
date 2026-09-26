# 首次使用流程 NAS 更新验收

用户明确授权“部署更新吧”。2026-09-26 已将平台 `bc41c05808271dc03d643d567e43788978e1e587` 更新至既有常驻实例，未重跑首装或迁移，未修改独立产品、其他 NAS 服务或小屋美术候选。

入口仍为 `http://192.168.31.210:18446/#/setup`。原管理员是部署验收预置账号，用户明确从未创建自己的账号；确认尚无持久用户账号文件后，更新显式启用 create 模式，删除运行配置内原 username/password_hash，保留业务身份、数据库和授权配置。首次初始化使用独立随机安装凭据，不复用内部服务密钥；凭据仅 NAS 私有文件和本机 `.runtime/nas-resident-access/first-setup.txt` 保存，本机文件ACL限Administrator/SYSTEM，不入库。管理员用户名及密码由用户在网页自行设置，未代建测试管理员。

## 固定版本与现场

- 源码从上述固定Git提交导出，原始源码包SHA256 `d7e5b6e17c6eb9a49285623e0f59e66a9b2b983303c76144ddac2002e6f691f5`；沿用原始合同字节，完整构建上下文清单位于现场 source-lock.json。
- NAS Docker实构建并通过本机仓库push/pull核同镜像ID。镜像 `127.0.0.1:19550/tianshu/platform@sha256:a236de76dc192895d7a67f460ce48db45f33f895b9a9f5d72a7895dc6aca398d`，ID `sha256:39f9bf92d4d999cef3c9273b21e5cda85a9b81d3f9926065ec7b573a06daceef`。
- 更新现场 `/volume2/tianshu-v2-resident-updates/onboarding-20260926`，固定执行脚本SHA256 `ee861f7da0a1c21513c78212f6d729c5ed33744e3e46c55190726cb6a644f315`。
- 先停止旧容量守护及其锁定的9个容器，确认全部exited/restart=no，再完整归档部署目录。备份 `deployment-before.tar`，64512000字节，SHA256 `4780aca7b0643a762fcc51cca4582ff590487bfbc9eb1de20da6c4a7c03cd968`，保留UID/GID及模式。原配置、单位文件、容量配置、容器检查结果另私有保存。
- 新镜像对现有状态执行公开preflight=ready，然后只替换Platform；其余8个容器保留原ID、镜像、挂载，恢复unless-stopped并依序启动。Platform新ID `18a5823565a6f75a2efb89b625e0a416581b9a92ae46adfb7641be3565e938bb`。
- 为新Platform身份建立新容量状态目录 `onboarding-20260926/capacity-state`，更新原守护配置及systemd单位所指state目录，保留旧状态及停机回执，重新arm/start/status=ready。阈值未放宽。

## 运行配置权威变化

此次是对原常驻安装的受控更新叠加，不重写历史首装收据。新Platform运行Compose为更新目录下 `core.compose.json`；其余8个容器仍使用原来的Compose来源。Dockge核心栈保存副本与新Compose逐字一致。原 `exports/first`、`exports/final` 和旧bundle-integrity为更新前历史材料，不再代表更新后的Platform配置；不能运行旧首装、旧finalize或用旧导出覆盖现状。当前容量配置记录新镜像与精确Compose身份。

## 实机证据

详细去敏回执见 `nas-onboarding-update-2026-09-26.json`：

- 四核心全部healthy、五OBS running，九容器unless-stopped；其余8个镜像/挂载/ID与更新前相同。Docker返回的挂载数组顺序会变，按目标路径排序后比较完整对象，不弱化字段校验。
- 容量守护新状态ready、心跳新鲜、9服务；空闲约465GB，部署计量约64MB。
- LAN实际GET session返回未认证、onboarding=create_admin；旧登录POST被409/setup_required拒绝。账号文件尚未创建，留给用户首次设置。
- Codex内置浏览器实际打开NAS页面，确认“欢迎来到天枢”、账号/设置密码/确认密码/安装验证码/创建管理员表单，视觉风格保留，页面已留给用户。
- 本轮未提交用户的新管理员密码，实机创建成功及用户首次登录应在用户完成设置后再确认。模型仍未配置，不能称真实对话可用。

构建首次发布未等待仓库HTTP就绪，push失败；线上当时未停机。保存原失败回执后加实际/v2/可用检查，同一构建镜像发布成功，仓库已正常停止。未重跑构建或首装，没有失败现场清理。

后续不得只回滚旧二进制而保留新账号状态，否则旧程序不理解新账号库。账号创建后必须按代码、配置、账号库及封条的一致集合做恢复。现有数据、日志、旧镜像和备份均保留。
