# TS-050 真实source-sync联合验收

> 后续协调复跑发现W5失败（9通过/1失败），本候选未合入。下文保留原本地验证历史；当前状态与独立原始失败、传输复现见 [W5诊断](TS-050-w5-diagnostic.md)。一次被动W5正常不覆盖原失败，产品修复前不解除阻断。

2026-09-14。**真实本地文字/来源链已经贯通；本次请求的完整矩阵仍为partial。** 两批定向验证共 **10/10通过预期断言**：稳定9项54.939秒，再补Core实际召回进入模型请求1项4.581秒；不是把拒绝场景算作完整功能成功。

剩余阻断收敛为真实遗忘确认签发、画像批准/发布；原SourceAuthority未接线、Memory HTTPS缺口已经在本轮真实组合中解除。旧 `dc357e2` / `3c4e592` 证据保持原样，六份历史文件逐Git字节校验；本报告和 [新轨迹](TS-050-source-trace.json) 独立记录新结果。

## 本轮基线

| 产品 | 实际测试提交 |
| --- | --- |
| Core/Companion | `a759f1755c3e9b2afa6b6e5b3fd5e2b6b8072d79` |
| Platform | `a94d34534ba0b6002bdcdab9db1d5dd899a06a16` |
| Memory | `ba0e50d56d6a4e816267d710c41c6b0c49035431` |
| Gateway | `b3b101faf3902f05d80818b39fe7c91367865d4e` |

source-sync/v1 1.0.0 manifest LF SHA256：`178d0ce66210bdfad4cfb85d8b5f0905b0b67f834e2a530efe5636ff0373633d`。依赖text-dialogue/v1 `81e6cc4ddef7c6f82e055d4cb04b090db036dd5c52763473ce697aa02db478a1`、profile-memory/v1 `488d05438dd5b5abaa43a66a7eab0eb5cf615d5af01a964a7286cd23e68f7eb7`。运行前后核验协调HEAD/祖先和源码快照全部Git blob，额外文件仅准精确 `.ts050-commit`；产品自己的合同加载器核验完整发布包。

源码只从上述正式提交导出到 `.runtime/ts050-source`，没有editable安装、修改产品/锁/共享合同或读worker实现。Core和Memory使用原工厂；Memory通过公开迁移方法从合成新库建立schema3和独立guard。实际 [接线矩阵](TS-050-source-notes.md) 区分了产品、受信应用端口和外部替身。

## 实际覆盖

| 场景 | 结果 | 本轮可证明的事实 |
| --- | --- | --- |
| 私聊首账号W0全链 | 通过 | Platform登记精确source_input、真实input授权，Core用Memory真实身份产生inline admission/receipt，Platform实际HTTPS响应后原子确认。零预算来源屏障、生成、sent、真实committed_event、Memory候选、TrustedWorkflow两unit完整组提交及同job幂等均贯通。 |
| 完整记忆与零预算 | 通过 | 实际Memory召回返回同组两条完整记录，0预算无正文但完成来源屏障；追加一项证明下一轮Core的实际网关请求携带相同record_ids、两条原语义和完整dependency_group，每轮model_calls=1。未把单独Memory接口成功当作Core已使用证据。 |
| 群/私同P A/B | 通过 | 两种受众分别验证相同P共享physical_receipt但A/B有不同actor receipt、独立组和候选；P/A由实际Core facts证明。A重复消费/提交不吞B，跨actor origin/scope请求403；群与私conversation不同。 |
| 修订与撤回 | 通过 | edit revision2只给A新receipt，Memory同步P变化使A/B旧组都不可召回，旧候选提交拒绝；retract revision3无actor admission，实际物理墓碑withdrawn，高revision复活在Platform返回409 scope_changed。 |
| 画像版本域 | 部分通过 | 原profiles API与Core客户端实际执行空/零预算探针。私密旧组失效提高text版本，未存在活动共享画像时profile epoch不变，version_domain=profile-memory/v1。已批准画像的正向查询/撤回失效未通过，原因是批准/发布入口缺失。 |
| W5连续短句/名字 | 通过 | 实际墙钟五秒窗口，“我叫”“小明。”同collector整组封存；下一轮“我叫什么”模型请求包含真实近期输入与已发上下文，录制模型仅在实际prompt含名字时回答“你叫小明。”，该文本经真实Core/Gateway到达渠道。 |
| 两槽/T3及有序发送 | 通过 | 外部模型A被确定性gate延迟，B先完成；Core实际状态generating/ready_to_send/queued，未抢发。释放A后全会话发送序号1/2/3、actor A/B/A，每轮一次主调用。 |
| A方案→B插话→A续接 | 通过 | 第三轮实际prompt delivered_dependencies只引用前一个同scope A轮，未引用B；与上述两槽/发送顺序同一真实流程验证。 |
| unknown和Core重启 | 通过所列范围 | 录制渠道返回unknown后重开真实Core ASGI/SQLite owner，closed_unknown不重发；原key重试保留receipt，随后新轮可sent；2个输入只有2次主调用/2次下行和2个候选。未模拟硬断电或真实SDK回执查询。 |
| 恢复/低水位 | 通过拒绝边界 | 正常Core重启保留generation/sequence。另路径启动真实较旧完整Core库，低水位下Memory503不服务旧缓存；实际撤权同步后，另路径恢复旧Platform allowed快照，Memory503且guard/候选状态不变。没有SQL改大水位或改权限造状态。 |
| Memory guard缺失 | 通过拒绝边界 | 停服务后把当前guard保留到另一个诊断文件，工厂真实503，未重建guard；保留文件SHA一致。没有重写旧检查点来解锁，也没有声称完成恢复。 |
| 遗忘确认 | 阻断 | 使用实际记录、scope、binding、HTTPS解析context构造正式确认输入，原confirm_revision无真实adapter→503；绕过确认直接HTTP forget→403，原记忆仍在。**不声称遗忘成功或跨revision suppression已联合通过。** |
| 画像批准/发布 | 阻断 | 原approve_profile/publish_profile均503，未调用LocalFixtureSources、迁入预批准组或注入固定True。群admission不被当作共享批准。 |

