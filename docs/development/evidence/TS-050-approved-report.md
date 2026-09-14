# TS-050 新查询恢复与真实用户批准验收

2026-09-14。**本轮指定的本地L0缺口已有通过证据，待协调复核合入；未自行标done。** 首次4/4通过54.641秒；随后补充“撤销共享后原私密源变化不推进公开/B epoch”，仅画像1/1通过19.003秒。最终是4个场景、5次执行，其他三个场景没有重跑。

所有旧成功、协调W5失败、受控诊断与被动观察保持各自历史事实。f6354b6前13份证据逐Git字节未改；原W5具体底层异常未记录，其根因仍是高置信推断，不因新修复通过而改写。

## 本轮固定输入

| 产品 | 实际测试提交 |
| --- | --- |
| Core | `4f22c031deb2d193ca66c73ef7c5a41da3872fc6` |
| Memory | `575941ee12e2a7d6dbd3d3374e34866eb82006fb` |
| Platform | `a94d34534ba0b6002bdcdab9db1d5dd899a06a16` |
| Gateway | `b3b101faf3902f05d80818b39fe7c91367865d4e` |

source-sync/text-dialogue/profile-memory均为原正式1.0.0，source manifest SHA256为 `178d0ce66210bdfad4cfb85d8b5f0905b0b67f834e2a530efe5636ff0373633d`。完整三包哈希、pins及38包环境版本在 [新轨迹](TS-050-approved-trace.json)。独立目录 `.runtime/ts050-approved`；固定Git导出前后核对blob及额外文件白名单。平台资产提交f4dac458不在本轮，HEAD前移不改变a94d345测试pin。

Core/Memory原工厂、Platform input/current/inline确认、Memory SourceAuthority/TrustedWorkflow均真实。两个用户操作场景在服务启动前通过产品CLI `migrate-users`建立schema3/local_users_schema=1及新备份。无editable安装，子进程执行console-script的实际目标 `tianshu_memory.cli:main`，经过原argparse和完整操作文件，不写测试SQL代替迁移。

local_users只在实际身份/会话映射后登记精确actor/scope/owner或curator权限；使用独立随机用户凭据，服务token与平台context不当作内容批准。每次完整操作是明确合成本地用户执行。本次owner/curator为不同凭据/角色登记、映射同一合成账号，实际走不同授权分支；未额外声称跨成员整理已联合验证。

## 完整本地L0当前矩阵

“沿用”指已获协调9项成功证据或未受影响的历史子链，未泛化重跑；变更接点由本次验证覆盖。完整矩阵是否释放依赖由协调复核后决定。

| 项目 | 当前证据与状态 |
| --- | --- |
| 首账号W0、来源/身份/会话回填、真实SourceAuthority | 沿用source-sync真实链成功；本轮每个场景继续经过真实输入登记、inline回填、C1/P1/C2、schema3和精确event_scopes。 |
| 完整记忆组/零预算/真实Core召回 | 沿用独立完整组与Core模型prompt召回成功；本轮真实来源屏障/候选提交持续使用，无替身权威。 |
| 群私A/B同P、独立receipt/候选、编辑撤回及不可复活 | 沿用协调成功；本次新增A遗忘不删B与更高source不清除suppression。 |
| W5/普通名字续问 | **新Core正向通过**：两轮phase/delivery均sent，各model_calls=1；实际名字prompt和下行“你叫小明。”。本次自然W5未发生传输异常，重试分支另有受控用例。 |
| idle竞态/单次查询重试/两槽 | **新Core受控通过**：保留5秒keepalive，取出连接后暂停5.2秒；RemoteProtocolError后相同request_id和body SHA仅重试一次，新TCP/TLS200，Memory目标请求只抵达一次。B查询在A失败前已在途，到A恢复后才完成，共享客户端未关闭。三轮各一次模型/下行。 |
| 写/模型/发送不通用重放 | 本轮确认不在查询白名单、模型/发送无重复；更广写路径故障边界沿用TS023组件108项/95子场景，不声称本轮重跑了全部写故障。 |
| 两槽/T3、有序发送、A方案-B插话-A续接 | 沿用协调成功；本轮补共享客户端重试不破坏另一在途槽。 |
| unknown/重启/owner水位/Memory guard拒绝 | 沿用协调成功，未因新指令泛化重复。 |
| 真实用户确认→HTTP forget | **新入口通过**：独立用户凭据+CLI完整确认，现有HTTP revise返回tombstoned。同幂等重放取原结果，改key重用消费确认403。 |
| 遗忘持久与A/B隔离 | **通过**：Memory/Core owner重启前后A为空、B原record可读；旧待提炼候选拒绝，旧已提交job回执未激活记录。更高物理修订获真实remote allowed后A仍为空。后续物理edit对B的全局失效与A遗忘分开记录。 |
| owner/curator批准→发布 | **新入口通过**：owner CLI批准public兴趣、LocalUserApplication发布；curator应用批准群topic、CLI发布；发布幂等。错误用户/服务凭据401，仅owner权限批准群topic403。无True adapter。 |
| Core实际画像prompt/双域发送检查 | **通过**：prompt含owner兴趣和curator话题，来源仅shareable_projection；profile版本3与text版本1独立。模型在途时真实CLI撤销画像，返回后Core scope_changed、model_calls=1、无下行，正确拦下旧画像生成内容。 |
| 画像撤销/源撤回 | **通过**：owner撤销后兴趣消失、群话题保留；原群源retract后话题消失；旧发布均不能复活。 |
| 私密epoch隔离 | **通过**：无关私密遗忘不推进有效共享epoch（3→3）或B（1→1）；已撤销public画像后再次改原私密源，公开仍3→3，B仍1→1。 |

