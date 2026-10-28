# TS-387 原生自主生活验收工具

本子任务基线为 R14 根 `b930ef638b95b8d363fc889de8527bbf711987a9`，分支 `codex/al-native-acceptance-20261028`。仅增加 `tests/deployment/single_container/native_autonomous_check.py`、同前缀 `native_autonomous_fixtures.py` 和本交接；产品、共享合同、队列表、生产配置及数据均未修改。产品候选由父级安装到以 R14 不可变镜像为基础的新隔离镜像后执行。

工具复用原 `native_host_smoke.prepare/installed_wheels`、`native_memory_check.configure/Recorder/json_call`、`native_schedule_check.configure/seed_platform/wait_for/storage_bindings/Provider`。冻结 Platform 与 Memory 的 text-dialogue 合同分别传入，复制夹具时不重写 contracts。没有产品源码 overlay 选项；启动和结束均检查全部已导入产品模块属于当前安装 venv，检查六个实际 owner 线程及其原生请求所属线程。

验收主链不发送任何用户消息：原生 Life 后台选择目标 → 实际安装的 **builtin content-discovery** 插件 worker → 原 `content.discovery/http.client` 通过 loopback SearXNG `/search` 发现候选 → 原 AL07 capability receipt → Companion registry 从真实返回选择 `read_locator` → Knowledge 获取真实网页 → 读取 64 字符的有限区间 → 原 actor outbox 送达独立 Memory episode → recall 取回精确 episode revisions → 两次原 `append_derived` 保存 understanding/preference → 完成实际活动和关切 → 后继目标的实际模型输入包含这两项派生材料。

搜索替身只返回标题和 snippet；实际正文在单独 `/article`，模型不得从搜索摘要获得全文。录制 provider 走原 Gateway 流式 SSE / tool-call 路径。全天阅读 routine 只通过既有 `configure_actor` 设置在合成角色上，避免验收时间落在睡眠时段；未修改时钟或产品的睡眠/手动日程规则。角色确认沿用 Platform 既有 `seed_roles` 合成注册 helper，因此不宣称网页管理或真实角色注册流程已验。

另经六 owner 原端口调用 Knowledge 上传/acquire/read，检查 VTT 的 30 秒有界区间、未读 cue 与续读；检查含真实音轨的 MP4 返回实际 `audio_clip/video_frame`、帧数上界、原件及输出 bytes 的摘要。此部分是原解码路径验收，不把这些直接上传当作 Companion 已发生的自身经历，不宣称所有媒体目录适配或外部账号动作已贯通。

## 执行

在安装完整候选 wheel 的新隔离 Linux 环境运行。下列路径为显式验证夹具目录，不挂载生产目录；脚本及同目录 fixture helper 必须同时提供。`--output` 必须是尚不存在的目录。

```sh
/opt/tianshu-venv/bin/python -B /validation/tools/native_autonomous_check.py \
  --host-source /opt/tianshu/application \
  --platform-fixtures /validation/platform/tests/backend \
  --contracts /verification/contracts/text-dialogue/v1 \
  --memory-contracts /verification/memory-fixture/contracts/text-dialogue/v1 \
  --diagnostics-contracts /verification/contracts/diagnostics/v1 \
  --memory-fixture /verification/memory-fixture \
  --output /validation/results/autonomous-sqlite
```

PG 使用相同参数，换一个全新 `--output`，增加：

```sh
--postgres-url 'postgresql://synthetic_user@localhost/synthetic_al?host=/validation/pgsocket'
```

必须是独占可丢弃测试库和已存在的 Unix socket 目录；不能使用 `postgres/template*`。工具使用已有 helper 的随机 namespace，保留 PG schemas、SQLite、配置和 guard，既不 DROP 也不删除夹具。继承环境里的 `TIANSHU_POSTGRES_URL/TIANSHU_REDIS_URL/TIANSHU_STORAGE_NAMESPACE` 必须为空。新 actor 域通过既有 `ActorStore.initialize` 在开服前显式初始化，不在普通请求或启动中执行新 DDL。

结果文件：`native-autonomous-validation.json`、`autonomous-native-requests.json`、`autonomous-provider.json`、`autonomous-sources.json`。失败也保留文件和阶段；最终摘要记录 driver/fixture 两文件 SHA、安装来源、实际 backend、存储绑定及原始夹具库 hash。无 QQ 连接、付费模型或外部平台账号访问。

## 实际验证与限制

