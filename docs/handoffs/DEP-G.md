# DEP-G：Linux 新装引导与镜像验证入口

目标：落实第四批精确卡的 Linux 新合成安装入口、四产品运行身份、I/J 消费边界。
仅本地实现交付，待协调验收；**没有 Linux/Docker/NAS 通过结论**。

## R1 返修交付（优先于下方初次交付记录）

- 返修基线 `40798738c64d34dd916840b0068c3e164b156881`，最终 SHA 由交付消息固定。
  下方61项测试与旧日志来源属于初次交付历史，不作为 R1 结果；旧 DEP-G-local.json 保持原字节。
- 修复协调独立注入发现的停止归属问题：先 create 登记实际 ID/镜像/owner/名称/Compose
  来源，再按 ID 启动；停止前和每次信号前核对完整集合，拒绝陌生 ID、替换、重复 owner
  或身份变化。核心仍使用原 compose.json，schema 保持 dep-g-runtime/1.0.0。
- 停止前关闭 restart 并读回确认；要求退出0、无 OOM、无重启，终态连续稳定3秒。
  无强杀回退；部分 create 保持不确定，部分 start 只处理已登记 ID。
- 一次性容器先登记、不用自动删除；正常稳定退出后先持久化完整身份和退出证据，
  再精确 docker rm ID（无强制/删卷）。成功只留四个核心容器；异常/超时保留现场且整次失败。
- 按协调新接受版本，日志来源窄重绑至 `65b88a6d1c2b5047ca6bfb2f7f7484749eb14154`
  （协调根已合入 4f3f7231）；四产品和合同不变。
- 实际验证：本任务私有 venv 全量 packaging **71项通过、0 skip**；Ruff 与 diff 空白检查通过。
  故障注入含同标签重复 owner、替换 ID、镜像/owner 改变、restart 更新失效、短暂退出后重启、
  退出2/143/137、OOM、部分 create/start、一次性超时和正常精确删除。全部 Docker 测试是替身。
- 固定 Git 日志导出、实际 initialize/configure、合成 TLS 握手、五写卷、独立项目、完整性、
  preflight 与篡改挂载拒绝通过。对账公开 CLI 使用合成 HTTPS 查询替身，完整/缺失样例均符合预期。
- 新证据：`tests/deployment/packaging/evidence/DEP-G-R1-local.json` 和
  `tests/deployment/packaging/evidence/DEP-G-R1-combination.json`。
- 未执行：真实 Linux 镜像构建/容器生命周期/权限/flock/四服务对话、真实 Loki、恢复、NAS。
  下一步：协调审查此固定提交后在明确授权的全新 Linux 合成 scope 验证；I/J 仍独立验身份、
  正常停写和额外挂载，不以本地替身结果替代运行证据。未改产品/合同/根任务板，未合并或推送。

## 基线与版本

- 根基线 `eced6de3ed0e2292d8c767d9f37fea36d0e04476`，本任务分支 `codex/dep-g-linux-bootstrap`。
- 接口小提交：`ffcb573d9d9866b83491b08eab7c61e4f7ec6fcf`、
  `880c2c33ffc4c12b9267d38267c2dacde074890d`；镜像/容器区分补充
  `ceca645fbc60b68a28eb1fa4e942ecf8bab3ea1b`。均已通知协调转 I/J。
- 最终实现提交是包含本交接的分支 HEAD，完整 SHA 在交付消息中固定；交付后停止写入。
- 按协调验收后的正式版本重绑：platform `b98c8a2a09279125df08bd48f3fab3422f3de165`，
  gateway `601974194042641c5a85cc3c061cbd1880d7daf1`；companion
  `e94b609099365f75ca933d9fed03cdfbc83ec235`、memory
  `9a3b2bed6aebff9f0677f2c62e979859769e0c9c` 不变。
- 根合同只读协调根原字节，核清单全部文件后复制私有 `.work/contracts`，续期 manifest
  `c5017724187c1386b647fcc5b41ab3cb1702f27d6192f3a87fcefb23e7a5a61c` 不变。
- observability 保持既有正式 `668d69e2c52ea55d9b1f102426196a5286c57bc5`；未把尚在返修的
  DEP-I 345cdea 当接受版本。待其协调集成后另作窄版本重绑。

## 实现

1. `synthetic_init.py` 默认 plan；显式 execute 创建全新 scope/inputs 和
   scope/deployments/source，随机独立凭据、临时 TLS，不启动 Docker。日志 TLS 覆盖
   logs.internal、各 obs 服务名、127.0.0.1；另有客户端证书，均只用于合成 scope。
2. `linux_validate.py` 默认 plan，执行器拆到 linux_runtime/linux_bootstrap。
   仅本机 Linux Unix socket，并固定已核 endpoint；独占 G/I/J 共同 flock，拒绝已有项目、
   旧状态/日志、重复 attempt、外部模型目标和已有镜像 tag。源码从固定 Git 导出，
   平台原 Git 与注入合同分开列入 inventory；运行工具本身也须与包内原字节相同。
