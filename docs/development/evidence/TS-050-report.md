# TS-050 本地文字链联合验证：partial

2026-09-14。**8 个集成场景的预期断言通过，完整 L0 未通过；L1 未运行。** 真实 Platform、Companion、Memory、Gateway 共同参与，外部模型为隔离录制服务器；没有用字典来源或 LocalFixtureSources 放行记忆。

最终命令 `python -B scripts/integration/ts050.py run`：**8/8，20.860 秒，退出码 0**。静态/格式/AST、环境依赖一致性与 diff 检查通过。没有运行无关产品全套测试。详细数据见 [机器轨迹](TS-050-trace.json)，接点与限制见 [接线矩阵](TS-050-wiring.md) 和 [后续最小接点](TS-050-next-ports.md)。这些“通过”证明正向子链和预期拒绝行为，不把拒绝测试算作用户对话成功。

## 实际测试基线

| 产品 | 本次实际加载提交 |
| --- | --- |
| Companion | `812019e287a5bf37d9b5d810a028ffea828b872e` |
| Memory（本人首切片） | `358e4a664e14eee734494646f3aa3a0bc666a222` |
| Model Gateway | `b3b101faf3902f05d80818b39fe7c91367865d4e` |
| Platform | `a5ee59ff67a2de7a7e0d8328ea3c0f43e1d6a209` |

合同 text-dialogue/v1 1.0.0，manifest UTF-8/LF SHA256：`81e6cc4ddef7c6f82e055d4cb04b090db036dd5c52763473ce697aa02db478a1`。各产品自己的合同加载器同时校验发布文件。

启动时 Memory 协调 HEAD 已前移到 `dbf5c196dbb6de0d8828c0466e824efb5c7d08ce`；协调者明确允许继续固定旧切片。脚本核验固定提交是协调 main 的祖先、协调检出干净、快照每个 Git blob 未变化，实际导入的仍是旧提交。**没有使用 TS-031 画像、TS-032 未集成代码或任何 worker 可变目录。**

## 覆盖状态

| 需求/场景 | 结果 | 实际证据与边界 |
| --- | --- | --- |
| 首账号、会话回填 | 通过（方案 B 应用端口组合） | 平台真正签发；最初 person/conversation 均 null；Memory HTTP register/resolve 返回真实人物；Core HTTP ingest 返回真实会话；prepare/confirm 只消费这些实际响应。重复入站同 receipt、1 条 inbox。Memory 的 HTTPS resolver 未被该组合覆盖。 |
| W0 | 通过收件/封存及失败关闭 | immediate_submit，Memory select 503 后 turn failed，scope_version=null，outbox=blocked_scope/attempts=0；没有伪造合法 committed_event。 |
| 连续短句、W5 | 通过收集边界 | “我叫”“小明”组成同一完整组；旧 timer 不封存；deadline 前 1ms 无 turn，精确边界封存。使用 Core 公开 clock 注入的虚拟时间，不是实测五秒延迟/SLA。 |
| T2 重叠、T3 等待 | partial | 延迟真实 Memory HTTP 请求（只延迟、不替换响应）时，状态 preparing/preparing/queued；释放后真实 503，全部失败关闭。证明两个准备槽和 T3 容量等待，**不证明 T2 模型生成重叠**。 |
| 普通名字续问 | blocked | 真实第一组已持久保存，续问入站可受理；第一轮在 Memory gate 停止，未产生可验证的模型 prompt/已送回复。不得算成名字续问成功。 |
| 本人完整最小记忆 | blocked | 2048 tokens / 8192 bytes 请求实际返回 503，无 selected_units。没有装填 fixture 来源或记忆记录。 |
| 零预算 | 通过拒绝边界，正向 blocked | 0/0 仍返回真实 503，不以“无需召回”绕过 SourceAuthority。 |
| 旧修订/墓碑 | partial | 平台真实 observe/edit/retract 后旧 source probe 返回 scope_changed；archived probe 返回 503。Memory 旧记忆失效链没有来源接点，因此未覆盖。 |
| 权限撤销 | 通过入站边界，记忆失效 blocked | 平台撤销真实 entry 后 Core ingest 403，inbox 保持 3 条；无模型和发送。没有证明撤权已经同步到旧 Memory 证据。 |
| 修订/遗忘 | blocked | correct/forget schema-valid 请求实际 503，无来源权威/确认入口。未声称成功删除/修改旧记忆。 |
| 平台配置、原生转发 | 通过真实 HTTP 子链 | 真平台发布版本 7；原始请求和响应字节逐一相等，SHA256/字节数记录；真实 Companion Gateway 客户端经 HTTPS 调用网关，缺省 model 仅按真实 binding 补齐；版本 8 发布后旧 7 仍固定可用，撤销 7 与用 dialogue 来源访问 config 均拒绝、不增加上游次数。 |
| unknown 不重放 | 通过网关；渠道 blocked | 录制上游收到请求后断连，网关真实回执 unknown；同 request 重放409；独立 CLI 进程重新打开同数据库后回执相同、重放仍409，上游总数1。没有用它冒充 Core/QQ/TG 发送 unknown。 |
| 重启恢复 | partial | Core ASGI 完整关闭/重新创建、SQLite owner 重开后，收集组和失败/blocked_scope 保持；网关有独立进程重启。Core 进程崩溃/在途发送恢复未覆盖。 |
| 有序发送 | blocked | Core 在记忆 gate 前停止。渠道录制接收器没有收到请求，不手工填 replies/turns 表来制造发送事实。 |
| 提交候选去重 | blocked | 同一 schema-valid 诊断事件两次 HTTP consume 都503、无 candidate_job_ref。该输入明确不是 Core 正式 committed_event，未验证正向候选重复消费或落库去重。 |
| 来源真实性 | partial | 平台真实 current_sources 检查成功仍保留 archive_verified=false / scope_version_verified=false；特意使用 scope_version=913、input_revision=77 的诊断 probe 说明它不证明 owner facts。结果从未转为 Memory 成功授权。 |

