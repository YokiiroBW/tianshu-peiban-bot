# 主动陪伴修复阶段发布 R38

用户已授权本聊天接管串行发布。PC-01、03、04 的代码与 collector 已发布，PC-05 已完成生产收敛；完整 18 卡目标尚未完成，W1 的日记及真实主动链路仍在推进，禁止进入 W3。

当前 `2026.10.30-unified-linux-r38`，固定 image `sha256:4a8b2376dc7c01616d6c77326b84b54d3166a8d4f76394f918e92431c64ef057`。Companion 代码 `57879c62d5549dfde6043c3a06d1e68beaec4655`、Platform `73cea0b6bcb782b5a8ff03d81248c20000ca1c12`、宿主代码 `ed128f3fede0e693c97366ab1c08fb6641be1ea1`；相关 main 已正常快进推送。Memory、Gateway 保留 R36 版本。

已核容器 healthy、六业务/五观测 ready、原鉴权 TLS ready 200 与九项检查；实际 LAN 网页入口的首页、活动页及状态资源 hash 匹配。37 个挂载、14 个非目标容器保持；与 R36 原始快照相比，1130 个配置文件保持，变化仅占位角色登记删除、闭合日志 vocabulary/Vector 规则与同 scope 启动租约，另添加 34 份版本化公开合同文件。容量守护 active/armed，20 GiB 底线保持。

占位 `actor:household` 按用户明确决定从运行角色、人格登记、角色绑定/读者列表和 live actor 行删除；历史活动、日记及内部控制/来源模板标识保留，不扩大授权。停写后 dry-run 与预期同为 7 条活动收敛、3 条 actor 变更；两表 COPY 和原行回退材料已存入既有 tooling 备份区域。7 条旧活动转 dormant，paused-null 为 0，checkpoint/result_refs/未知原步骤保留；未迁移 life_events、life_known 或 life_diaries。

R37 首次启动未通过：我的打包脚本继承 077 umask，覆盖公共目录时带入 0700，业务 UID 无法打开宿主和观测脚本。已用 R38 将三处公共目录恢复为 R36 原有 0755；业务 UID 的原生 owner guard 执行、观测 CLI 与实机启动通过。R38 复用已核代码字节和定向验证，没有重复数据收敛。原失败保留在收据，不宣称零中断或第一轮全绿；未来构建解包步骤已修正。

相关局部活动、原回执、防重放、合同、前端类型/构建与真实 PG 临时表备份/幂等/回退验证通过。未跑全量套件，未把替身与健康称真实陪伴质量。此阶段零新增真实模型测试、零 QQ 测试发送；受控消息的账号授权只保存在本地工作记录。详见[结构化收据](proactive-companion-repair-release-2026-10-30.json)。