## 数量与验证批次

最终选取结果共 **16模型请求、15发送请求**；差额1是画像撤销后正确拦截的已生成回答，不能把它算作回复链故障。15个发送均有录制sent回执。**1,125条**owner HTTP均TLS；**26次**监听关闭检查无失败，Gateway子进程全部停止。2次实际migrate-users CLI，8个成功完整用户操作：确认2、批准2、发布3（含幂等）、撤销1，另有拒绝对照。

初始四项在补充画像边界前已原字节归档到 `.runtime/ts050-approved/diagnostics/initial-four` 并保存SHA。汇编只更新画像场景，另三项逐文件核对原SHA；不会把新旧不同pins或环境混为一次通过。代码稳定后集中diff/ruff/格式（无缓存）/AST及对应范围验证；未重跑旧网关/恢复九场景、产品全套或因平台HEAD前移重复成功检查。首次离线汇编仅遇Windows路径分隔符KeyError，归一化后只重跑汇编，未重跑应用。

## 重现与证据

```powershell
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py prepare
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py run
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/collect_approval_evidence.py
```

run当前只发现 `test_ts050_acceptance*.py`；本次补验为 `run --pattern test_ts050_acceptance_profiles.py`。首次环境用Python3.12+和生成的requirements建立本切片venv，uv cache限定本切片目录；本次复用原38包环境，产品锁未改。原始结果/日志、初始归档与环境信息在 `.runtime/ts050-approved`；Git汇编约1MB，保留完整用户操作、核心状态/模型请求、HTTP时间线及原结果SHA。

请求origin、批准ref、确认ref以SHA替代保留关联；HTTP SHA指脱敏前原字节。服务/用户凭据、私钥、临时绝对路径未发布。两个真实用户角色仍是测试中的合成外部操作者；模型/渠道是明确的录制替身，入口/批准/存储/HTTP是真实产品。这符合本轮本地L0范围，不等于真实账号/L1。

## 当前仍未通过或未纳入的项目

- **本轮指定本地L0功能项没有已知剩余失败项；新切片尚待协调复核/合入，不能自行宣告整系统完成。**
- 本次未追加HTTP correct的正向语义重建、跨成员curator、硬进程崩溃/断电等扩展联合场景；不能把既有组件证据说成这些都已联合通过。是否新增为后续本地门槛由协调决定。
- 真实QQ/TG或网页登录账号、真实外部模型、真实SDK回执、归档/生产/PostgreSQL等属于另外接入或L1/生产验证，**不混作当前本地L0仍失败的理由**。

任务板、完整矩阵验收和释放依赖由协调者管理。本任务不推送、合并、部署或启动下游。
