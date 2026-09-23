# NAS-07 恢复资源适配

基线 bacba47。恢复演练消费已经由运行身份固定的显式 NAS QA 资源配置，复用日志包的资源校验；仅接受 QA 项目、回环绑定、四产品测试 TLS。克隆保留原 CPU 集合与内存限制，实际容器检查资源漂移，真实演练前检查主机能力。默认可移植 CPU/PID 限制保持。

首次 42 项受影响回归中 33 通过，新增资源来源约束误影响旧可移植夹具造成 9 项失败；约束缩小为 NAS 配置后，11 项演练回归全部通过。新增 4 项覆盖未绑定/局域网配置拒绝、资源漂移和主机控制器不足，全部通过。Ruff 通过。首次失败证据保留在本工作树 tests/deployment/recovery/nas07-verification.json，复验 nas07-drill-recheck.json；两者不是实机证据。

2026-09-24 实机补充：m 九组件正常停写、备份 nas-first-snapshot、恢复 restore-disabled 与公共生命周期 verify-restored 均通过。独立回执位于协调目录 docs/development/nas-disabled-restore-accepted-2026-09-24.json；原始 NAS 报告 next-m/recovery-m5/rehearse.json。原目录和恢复目录保持停止/禁用，product_functional_restore=false，克隆功能读回仍待。

真实数据布局暴露的修复已纳入：Grafana 配置目录逐文件入清单；从未创建、未登记且有所属状态父目录的可选 sidecar 可缺失；Loki chunk 的 base64 文件名可包含 +/=，原路径越界/链接拒绝保持。恢复相关 20 项清单/生命周期、45 项快照/枚举及文件名正反例通过。

日志 n 轮实机短链 12 项通过，5 项未运行、应用容量回收合同阻塞；原证据哈希独立检查通过，五日志组件全部正常退出 0。包含断线落盘、重建补传、轮转对账、去敏、权限、Grafana/Prometheus 和告警，不代表 30 日实际保留或物理磁盘耗尽测试。Vector 停止预算增至 90 秒，正常 SIGTERM，无强杀兜底。

7a18f5b 增加显式真实模型验收输入；默认合成入口不读取真实供应商。只接受先通过文本探针的 HTTPS 公网地址，密钥仅落 NAS 私有环境，发布文档不含密钥，不宣称流式能力。网页合成测试消息通过四服务调用已登记模型；结果单列 real_model_dialogue。93 项 packaging 测试通过，Ruff 通过。真实 NAS o 轮已通过，固定用户指定 AstrBot 日日新/deepseek-v4-flash：网页登录、消息受理、四服务模型回复、平台预检及正常停止均通过；四固定容器再次独立核实退出 0、无 OOM/重启。协调回执 nas-real-model-accepted-2026-09-24.json，原始报告 SHA256 2f355b809932eaecd44a6b3e73d8fda103afea6fbc20bdbd591c7991326823c7，身份 SHA256 a5a5bcaee5a0f9bfa5bf2c2b666d441a97bcfe331bcf7a0d8a73bc5654a6c87b。仅测试消息，不读取用户聊天；非浏览器渲染或 LAN 验收。

仍未宣称完整验收、正式可发布或已开放局域网。QA 资源配置不能被改名当作正式发行，LAN/TLS、长期配置刷新及正式运行方式仍需核实。

2026-09-24 后续：m3 恢复克隆已实际启动九组件，经 HTTPS 公共接口读回备份前成功模型回执；源与克隆 18 个固定容器全部退出 0，无 OOM，原 restore-disabled 未激活。回执 nas-restore-readback-2026-09-24.json，原报告哈希 f2c0635fcd6ba9080a5d255ac451615a3a30dd1ce452aca1b36a3cbfb4c180cc。仅 gateway/data_readback 通过，状态 partial_functional_coverage；其他三产品及遗忘/撤销/unknown 不重发尚无恢复场景覆盖。

m1 仅预检拒绝容量只读挂载，没有创建容器。m2 实际启动因过期来源触发网关续期 forbidden，网关退出 1、陪伴未启动；其他七个组件退出 0，独立检查无任何运行容器。m3 仅为历史读回显式关闭克隆网关的在线来源续期，不签发/延长旧授权，不测试新的模型请求。新增恢复 access 桥只允许带回环端口的成员，容量只读目录必须来自同一克隆的既有挂载登记；NAS 关停预留 90 秒。13 项原/新增演练测试及 1 项容量挂载正反例通过。

LAN 新增独立 nas-cpuset-lan-qa-v1，仅 QA 项目、明确 RFC1918 IPv4、相同 IP 的 HTTPS origin 和专用测试 TLS，原 loopback profile 不放宽，正式 release 仍拒绝。提供最多 600 秒的浏览器窗口，密码只在 NAS 私有临时文件中，关闭/到期移除，停止流程保持固定容器 ID。96 项 packaging、5 项日志资源通过；NAS p 预检发现 bundle 二次校验漏适配（未启动），97eeec5 复用核心地址规则修正，并补候选 preflight 正例，13 项专项通过。

NAS q /volume2/tianshu-v2-validation-wave1/next-q/scope-q/deployments/source 已完整准备、preflight 通过，尚未执行/构建/启动，代码 coordination-nas07k=97eeec5853bdcbd15bf3ade7df194f636e1a6571。计划入口 https://192.168.31.210:19443。Windows 临时信任专用 CA 的许可已向用户请求，尚未获得，不导入也不跳过浏览器安全提示；公钥证书 .runtime/nas-wave1/lan-q-ca.pem，指纹见协调 nas-lan-certificate-2026-09-24.json。CA 2026-09-25 19:07 UTC 到期，叶证书比 CA 提前一天到期；若过期必须准备新证书，不能关闭校验。获准后只加入当前用户信任，验收后移除精确证书。
