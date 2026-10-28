# TS-387 原生自主生活验收工具

本子任务基线为 R14 根 `b930ef638b95b8d363fc889de8527bbf711987a9`，分支 `codex/al-native-acceptance-20261028`。仅增加 `tests/deployment/single_container/native_autonomous_check.py`、同前缀 `native_autonomous_fixtures.py` 和本交接；产品、共享合同、队列表、生产配置及数据均未修改。产品候选由父级安装到以 R14 不可变镜像为基础的新隔离镜像后执行。

工具复用原 `native_host_smoke.prepare/installed_wheels`、`native_memory_check.configure/Recorder/json_call`、`native_schedule_check.configure/seed_platform/wait_for/storage_bindings/Provider`。冻结 Platform 与 Memory 的 text-dialogue 合同分别传入，复制夹具时不重写 contracts。没有产品源码 overlay 选项；启动和结束均检查全部已导入产品模块属于当前安装 venv，检查六个实际 owner 线程及其原生请求所属线程。

验收主链不发送任何用户消息：原生 Life 后台选择目标 → 实际安装的 **builtin content-discovery** 插件 worker → 原 `content.discovery/http.client` 通过 loopback SearXNG `/search` 发现候选 → 原 AL07 capability receipt → Companion registry 从真实返回选择 `read_locator` → Knowledge 获取真实网页 → 读取 64 字符的有限区间 → 原 actor outbox 送达独立 Memory episode → recall 取回精确 episode revisions → 两次原 `append_derived` 保存 understanding/preference → 完成实际活动和关切 → 后继目标的实际模型输入包含这两项派生材料。

搜索替身只返回标题和 snippet；实际正文在单独 `/article`，模型不得从搜索摘要获得全文。录制 provider 走原 Gateway 流式 SSE / tool-call 路径。全天阅读 routine 只通过既有 `configure_actor` 设置在合成角色上，避免验收时间落在睡眠时段；未修改时钟或产品的睡眠/手动日程规则。角色确认沿用 Platform 既有 `seed_roles` 合成注册 helper，因此不宣称网页管理或真实角色注册流程已验。

另经六 owner 原端口调用 Knowledge 上传/acquire/read，检查 VTT 的 30 秒有界区间、未读 cue 与续读；检查含真实音轨的 MP4 返回实际 `audio_clip/video_frame`、帧数上界、原件及输出 bytes 的摘要。此部分是原解码路径验收，不把这些直接上传当作 Companion 已发生的自身经历，不宣称所有媒体目录适配或外部账号动作已贯通。

## 执行

在安装完整候选 wheel 的新隔离 Linux 环境运行。下列路径为显式验证夹具目录，不挂载生产目录；脚本及同目录 fixture helper 必须同时提供。`--output` 必须是尚不存在的目录。

```sh
/opt/tianshu/venv/bin/python -B /validation/tools/native_autonomous_check.py \
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
