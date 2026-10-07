# 用户详情管理员开关部署 — 2026-10-07

用户明确授权提交并部署。Platform `70909d64b870072dfeecfbef1310d243adf4fe5a` 已快进推送 GitHub main，并部署到 NAS；基线 `0a3e50c97a9403611e79995b1c74a9b31d06c490`。

用户页 `http://192.168.31.210:18446/#/memory/0` 选中用户后，姓名旁直接开关管理员身份。移除独立设置表单，旧 settings/7 地址进入用户页；角色技能和天气原地址不变。复用既有 QQ 管理接口、identity.explain 能力及版本检查，没有新增实际权限。已有范围限制授权继续保留。

## 验证

- 本地类型、构建、格式、12 项既有及 4 项新增浏览器用例通过。补充隔离真实 Platform HTTPS 服务/合成 QQ 资料上游桌面与手机各 1 项保存、刷新持久化、撤销测试通过。
- 前次合同哈希阻断已通过使用已发布 `3c38a69362b8a9ab26f102189b0efa9e67b19b74` 的 git archive 解决；未修改合同校验或生产合同。真实 HTTPS 联调使用一次性合成数据库，未修改生产管理员。
- NAS 固定提交构建镜像 `sha256:b391fc4b036cbcb8daa093491fd93b6831832dda6f0981e001a4124c3aa0d5a7`，Linux 运行时导入、合同、CLI、pip、网页产物检查通过。依赖输入保持原版本。
- 五核心 TLS live/ready 均 200；十容器运行、guard ready。57 个 HTTP 静态文件哈希全部匹配且 no-store。Dockge 快照已同步实际 Compose。
- 冷备 33 个 SQLite 验证通过，路径 `/volume2/tianshu-v2-resident-updates/user-admin-release-20261007-release/cold-snapshot`。管理员授权表只读摘要与冷备一致；用户状态、角色和天气密文配置保持不变。
- 仅重建 Platform，其他九个容器 ID 保留；Gateway origin 未重新签发。没有生产数据迁移、真实 QQ/模型/GPU 测试调用。

## 证据与边界

根工作区 `.runtime/user-admin-release-20261007/deployment-result.json` 汇总固定镜像、服务就绪、用户状态及静态资源回执。浏览器交互证据来自隔离真实 HTTPS 服务；生产未创建网页登录会话或进行管理员写入。部署验收为生产健康、固定镜像/静态资源和既有授权保留验证。

产品实现交接 `worktrees/USER-ADMIN/tianshu-platform/docs/handoffs/USER-ADMIN.md` 中未部署与联调阻断属于部署前状态，由本记录覆盖。Git 提交使用与历史一致的命令级 Codex 作者标识，未更改全局 Git 配置。

## 同日生产权限修复

用户报告开关返回 403，经只读核验，生产 web principal 有 qq.admin.view 但缺少 qq.admin.manage。用户明确授权修复后，仅向该现有 operator 的 actions 增加 qq.admin.manage；没有修改 QQ 管理员 grant。原产品镜像/提交保持不变，Platform 强制重建以重新读取绑定配置。

沿既有发布流程完成 33 SQLite 冷备、恢复及五核心 TLS live/ready 200，guard ready、十容器运行、其他九容器 ID 保留，Gateway origin 未重新签发。核验新运行容器挂载配置同时包含 view/manage；与冷备配置逐结构比较确认只有授权动作这一项变化，管理员 grants 表摘要与冷备一致，用户/角色/天气状态保持。Dockge 同步完成。

回执 `.runtime/user-admin-permission-20261007/final-readback.json`；冷备 `/volume2/tianshu-v2-resident-updates/user-admin-permission-20261007-release/cold-snapshot`。未借用用户网页登录会话或执行真实用户授权写入；用户刷新/重新登录后可自行操作。
