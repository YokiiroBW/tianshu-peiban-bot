# TS-050 后续切片：Memory → Platform 真实 HTTPS

2026-09-14。**定向7/7通过，11.275秒；完整L0仍为partial。** 本次解除的是Memory产品认证器到实际平台的HTTPS接线缺口，来源权威、召回、发送和候选提交缺口未解除。

历史提交 `dc357e2cefb3e3df7c427938da38dd2c67d28341` 的8项证据保持原样；本报告及 [新轨迹](TS-050-tls-trace.json) 独立保存本次结果。旧报告/矩阵/来源提案/轨迹的Git字节SHA256已对照该提交复核，记录在新轨迹中。没有把旧网关字节与unknown成功结果计入本次7项，也没有重跑那两个场景。

## 固定版本与实际接线

| 产品 | 本次固定提交 | 接入状态 |
| --- | --- | --- |
| Memory | `69b29f3a6b8cd61d39d733870d136de2caeb75f0` | 正式集成TS-032；原 `configured_app()` / `Authenticator`，配置 `callers.companion.issuer_ca_file` |
| Platform | `a5ee59ff67a2de7a7e0d8328ea3c0f43e1d6a209` | 真实认证/签发/映射/撤销；实际HTTPS resolver |
| Companion | `812019e287a5bf37d9b5d810a028ffea828b872e` | 真实HTTPS来源、Memory客户端、持久Core与W0收件 |
| Gateway | `b3b101faf3902f05d80818b39fe7c91367865d4e` | 基础设施按原fixture启动，**本次没有网关请求或新的网关业务通过声明** |

text-dialogue/v1 1.0.0，manifest LF SHA256仍为 `81e6cc4ddef7c6f82e055d4cb04b090db036dd5c52763473ce697aa02db478a1`。Memory包含TS-031代码，但本次未配置/加载profile合同，也未验证画像能力。四产品只读协调检出核对HEAD/祖先，再从固定Git提交导出到 `.runtime/ts050-tls/sources`；运行前后逐blob核验。

| 段 | 本次事实 |
| --- | --- |
| Memory来源解析 | **不使用方案B的 PlatformPortAuthenticator。** 产品工厂读取临时部署配置，原Authenticator建立可信TLS连接，实际平台返回companion→memory上下文；没有字典来源、替换HTTPX或假issuer响应。 |
| CA信任 | 非空绝对 `issuer_ca_file` 指向本轮隔离CA；系统信任库/certifi未改；原HTTPS强制、trust_env=False、重定向拒绝保持。 |
| 首人物/会话回填 | 平台签发的真实来源最初person/conversation均null；Memory真实HTTP响应给出person，Core真实HTTP ingest响应给出conversation。平台 `prepare_mapping/confirm_mapping` 仍由受信同进程适配器调用，不是新增共享HTTP wire。 |
| 来源状态 | 产品工厂 `source_authority=None`，没有SourceAuthority实例或LocalFixtureSources。健康/零预算/完整预算/修订/遗忘/consume继续503。 |
| 外部模型与渠道 | 仅保留原录制设施，**本次模型0次、渠道0次**；无实际QQ/TG登录、外部模型或生产访问。 |

## 实际验证

`python -B scripts/integration/ts050.py run` 现在只发现 `test_ts050_tls*.py`，结果写到独立 `.runtime/ts050-tls/`。

| 用例 | 结果及证据 |
| --- | --- |
| 真实HTTPS首身份/回填/W0 | 通过；Core请求期间观测到至少两次来自产品的Memory resolver HTTPS成功请求，实际receiver=memory/caller=companion。重复入站同receipt、inbox=1。Memory gate后Core failed，scope_version=null、blocked_scope/attempts=0、model_calls=0、replies=0。 |
| CA拒绝与恢复 | 默认根不信任隔离CA、错误CA、false配置均503；三种拒绝均没有平台HTTP resolver请求。恢复可信CA后同产品工厂register首次created=true、resolve返回同person；两次实际HTTPS成功。false是配置前置拒绝，不误写成已完成TLS握手。 |
| 身份/用途/撤销 | API错误凭据401且无resolver请求；错误resolver凭据、错误接收方/调用方、issuer不符、actor无权、config来源冒充dialogue、未签发ref、账号不符均403。真实平台时钟故障注入+301秒模拟来源到期后403，恢复时钟身份仍可解析；真实entry撤销后Memory与Core均403，Core inbox=0。 |
| None来源仍拒绝 | 0/0和2048/8192选择请求先完成真实HTTPS来源解析，再503且无selected_units；correct/forget/consume均503，无候选回执，health仍unconfigured。consume输入仅为schema-valid负向probe，未称其为权威Core committed_event。 |
| 快照正常对照 | 独立临时Git仓库的已提交blob和唯一精确 `.ts050-commit` marker通过。 |
| 快照额外文件拒绝 | 注入sitecustomize.py、pyc、pyd、so、pth、zip、仿marker的py和额外txt，共8个反例，均在导入前被拒绝；不尝试执行这些文件。 |
| 快照内容/缺失/marker | 已提交文件改值、文件缺失、marker不符均拒绝。新守卫还拒绝未登记目录、符号链接和junction。 |

共**4项真实网络组合场景+3项快照守卫测试**。30次实际平台resolver HTTP请求均为HTTPS（包含测试调用的scope核对；不把全部30次都归为Memory产品发起）。35次监听关闭检查全部通过，无服务残留；临时数据库/证书/凭据及快照测试Git仓库已清理。仅一次asyncio setup耗时诊断，不是错误；未出现关闭超时。

完整diff审查后，ruff check/format（`--no-cache`）、5个Python文件AST通过，然后一次运行上述7项。运行器对四产品runtime依赖约束检查通过；复用原隔离venv的38包，未重新安装或修改产品锁。Python3.12.14、httpx0.28.1、fastapi0.135.1、uvicorn0.42.0、aiohttp3.14.1；全部版本在新轨迹。没有重复Memory112项产品套件或原8项全量集成。

## 重现与剩余工作

在TS-050分配工作树、已有原venv中：

```powershell
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py prepare
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py run
```

首次新环境可用Python3.12+执行prepare，在 `.runtime/ts050-tls/venv` 创建venv，再用uv安装本切片生成的 `.runtime/ts050-tls/requirements.txt`，缓存指定 `.runtime/ts050-tls/uv-cache`。实际命令、unittest原始输出、四条合成场景记录及环境信息分别保存在脚本、`.runtime/ts050-tls/unittest.txt`、`results/`、`environment.json`。原 `.runtime/ts050` 及原四份证据没有覆写。

`verify_snapshots` 现在只允许Git树内文件和精确 `.ts050-commit`。运行器在设置子进程PYTHONPATH和导入产品前检查目录清单。另用新守卫只读复核原dc357e2快照通过，原现场也仅有该marker；**本次是守卫加固，不是发现旧证据混入额外模块**。

仍需协调SourceAuthority的真实source/receipt/current revision、Memory自有来源同步/版本失效、Core turn事实、确认与候选应用接点；详见历史 [最小接点提案](TS-050-next-ports.md) 中来源部分及Memory已集成TS-032候选。TLS缺口在本切片已解除，其余内容不因此变为已实现。真实召回、普通名字prompt、T2生成重叠、有序发送、渠道unknown和候选去重仍未联合通过；完整L0继续partial，不放行下游。
