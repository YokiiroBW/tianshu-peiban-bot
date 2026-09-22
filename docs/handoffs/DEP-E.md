# DEP-E 最终发布组合与验收接线

## 目标、范围与基线

执行用户授权的部署第三批Codex任务；没有子代理、DSH实现子任务、NAS操作、真实账户、付费模型或Docker/WSL安装。
根基线 `668d69e2c52ea55d9b1f102426196a5286c57bc5`，分支 `codex/dep-e-wave3`。
只修改 `deploy/tianshu/`、`tests/deployment/packaging/`、`tests/release_acceptance/` 和本交接。
不改产品、日志/恢复实现、根任务板、CURRENT、workspace或共享contracts；源码从固定Git对象读取。
交付为包含本交接的最终DEP-E提交，完整HEAD随交付消息报告；不得把根HEAD当产品版本。

## 固定输入

| 产品/组件 | 已协调验收并集成的提交 |
| --- | --- |
| Platform TS109 | `0a3cf65b8da19eabdd5d72f9f999281cbb52fdac` |
| Companion TS108 | `e94b609099365f75ca933d9fed03cdfbc83ec235` |
| Memory TS107 | `9a3b2bed6aebff9f0677f2c62e979859769e0c9c` |
| Gateway TS110 | `ec20f95e3849ebee968c4f00d7a31d14eccaa40f` |
| 日志公开包 | 根 `668d69e2c52ea55d9b1f102426196a5286c57bc5` |
| 恢复清单消费者 DEP-F R1 | 根 `543340ac5489545e32772eb28849b4a5c1038eed` |

续期合同为正式 `model-origin-renewal/v1`，manifest原字节SHA256
`c5017724187c1386b647fcc5b41ab3cb1702f27d6192f3a87fcefb23e7a5a61c`。
其他合同与这份新合同均经协调授权从权威contracts逐文件核原字节复制到私有快照，未转换行尾或修改原件。
根基线中的合同曾过时，不以其旧字节替换已发布合同。

## 交付变更

- Schema 1.1加入日志五个持久目录、唯一owner/完整目录备份组，四个products不变；保留可读1.0例子。
  早期不可变接口提交 `cb371ab453621841e4a498598529a4ddcc41d3b2` 已发DEP-F，五卷接口此后未变。
- 新公开 `configure-observability/reconcile-logs`：导出固定日志包，通过原CLI执行；重绑产品后从Git重建词表。
  旧日志CLI仅消费私有1.0投影；权威发布/恢复清单始终1.1。核心和日志两个项目，日志显式 `<core>-obs`。
  校验五个写卷、组合摘要及Linux权限元数据；日志配置后使用绝对主机路径，不可直接移动组合。
- 配置明确禁用自动候选，网关Companion授权修正为internal=true；否则真实Core内部调用会被拒绝。
  显式开启两端来源续期、核专用config-entry路由；不扩大scope/TTL，不给网关管理员凭据。
- 全新合成授权引导实际调用平台公开CLI publish/issue，检查格式/过期/启动余量，失败不自动重试。
  随后运行Memory公开新库迁移和四个原产品CLI。不是测试issuer，不跨产品写SQL或伪造业务回执。
- 验收读取真实公开capabilities；disabled_verified与memory_write_proven=false/archive=false分开。
  子进程成功配置解析观察加鉴权readiness、源快照/配置hash和PID记录；这是测试观察，需协调独立审查。

## 实际验证

Python3.12.14，Windows，本任务独立测试venv；jsonschema4.26.0、cryptography50.0.1、aiohttp3.14.1、
referencing0.37.0、fastapi0.135.1、uvicorn0.42.0、httpx0.28.1，Ruff0.15.7。共享测试依赖不作为各镜像正式依赖证明。