3. 镜像串行构建，四产品与已配置五日志镜像逐个 inspect，local image ID 与实际
   RepoDigests 分离，不拿 local ID 填 release digest。日志镜像只读 inspect，不拉取/启动。
   产品 installed interpreter 的依赖检查支持 Memory/Gateway 无 pip venv；不使用宿主测试环境。
4. 新平台容器公开 CLI 一次性授权：无模型 liveness 只 issue（合同不允许空 provider 发布）；
   显式 synthetic-dialogue 才注册专用模型并 publish/issue。返回来源格式/期限验证后只注入
   gateway.env；管理凭据不发给网关，ref/凭据不进入报告，无失败自动重签/重放。
   配置、被测 candidate 及日志兼容投影只按明确 allowlist 更新摘要；中断保持 INCOMPLETE。
5. 真实 Memory CLI 首装迁移、四容器 liveness、实际 UID/GID、匿名拒绝及鉴权 readiness；
   可选网关容器内 TLS 合成模型及真实 Web 登录/CSRF/发送/回执核对。默认清单网页仍关闭。
6. 仅向本次项目、工作目录及服务归属均吻合的容器发送 SIGTERM；不 down/prune/删卷/强杀。
   超时、不明结果、容器消失或异常退出拒绝绿色；保留现场与原始合成应用日志。
7. `runtime-identity.schema.json`/example/生成器输出双项目、完整 Compose JSON及两种摘要、
   13 挂载与九 owner、源码commit、配置/工具hash。镜像已inspect时容器字段仍可null。
   authority/recovery_inventory 显式null，由 J 独立 prepare 证明，G 不猜造。

## 实际验证

独立本任务 `.work/venv`（Python 3.12），未使用其他工作者 venv。
去敏可审阅证据：`tests/deployment/packaging/evidence/DEP-G-local.json`。

- `python -m unittest discover -s tests/deployment/packaging -p 'test*.py' -v`：
  **最终 61 项通过，0 skip**，含21个 DEP-G 测试。Docker 接线测试使用明确替身，
  覆盖 liveness/合成对话/签发失败、停止归属、期限、不重试、远程context、镜像语义、
  工具版本不匹配、core_only 不得削弱 release 检查，不能当容器证据。
- 对新增/修改 Python 运行 Ruff，全部通过；完整 staged diff 与 `git diff --check` 审查无问题。
- 按新正式清单导出四产品 Git 原始快照与原字节合同，成功；原 Git 输入与平台合同注入
  分列。用于安装的平台另复制一份，避免 pip 产生的构建文件污染被测 context。
- 在本任务新 venv 正式安装 platform wheel，`pip check` 通过；安装后42个平台 Python 源文件
  与 b98c8a2a 固定快照逐字节相同。使用 `verify_platform_bootstrap.py --contracts <私有合同>
  --platform-source <固定contexts/platform> --output <新报告>` 实际跑公开 CLI：
  liveness issue / synthetic publish+issue 两场景通过，新库由产品自行初始化，私有ref注入成功。
  没有 SQL 造授权。此项是 Windows 已安装平台 CLI，不是 Linux 容器。
- 实际 `synthetic_init.py --execute` 生成全新合成 scope；Linux 校验默认 plan 返回 not_run。
  本机尝试显式执行返回 `linux_host_required`，全维度 not_run，未触发 Docker/业务副作用。

## 未执行、风险与下一步

- 本机无 Docker/WSL，尚未执行 Linux 构建、实际镜像inspect/依赖、挂载UID/GID、flock竞争、
  Compose启动/停止、Linux鉴权ready、Linux四服务合成对话。不可将mock测试或Windows CLI填为通过。
- 日志全链、九 owner 正常恢复、NAS、真实模型、浏览器均未执行；release_ready仍false，
  所有应用镜像digest仍null。默认旧verification.json原字节保留，不能当新版证据。
- 入口要求Python3.12及独立venv；NAS若只有旧Python，先由协调准备隔离运行时，不安装宿主全局包。
  目录权限调整、可用端口/网段确认及设备执行均由协调负责，本任务无SSH/设备写入。
- I需要完整日志包先配置、五obs镜像在本机、五写卷全新为空；G不启动obs、不清core日志。
  G可保留启动日志供 I 做完整基线对账，但 I仍须独立核四应用已正常停写才注入fixture。
  J须核九owner并prepare真实authority/inventory，恢复后继续disabled；G stopped不替代恢复许可。
- 请协调先验此固定交付，再按 `deploy/tianshu/LINUX-VALIDATION.md` 的显式新scope命令进行
  Linux验证。若实际容器发现公开产品入口/正常停止缺陷，需回报具体产品负责人，不绕过门禁。

未改根任务板/合同/产品协调检出/其他工作者目录；未合并、推送、发布，也未开启其他窗口或子代理。
