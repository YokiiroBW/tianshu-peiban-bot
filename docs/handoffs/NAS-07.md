# NAS-07 恢复资源适配

基线 bacba47。恢复演练消费已经由运行身份固定的显式 NAS QA 资源配置，复用日志包的资源校验；仅接受 QA 项目、回环绑定、四产品测试 TLS。克隆保留原 CPU 集合与内存限制，实际容器检查资源漂移，真实演练前检查主机能力。默认可移植 CPU/PID 限制保持。

首次 42 项受影响回归中 33 通过，新增资源来源约束误影响旧可移植夹具造成 9 项失败；约束缩小为 NAS 配置后，11 项演练回归全部通过。新增 4 项覆盖未绑定/局域网配置拒绝、资源漂移和主机控制器不足，全部通过。Ruff 通过。首次失败证据保留在本工作树 tests/deployment/recovery/nas07-verification.json，复验 nas07-drill-recheck.json；两者不是实机证据。

2026-09-24 实机补充：m 九组件正常停写、备份 nas-first-snapshot、恢复 restore-disabled 与公共生命周期 verify-restored 均通过。独立回执位于协调目录 docs/development/nas-disabled-restore-accepted-2026-09-24.json；原始 NAS 报告 next-m/recovery-m5/rehearse.json。原目录和恢复目录保持停止/禁用，product_functional_restore=false，克隆功能读回仍待。

真实数据布局暴露的修复已纳入：Grafana 配置目录逐文件入清单；从未创建、未登记且有所属状态父目录的可选 sidecar 可缺失；Loki chunk 的 base64 文件名可包含 +/=，原路径越界/链接拒绝保持。恢复相关 20 项清单/生命周期、45 项快照/枚举及文件名正反例通过。

日志 n 轮实机短链 12 项通过，5 项未运行、应用容量回收合同阻塞；原证据哈希独立检查通过，五日志组件全部正常退出 0。包含断线落盘、重建补传、轮转对账、去敏、权限、Grafana/Prometheus 和告警，不代表 30 日实际保留或物理磁盘耗尽测试。Vector 停止预算增至 90 秒，正常 SIGTERM，无强杀兜底。

7a18f5b 增加显式真实模型验收输入；默认合成入口不读取真实供应商。只接受先通过文本探针的 HTTPS 公网地址，密钥仅落 NAS 私有环境，发布文档不含密钥，不宣称流式能力。网页合成测试消息通过四服务调用已登记模型；结果单列 real_model_dialogue。93 项 packaging 测试通过，Ruff 通过。真实 NAS o 轮已通过，固定用户指定 AstrBot 日日新/deepseek-v4-flash：网页登录、消息受理、四服务模型回复、平台预检及正常停止均通过；四固定容器再次独立核实退出 0、无 OOM/重启。协调回执 nas-real-model-accepted-2026-09-24.json，原始报告 SHA256 2f355b809932eaecd44a6b3e73d8fda103afea6fbc20bdbd591c7991326823c7，身份 SHA256 a5a5bcaee5a0f9bfa5bf2c2b666d441a97bcfe331bcf7a0d8a73bc5654a6c87b。仅测试消息，不读取用户聊天；非浏览器渲染或 LAN 验收。

仍未宣称完整验收、正式可发布或已开放局域网。QA 资源配置不能被改名当作正式发行，LAN/TLS、长期配置刷新及正式运行方式仍需核实。
