# CONNECT 部署接线准备（未执行）

本文件为本轮整体贯通的配置计划，不是已部署证据。当前有效版本仍见 provider-deployment-2026-09-27.md/json；保留现有账号、供应商、九服务和独立其他 NAS 产品。

## Memory 浏览

B 确认现有 Platform Origins.issue/resolve 可以复用，不新增伪造身份接口：固定 local_operator/self_private actor entry，每次请求新签 origin；只增加精确 route `{caller:platform,receiver:memory,purpose:dialogue}`。Memory 独立 issuer 凭据映射 Platform `origin.resolve` principal，resolver 固定 `{caller:platform,purpose:dialogue}`。固定 web_memory.entry_id；已登录账号必须与 entry owner/account/identity 相符，不能把任意管理员默认映射为 household。

Memory 另设仅有 browse 权限的专用 caller 与 browser_reader，绑定实际账号、actor 和完整精确 scopes。模型选择、Companion、来源同步及浏览身份不得混用。浏览仅是既有身份系统中的独立受限调用；复用 purpose 名不授予写入或用户确认权限。签发前和异步响应返回前重校来源、账号/范围与会话。完整字段以 B/M 固定提交交接为准，必须有跨服务联合正反例后才发放现场权限。

## 其他待接线

- Knowledge：当前产品知识 CLI 已支持受控 TLS/Host 的非 loopback 绑定。需要独立服务进程、固定 client/逐操作权限、项目白名单、私有证书/配置及显式 schema 准备。未核完整部署资源/日志/容量归属前，不添加临时裸进程；不让平台读取对端数据库。
- Persona：根 persona-management/v1 目前只是独立发布准备包；旧 candidate 保留。真实 HTTPS/Platform/Chromium 联合与独立审查完成后，才固定 published manifest、消费者 hash 并启用 production 配置。
- Life：配置正式 life_readers 及已有剧情授权，只读取已发布日记；不启用草稿管理权限，不以读取触发 tick 或写作。
- AssetLibrary / Home Assistant：属于独立外部服务。建立可配置、可检测的用户流程，保留对端身份和资源范围；无正式配置不得显示连通，不自动执行设备动作。

## 实施门槛与记录

先固定 B/U/M 代码与 A 审查结论，完成真实本地服务联合测试，再备份和受控 NAS 更新。若服务集合变化，必须同步容量 guard 精确容器身份、Compose 来源、日志观测与备份边界；不能沿用仅九服务的旧断言当作新服务已受保护。按功能矩阵逐项记录业务读取和明确用户操作的结果，容器健康不代替功能验收。
