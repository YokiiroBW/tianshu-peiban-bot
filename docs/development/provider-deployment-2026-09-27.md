# 供应商自助配置 NAS 更新

用户授权“部署吧”已执行。入口 http://192.168.31.210:18446 ，保留现有管理员账号与数据。设置中的模型管理已部署，可新增兼容供应商、配置模型、测试并设默认。本记录覆盖此前本地集成但未部署的状态；机器证据见同名 JSON。

## 已部署与验证

- Platform `1d51d888ff77594a1f6fbb1c7e0d4fdf92285ff8`，Gateway `e4f112f02d28a1ded134126feed33f15e003ec20`，Companion `31677983798ba27b24d57925feab4774c2eec30f`。Memory 保持原镜像和数据。
- 九服务 running、unless-stopped；四核心 healthy，五个观测服务无 Docker health 字段；容量 guard ready。Dockge 保存副本与当前核心 Compose 同字节。
- 仅在 NAS 内读取已授权 AstrBot 日日新供应商，凭据进入平台加密目录，未输出或入库。`deepseek-v4-flash` 的真实短回复通过，随后设为默认。
- 陪伴生产 HTTPS 客户端→平台动态选择→网关→真实供应商→路由回执核验通过；非空回复、模型绑定与 succeeded 回执均确认。使用合成探针，不读取聊天历史。
- 浏览器入口确认显示现有账号登录页。未收到用户自行登录的反馈，因此用户会话内配置操作、完整 Memory/Core 消息送达不在本次通过结论内。

## 当前现场与恢复依据

更新目录 `/volume2/tianshu-v2-resident-updates/providers-20260927`；核心 Compose 为其 `core.compose.json`，平台来源为 `platform.compose.json`。容量当前 state 为 `gateway-r1-release/capacity-state`，最新成功收据 `gateway-r1-release/completed.json`。该目录上层早期 failed.json 是保留的历史失败，不覆盖最新成功收据。

两次更新前均停精确九服务后作一致备份，路径、字节数与 SHA256 见 JSON。账号库和封条保留；未运行旧首装、重置账户或覆盖其他 NAS 产品。网关使用系统公共根加既有私有 CA 的出站 CA 文件。所有凭据仍只在现场私有目录。

## 故障与处理边界

初次构建时旧容量 guard 出现 docker_command_failed 并按设计停止九服务。精确退出/停止收据已留存。全 NAS 容器枚举与构建临时容器删除竞争是疑点，未证明根因；没有称通用缺陷已修。后续构建保留中间容器，容量 guard 持续 ready，不清理历史现场。

初次更新网关遇到 origin_renewal forbidden；实际旧 origin 已过期。通过平台正式 issue CLI 重发并更新网关私有环境后恢复，未直接修改数据库。更新脚本由总控监督执行，不宣称具备无人值守自动回滚。

原短回复测试 max_tokens=16 返回 upstream_invalid/unknown，原响应体未保存。独立的 256-token 诊断返回 HTTP 200、非空最终 content 和 stop。经 Sol 修改、Luna 独立审查，提交 e4f112f 仅把测试预算改为 256，保留单次请求、不重试、非空最终内容与完成原因判断。适配器21、HTTP28、根联合6通过。新版本部署后的正式真实短回复测试及运行链探针均通过；不把 reasoning_content 当最终回复。

这是供应商功能部署与限定真实运行验证，不代表小屋美术、全产品功能或完整浏览器聊天验收完成。
