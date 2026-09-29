# 多角色部署记录（准备中）

本记录仅针对多角色运行时，不包含并行 QQ 身份功能。最终是否完成以文末验收与同名 JSON 为准。

## 固定版本与范围

Platform `17af1b43b432f4bd6e250192fcfad35f8b04a8c1`，Companion `9863800f0fcca67f59aa285ae14802d391f77e50`，Memory `870d9269d33b88ed34c6445eb7d95f791b43a914`。Gateway代码不变。三个产品从固定 Git archive 构建、记录 SHA256；镜像在 NAS 串行构建，注册表仅127.0.0.1:19550短时开启。首轮生产不代建角色或改原人格，不改变机器人观察与回复规则，不发QQ消息。

## 更新步骤

发布已联合验证的 role-runtime/v1 合同，串行快进合入三产品主线。镜像成功且独立审查通过后，停止容量服务以取得十服务一致停机备份。备份 `/volume2/tianshu-v2-resident`（包含账号、模型目录密钥、三个数据服务和现有sidecar）、Compose、容量配置、unit及Dockge配置；私有备份不入库。

只增加 Platform role_runtime（Memory HTTPS与既有方向唯一的TS_MEMORY_BROWSER服务凭据、可信CA），管理员role.manage；Memory增加持久 `/srv/tianshu/role-grants.sqlite`、注册platform caller的role_admin、companion caller的allow_runtime_roles。保留原allowed_actors和全部其他配置；未显式接管前不生成旧角色动态grant。

替换Platform/Companion/Memory镜像，按既有流程刷新Gateway来源授权并重建Gateway。保留Knowledge和五项日志服务容器；全部挂载保持。核对账号文件及原Persona数据哈希，再配置新capacity-state、重新arm和启动容量服务，同步Dockge。运行Compose路径仍 `.../bots-20260928/{core,platform}.compose.json`；新部署资料目录 `.../role-runtime-20260930`。

## 验收与边界

独立本地真实HTTPS四服务+Chromium联合4项通过；最终单行取消guard修复追加真实HTTPS1项通过。不同角色模型/人格、网页来源拒绝、合成BOT、取消与重启见 reviews/role-runtime-final-joint-2026-09-30.json。独立门禁报告另存。

NAS验收计划：十服务容量guard、五核心就绪；专用服务凭据读取Core角色目录和Memorygrant状态；原账号与Persona保留，机器人观察策略不变；LAN资源哈希、匿名管理拒绝、归档读取、日志检索。实机网页若需要用户重新登录，明确保留这一验收限制，不冒充已登录表单验收。

## 恢复

发生异常先保持错误记录，不盲目重复一次性更新脚本。基于新目录的capacity-before、core-before、platform-before、unit-before与deployment-before.tar受控恢复完整集合。已接管/新增角色之后不可单独回退一个服务或sidecar；必须同时恢复Platform主库与roles账本/模型目录、Companion主库和Memory主库与角色grant。原始账号、备份密钥不打印或提交。

当前：镜像构建、最终门禁进行中，尚未切换运行服务。