1. 包装原批次39项通过；随后新增日志五卷权限边界并执行权限11项全部通过，合计40个不同包装用例，0skip。
2. 验收运行器47项通过，0skip；Ruff check和完整diff whitespace检查通过。
3. 最终固定组合实际init→日志公开configure→preflight通过。公开reconcile对四条完整记录成功、缺一条准确失败；
   后端仅HTTPS查询替身，真实Loki/Vector链路未跑。正式DEP-F消费者读最终清单成功，少Loki卷拒绝；只做清单消费。
4. 四产品真实HTTPS：10 pass、0 fail、7 dependency_missing、3 not_run，报告incomplete。
   包含真实登录/CSRF、真实Web回复、四服务同correlation日志、配置加载和禁用能力事实。
5. 额外257次提交全部受理、257个不同correlation真实delivery成功、模型HTTP调用增量257（加首轮共258）。
   同会话、单条在途、短上下文0；公开capabilities无pending/blocked_scope/submitting/unknown，新库没有候选积压。
   负载持续310秒以上，整次运行2026-09-22 14:41:03.186166Z—14:46:32.707677Z，329.516秒。
   初始来源14:46:04.282640Z过期后仍有15次真实成功投递，末次14:46:21.749Z；同网关进程，没有重新issue或重启。
6. 结束后快照、执行文件和报告摘要均重新核验；公开inspect-acceptance验证20场景及产品/合同/功能绑定通过，release_ready仍false。

固定证据入口：`tests/release_acceptance/evidence/wave3/README.md`、`report.json`、`tested-manifest.json`、
`stress-delivery.jsonl`、causal原始事件、`combination.json`、`recovery-consumer.json`、`integrity.json`。
原报告文件SHA256 `c86d1a00b905a8553ba19b7a8bdf2cebdb3c40bdd3a775831a5aacf935a0517a`；
被测清单SHA256 `08c12281065b5e5e6e1cd515a1caae25cbd87d587ffb454c287a9e58e84e57a1`。
默认关闭Web的发布例子SHA256 `db9da299972163543c59edd9933cf247b645166a84640e47d5e70ec18fa09fb1`，不能反填到旧报告。
旧失败试验保留在 `failed-before-renewal/`，其调试断言/通用末尾错误局限在证据README说明，不抹去失败或误称产品缺陷。

## 未完成、风险和下一步

- ref明文未落证据，**本次也未记录ref hash**。同ref依据正式网关拒绝进程内换ref的约束及固定单次引导执行路径，
  不声称已有独立ref轮询观测。每次模型请求仅做真实HTTP总计数；delivery保留逐correlation，首轮有完整四服务关联。
- 模型TCP断开只会明确生成failed，不能伪称不确定投递；unknown、不重发/重启恢复、来源/模型撤销、超时取消、
  失败因果和日志不可写注入在此四产品适配器中仍缺失。协调已同意准确dependency_missing。
- Memory测试结束曾需要强制终止本次自有子进程；其他服务退出。不能把该清理当DEP-F正常停写或四产品恢复通过。
- Docker executable缺失，Linux镜像构建、UID/GID实际权限、正式安装依赖、容器停写、四产品恢复、NAS、浏览器渲染、
  真实模型质量与24h均未运行。Platform/Gateway Dockerfile与续期前相同，仍使用未固定digest的基础tag；产品写者后续处理。
- 实际首次授权引导接在本地四CLI运行器，未接到旧Linux smoke的容器签发/私有env及摘要更新步骤。
  新续期网关需要有效初始ref，静态占位值不能启动。后续Linux任务须补此接线，不能直接声称旧smoke能完成当前组合初装。
- 无自动长期记忆消费/提炼或Chat Audit归档；无应用日志持久确认后的回收授权。默认candidate/Web关闭，不自动晋升verified。

协调下一步独立审查本固定提交/证据及运行身份；随后安排Linux正式构建和隔离部署，再决定是否授权具体实机操作。
普通实现已完成并按固定提交停写；不自行合入根main或宣布全系统完成。