- Windows 本地：AST 语法、Ruff 静态检查和 `--help` 通过。初次未找到全局 `python`，已使用现有 Memory `.venv/Scripts/python.exe`；没有安装新依赖。
- 小型真实原件 fixture：本地 PyAV 19.0.1 生成 MP4 为 11085 bytes，重新打开确认同时有 video/audio；不依赖 numpy。
- **尚未执行候选 wheel 的六 owner SQLite 或 PG 验收**。当前本机该 venv 没有完整 Platform/Companion/NoneBot 发行依赖，不能用源代码补包冒称原生通过。父级应在候选稳定后统一运行；实际业务通过必须以结果 JSON `ok=true` 为准。
- 此工具不重复普通用户对话的提取/分段/学习/召回整链。保持并复用已存在的 `native_schedule_check.py`，本工具单独断言零用户 turn、无用户记忆 material/learning/context 请求。
- AL12 真实旁听 producer、跨日持续使用、异常进程强杀恢复、真实 QQ/网页登录和外部账号写操作均不在本次工具完成口径内。

## 原生 SQLite R4 夹具修正

父级实际运行已经走到真实阅读和独立 actor episode delivered，但录制 provider 第 5 轮错误地把 `complete` 理解成整件已完成，断言失败。Knowledge 字符读取的 `complete=true` 表示请求区间完整提供；R4 真实原件 total=141、实际 Reading coverage=[0,64)，整件仍未读完。本工具改为同时检查收到 64 字符、精确 [0,64) 覆盖、区间 complete=true/无 gaps、原件 total>64 和实际正文 marker，不削弱原件实读或未读区间边界。另将 builtin 的真实 `read_locator.kind=webpage` 与 acquire 的 `source.kind=url` 分开核验。

录制 provider 现在保存失败请求、已回传的真实 tool receipt 和异常类型/断言，主等待循环发现该失败立即结束，避免原 120 秒超时掩盖原始错误。R4 原失败证据由父级保留；此修正仍需新的原生运行，不能以静态检查冒称通过。

## AL12 独立原生工具

新增 `native_observer_check.py` 与 `native_observer_fixtures.py`。沿用相同参数、隔离和导入检查；改用 driver 路径，换全新 output 即可：

```sh
/opt/tianshu-venv/bin/python -B /validation/tools/native_observer_check.py \
  --host-source /opt/tianshu/application \
  --platform-fixtures /validation/platform/tests/backend \
  --contracts /verification/contracts/text-dialogue/v1 \
  --memory-contracts /verification/memory-fixture/contracts/text-dialogue/v1 \
  --diagnostics-contracts /verification/contracts/diagnostics/v1 \
  --memory-fixture /verification/memory-fixture \
  --output /validation/results/observer-sqlite
```

PG 与前述相同，显式加专用 Unix-socket URL；所有 `native_*.py` 一起提供。CLI 说明复用增加了可选 description 参数，原自主阅读 CLI 行为不变。

该工具额外注册独立 `companion_background` Gateway fixture 凭据与 background 分类，未借用前台凭据或修改产品。外部仅本地录制 provider 与反向 WS 合成 QQ 协议。真实 SDK 登录状态经 `/capabilities` 确认后，使用原 Platform observation enroll / policy 领域操作把群配置为 `observe + actor:a`，再发送一条合成群 WS 入站；原 SDK capture、Platform pump、Companion inbox/归档回调、Memory raw archive/source adapter、observer outbox、episode、reflection、下一目标全部必须真实运行。

验收不造 source_ref，不直调 append_episode，不造用户 turn；源描述符须由实际归档的原件及群 generation 得出。观察模型仅选实际 source facts，不能把旁听改称参与/实读或永久偏好。第一目标仅通过原 activity checkpoint 登记待查证问题，不宣称已经研究原件；之后独立目标明确 `observation_inspired=false`，检验前一 hint 已一次性消费、旧公共 topic 不自动覆盖新目标。

前后比较原用户记忆、material、segment、事实/记录、摘要、画像与 source 生命周期相关逻辑表的行/摘要，保持完全一致；原 raw observation/QQ 作者及别名登记单列允许，是原归档规则。断言零 user turn、零 user-learning、零假 reading、零回复 scope，WS 只允许 SDK `get_login_info`，任何 send/其他操作均失败。记录实际 Memory observer/reflection lineage 和受众，并要求精确群策略/generation 保持。

本地 Ruff 和 `--help` 通过；没有运行真实六 owner/SQLite/PG，没有 SSH 或产品修改。需最终 TS-386（含 hint 单次消费/规划召回修正）与 TS-388 的安装 wheel，再由父级统一实际执行。真实运行未通过前不宣称 AL12 原生通过。没有覆盖实际 QQ、网页管理、跨日、强杀、A→B→A 策略循环或外站账号。
