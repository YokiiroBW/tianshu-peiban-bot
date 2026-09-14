# TS-050 source-sync 实际接线

固定Core a759f1755c3e9b2afa6b6e5b3fd5e2b6b8072d79、Platform a94d34534ba0b6002bdcdab9db1d5dd899a06a16、Memory ba0e50d56d6a4e816267d710c41c6b0c49035431、Gateway b3b101faf3902f05d80818b39fe7c91367865d4e。source-sync/v1 manifest 178d0ce66210bdfad4cfb85d8b5f0905b0b67f834e2a530efe5636ff0373633d；text/profile保持正式发布依赖。仅本任务授权范围，历史dc357e2/3c4e592证据不改写。

接线：合成渠道→真实Platform register_input/dispatch→Core ingest-actors/input authority→Memory identity→Platform inline确认；实际scope回填后才写入受信Memory部署event_scopes。Core/Memory均用原工厂与明确CA。Memory自有公开迁移端口建立schema3和独立guard；真实SourceAuthority执行C1-P1-C2/m0，TrustedWorkflow处理真实候选。无B认证适配器、无LocalFixtureSources、无跨产品SQL写。

Gateway无显式CA设置字段；使用原CLI独立进程，启动前以标准SSL_CERT_FILE指定仅本进程的临时CA。已核对安装aiohttp connector在导入时用ssl.create_default_context建立可信上下文；不修改依赖或系统CA。Platform/Gateway/录制模型/录制渠道全部实际TLS套接字，产品配置与调度均真实。

先验证私聊首账号W0→回填→零预算→生成→渠道sent→真实committed_event→候选→TrustedWorkflow完整组提交/幂等；随后群私A/B同P、修订/撤回/不可复活、W5、两槽/T3、发送顺序、A方案-B插话-A续接、unknown、重启/水位失败。确认/画像批准无真实签发方，按实际拒绝记缺口，不能注入固定True审批来冒充通过。

## 实际端口矩阵

| 段 | 实现与传输 | 证据边界 |
| --- | --- | --- |
| 外部提交登记 | 合成渠道持固定nonebot部署身份，调用真实Platform `sources.register_input` | 明确合成外部输入，不声称真实QQ/登录认证 |
| 输入派发/确认 | 真实Platform `sources.dispatch` → HTTPS Core `ingest-actors` →真实inline确认事务 | 不手填person/conversation/receipt；无额外映射RPC |
| 输入权限 | Core原工厂客户端 → HTTPS Platform `source-access/read(input)` | source_input与逐actor origin分开；不使用B认证适配器 |
| 身份 | Core → HTTPS Memory identity → HTTPS Platform origins/resolve | person由Memory产生；首次会话由Core产生 |
| 当前来源 | Memory原SourceTransport → HTTPS Core C1 / Platform P1(current) / Core C2 | 真实SourceAuthority、schema3、m0/guard与本地事务；未导入observation或布尔鉴权替身 |
| 后台范围 | 真实inline确认成功后，受信部署配置登记精确event_scopes | 只加入实际actor/person/audience/conversation，不存在通配scope |
| 主生成 | Core → HTTPS独立Gateway CLI → HTTPS录制模型 | 模型为允许的确定性外部替身；网关、配置、请求及回执检查为原产品 |
| 下行 | Core Sender → HTTPS录制渠道 | published send_request/send_receipt，实际sent/unknown状态；不声称真实SDK回执 |
| 候选 | Core真实committed_event → HTTPS Memory consume →原TrustedWorkflow | 草稿是明确确定性外部提炼替身；提交、完整组、版本与幂等为真产品 |
| 确认/画像批准 | 原TrustedWorkflow，无真实批准适配器 | confirm_revision/approve_profile/publish_profile实际503，不记作遗忘/共享成功 |

本轮实际结果、分批命令、剩余缺口和证据见 [验收报告](TS-050-source-report.md) 与 [实际轨迹](TS-050-source-trace.json)。历史partial/TLS结论保持原样，本轮不再把来源权威未配置作为阻断。