## 数量、来源与安全边界

- **20次**实际Gateway→录制模型TLS请求，**20次**Core→录制渠道请求：**19 sent、1 unknown**。每场景原生模型响应文本与下行文本逐项一致；一般原生字节保真/旧网关unknown专项引用历史证据，没有泛化重跑。
- **1,087条**Core/Memory/Platform请求录制全部HTTPS，包含真实input/current/facts/identity/select/profiles/check/consume/config读取。新轨迹保留每次请求/响应SHA和状态；从实际嵌套时间区间提取29组C1→P1→C2样例，逐组断言相等Core head与精确admissions关联。m0、失效事务和guard运行的是原Memory SourceAuthority；没有向它导入observation或伪造m0。
- **66次**监听关闭检查全部通过，Gateway独立CLI子进程全部停止；临时数据库、证书、密钥、备份和故障注入副本由测试清理。缺guard日志为预期负向证据；一次asyncio setup耗时诊断非失败，无关闭超时。
- 所有账号/输入/模型/渠道数据均合成。原作者输入由明确合成渠道调用真实Platform受信登记端口；这不是QQ/TG登录或网页登录证据。外部提炼草稿是确定性替身，完整提交/血缘/版本/幂等由TrustedWorkflow完成；不声称真实模型提炼质量或输入陈述为真。
- Core服务URL是基础地址；Memory source_sync URL是完整正式端点。所有客户端验证临时CA。Gateway原CLI启动前仅在该进程环境设置SSL_CERT_FILE，使用aiohttp/标准库原默认可信上下文；没有修改系统信任、certifi或库代码，没有verify=False。
- event_scopes只在真实inline确认后把实际actor/person/audience/conversation写入本测试受信部署配置，不存在通配授权。测试只用产品公开应用方法写业务，恢复测试只复制停止后的完整库到新隔离路径；没有跨产品SQL写或手填身份、版本、组。

## 验证和重现

代码稳定后审查完整差异，ruff check/format（无缓存）、AST通过；先有私聊单项摸底通过，随后9项稳定范围全部通过。发现还需证明Core实际使用长记忆后，只追加 `test_ts050_source_recall.py` 一项，4.581秒通过，没有重跑原9项。新证据汇编核对两批结果哈希、相同产品/依赖版本及不重叠用例名称，合计10项59.520秒，不包装为一次10项运行。

在TS-050任务检出、已有隔离venv中，今后可一次运行当前全部10项：

```powershell
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py prepare
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py run
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/collect_source_evidence.py
```

本次实际增量命令为 `run`（当时9项），然后 `collect_source_evidence.py --archive .runtime/ts050-source/validated-nine.json`，再 `run --pattern test_ts050_source_recall.py`，最后 `collect_source_evidence.py --prior .runtime/ts050-source/validated-nine.json`。成功输入未变的产品全套、旧partial/TLS与旧网关专项未重跑。

首次新环境可用Python3.12+执行prepare，再用uv在 `.runtime/ts050-source/venv` 创建环境并安装生成的 `requirements.txt`，缓存限定本切片 `.runtime/ts050-source/uv-cache`。本次复用原38包环境：Python3.12.14、FastAPI0.135.1、uvicorn0.42.0、httpx0.28.1、aiohttp3.14.1、cryptography50.0.1（仅证书工具）；运行器检查四产品runtime声明约束，全部版本/来源在轨迹中，没有改产品锁。

原始脱敏录制约2.9MB留在忽略的 `.runtime/ts050-source/results`，本次Git内约1.2MB汇编保留关键完整事实/模型请求、所有HTTP时间线投影与原始结果SHA。可用来源ref替换为SHA256以保持关联，HTTP SHA指脱敏前原字节；汇编不是可重放HTTP输入。凭据/私钥/环境密钥和临时绝对路径不入证据。

## 下一步归属

1. **确认签发入口与Memory集成**：真实批准者必须认证具体forget/correct的完整semantic_request、账号、scope、binding_version、记录版本与期限，再经已有TrustedWorkflow确认端口登记；服务token、聊天中的confirmed或固定True都不足。接通后补真正forget成功、旧source高revision/旧候选/旧allowed均不能清除suppression，以及A遗忘不删除B。
2. **Memory画像批准/发布**：当前两个TrustedWorkflow方法明确503，需要真实主体/类别/群私范围批准与受信发布实现，随后补活动画像的正向读取、P撤回、私密活动不污染公开epoch等联合场景。已有空域/零预算通过不能代替它们。
3. **另列运行验收**：硬进程崩溃/断电、真实SDK回执查询、真实登录/账号/模型、归档、PostgreSQL和长时容量均未运行。本合同仍有共同读点后的发送竞争和256依赖边界，不宣称生产可用或分布式发送事务。

协调者审查和集成后决定任务板状态。本任务不推送、合并、部署、启动下游或修改产品。