## 调用、传输与清理

- 录制模型共 **4 次**：字节保真1、Companion Gateway 客户端1、发布新版后的旧版本请求1、unknown断连1。没有外部模型连接；上述客户端测试不是调度器主生成。
- 所有真实 Core 调度场景 **model_calls=0、channel_http_calls=0、replies=0**。失败前无法取得权威 scope_version，blocked_scope 没有对 Memory 发布或自动修复。
- 临时 CA/server certificate 只在任务 `.runtime` 中；Core 通过公开 transport 注入使用真实 HTTPS 和证书验证。平台→网关配置/网关→模型用明确登记的 loopback HTTP；没有关闭 TLS 验证、修改系统 CA/certifi 或依赖 monkeypatch。
- 70 次测试监听关闭检查全部通过；实际网关子进程已停止。每场景临时数据库、服务配置和证书在清理后删除；仅保留受控源码快照、隔离虚拟环境、无凭据的结果。合成服务凭据只放临时环境/配置，不进 Git。
- 首次8项运行已通过，但 uvicorn 因测试保留的空闲连接记录了关闭等待日志。把测试客户端显式设为不保留空闲连接后重跑共享集成范围，最终无关闭超时；仅有一次 asyncio setup 慢任务诊断，不是失败。初始版本检查及 Git archive 的 Windows CRLF 校验拦截发生于测试执行前；改为固定祖先校验及 `core.autocrlf=false` 导出后逐 blob 一致。

## 重现命令

在 **TS-050 分配工作树**执行。首次准备使用 Python 3.12+（本次为 Codex 捆绑 Python 3.12.14）和已安装 uv。全部写入都留在该工作树：

```powershell
$ts050Python = 'C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $ts050Python -B scripts/integration/ts050.py prepare
uv venv .runtime/ts050/venv --python $ts050Python --cache-dir .runtime/ts050/uv-cache
uv pip install --python .runtime/ts050/venv/Scripts/python.exe --cache-dir .runtime/ts050/uv-cache -r .runtime/ts050/requirements.txt
uv pip check --python .runtime/ts050/venv/Scripts/python.exe
& .runtime/ts050/venv/Scripts/python.exe -B -m ruff check --no-cache --select E4,E7,E9,F,I scripts/integration tests/integration
& .runtime/ts050/venv/Scripts/python.exe -B -m ruff format --no-cache --check --line-length 100 scripts/integration tests/integration
& .runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py run
```

已有环境和快照时只运行最后一条。结果在 `.runtime/ts050/results/test_*.json` 和 `.runtime/ts050/environment.json`；本次结果的汇编副本为 `TS-050-trace.json`。没有安装产品 editable 包；导入路径只指向指定提交的 Git 导出。

运行依赖取 Companion 与 Platform 已锁版本的相容并集，启动时检查四产品所有 runtime manifest 约束；Memory 使用其允许范围内的版本，**没有宣称复现其独立 uv.lock**。本次 fastapi 0.135.1、uvicorn 0.42.0、httpx 0.28.1、aiohttp 3.14.1、jsonschema 4.26.0；ruff 0.15.7 是集成工具版本，cryptography 50.0.1 仅用于临时证书。所有38个包的具体版本与 PyPI 来源记录在机器轨迹，未改变产品依赖锁。
