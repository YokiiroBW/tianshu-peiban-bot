# 订阅概览与模型功能分工联合发布

用户明确授权将本对话订阅更新与“设计天枢陪伴多模型协作”对话已实现内容一起推送部署。该对话实际交付为六类模型功能配置；自动辅助委派与两阶段回复尚未实现，本发布不把配置能力称为完整多模型协作。

## 固定版本与集成

从实际 GitHub main / NAS Platform `619c944924f582e9fe472f89d666d2da03c6f966`、Companion `537ea036298a239b8dae29baf7dba1f7ea4e39c5` 建独立检出，不用旧根或 projects 检出覆盖线上。

- Platform `7d2403c1f4aae5472c658fd9631b334daf7debe9`：合入订阅 `251a8fe`，以及另一对话原未提交功能现固定为 `0654aca`。解决 App/modules 合并冲突，保留线上设置空项、位置天气以及旧设置7→记忆入口。订阅根页概览、竖向子导航、大标题去除全部保留。
- Companion `fbb378b65d941201d9e65a52235b087f1b86d752`：合入模型功能 `bf5147f`，保留线上生活活动、意图、情绪及投递修复。
- 合同 `cc4e0e79011be16d0ab5facfd619a0bd41dacaf9`：新增 provider-functions/v1 增量，旧固定合同字节不变；平台先于陪伴启动，Gateway无需更新。
- 三者已非强制快进推送对应 GitHub main。候选检出位于 `worktrees/subscriptions-models-release-20261011/{platform,companion,coordination}`，根历史改动未混入。

## 功能及边界

订阅概览显示完整任务台账统计、订阅环图、暂存盘和近期任务，复用现有控制器/业务面板；未知与未配置如实显示。模型管理新增主对话、工具、代码、搜索、日常与日记、记忆整理六类分工；主对话及日常/日记已接通，其他功能仅支持持久配置和内部选择。已有角色模型、供应商、默认指针、密钥不自动变更；未单独绑定时按原规则继承。

媒体目标仍需实际 AssetLibrary/媒体库与专用发布配置，不能把新版页面上线称为真实下载入库完成。独立 AssetLibrary 候选和迁移主线未在本次覆盖部署。

## 验证

- Platform model_functions/catalog/overview/runtime 49项；Companion model_selection/life/function_selection/daily_life 68项全部通过。后端实际代码在前端最终格式/测试提交后未变。相关 Ruff通过。
- 订阅导航及概览45项、模型配置真实平台HTTP桌面/手机2项、类型/构建/格式通过。壳初轮13通过/6失败/1跳过，其中2项把已在线的settings/9误当非法的旧断言修正并窄重跑2通过，4项既有小屋标题失败保留，未伪称全绿。
- 新增合同schema与3个实例通过。测试用合成账号/模型，没有真实收费模型请求或QQ测试消息。
- NAS固定源码构建：平台87个模块与源码/合同/59网页文件哈希、CLI、pip check通过；陪伴67模块及95源文件、CLI、pip check通过。运行校验容器无网络和生产挂载，依赖输入保持。
- 平台镜像最初字符串检查错误要求存在动态拼接的固定接口地址而失败；修正为界面标识和绑定字段检查，同一镜像通过。修正脚本初次受Windows默认GBK读取失败导致一次未变化复验，随后显式UTF-8成功；未重建镜像或修改产品绕过。真实接口已由HTTP浏览器套件验证。

## NAS 结果

已部署 Platform 7d2403c 和 Companion fbb378b。五核心实际 TLS live/ready 均200；十容器运行、guard ready；另外八个容器ID和Gateway origin保持。59个实际网页资源逐文件哈希与镜像一致、no-store；两份Dockge快照与实际Compose完全一致。

冷备 `/volume2/tianshu-v2-resident-updates/subscriptions-models-release-20261011-release/cold-snapshot`：34个SQLite数据库通过离线恢复验证。所有settings文件、角色、管理员grant、天气配置、供应商记录/默认指针/加密key保持；新增model_function_bindings附加表及pre-functions独立升级前备份已确认。未自动更改任何功能模型绑定。

Platform镜像 `sha256:83c319cc6ebec44f36d2be8d9769a799cb7272ca43ba4883f0ff10a57e5ebc0e`；Companion镜像 `sha256:f42f5da3e5395ee75ab54ea4847ccea71ba6cde3fb3c4f8fa929a66eaaa526b8`。旧镜像/冷备/发布定义保留；回退不能用旧账本覆盖上线后新增用户数据。

入口 http://192.168.31.210:18446/#/subscriptions；模型分工从设置→模型供应商进入。未发送真实QQ测试消息、未调用收费模型或真实B站下载；服务就绪不等于上述外部能力全部实测。媒体targets仍为0。

脱敏结果见同名JSON。执行证据保存在忽略的 `.runtime/subscriptions-models-release-20261011/`，不将真实配置与凭据入库。
