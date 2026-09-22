# DEP-J Linux 执行入口

默认只计划，所有执行必须显式 `--execute`。本机 Windows 无 Docker/WSL，本交付的 Linux 命令未执行。测试中的 Docker/HTTP double 不等于真实容器或公开产品功能验收。

## 固定输入与准备

消费 DEP-G `880c2c33ffc4c12b9267d38267c2dacde074890d` 的身份格式；后续 `ceca645fbc60b68a28eb1fa4e942ecf8bab3ea1b` 仅澄清 not_observed 可以已有镜像观测。私有接口副本及逐文件原字节 SHA256 在 `interfaces/dep-g-pin.json`；不导入其他工作者活动文件。release 仍是 1.1 五日志卷，image ID 与 registry digest 分开。报告里的 null 不被当作 authority 或恢复库存。

在获准 Linux 本地环境创建独立工具 venv，安装本目录 requirements.txt。不得给宿主全局安装包，不使用远程 Docker context。入口仅调用显式 Docker 可执行文件和 `unix:///var/run/docker.sock`。

```text
python3 -m venv /absolute/private/tool-venv
/absolute/private/tool-venv/bin/python -m pip install -r ops/recovery/requirements.txt
```

先用既有 `python -B -m ops.recovery --root <全新绝对scope> --scope-id <显式UUID> --execute init-sandbox` 创建 scope，再让 G 的正式打包/合成初始化入口直接输出到 `<scope>/deployments/source`。不搬动已有部署，不读真实业务数据，不把 scope 外目录重新标为合成。九个真实 owner 必须已由协调准备，G/I 输出完整 Compose 与镜像/挂载观测；若只有四产品而五日志容器未创建，会明确拒绝 `missing_project_container`。

原 .deployment-owner.lock、companion owner、迁移备份、完整产品 SQLite/sidecar/guard、五日志目录均保留。库存扫描只登记字节/路径，不写业务 SQL、不造批准/撤销/unknown。文件集变化导致旧库存拒绝，不能用“自动更新库存”掩盖版本漂移。

```text
python -B -m ops.recovery --root <scope> --scope-id <scope-uuid> linux-prepare --deployment-directory <scope>/deployments/source --runtime-identity <source>/reports/runtime-identity.json --runtime-identity-sha256 <另存的原字节hash> --authority-id <本次显式synthetic-authority-uuid>
python -B -m ops.recovery --root <scope> --scope-id <scope-uuid> --execute linux-prepare --deployment-directory <scope>/deployments/source --runtime-identity <source>/reports/runtime-identity.json --runtime-identity-sha256 <同hash> --authority-id <同uuid> --docker-executable /absolute/path/to/docker
```

prepare 不启动或停止容器。execute 核九实际容器、镜像架构/ID/用户/挂载、配置原字节、源版本和挂载 uid/gid；只在此前未登记的直属 source 创建 J 的 `.deployment.json`、`recovery-inventory.json`、`.lifecycle`、`.recovery-registration.json`。另存返回的 registration_sha256。G 原身份文件不改，登记只表示显式合成首装权威，不表示生产可信或已经正常停写。中断留下部分登记时不自动覆盖/重建，保留现场。

## 九 owner 停写→完整备份→新目录禁用恢复

```text
python -B -m ops.recovery --root <scope> --scope-id <scope-uuid> linux-rehearse --deployment-directory <source> --registration-sha256 <另存hash> --backup drill-backup-01 --target restored-01
python -B -m ops.recovery --root <scope> --scope-id <scope-uuid> --execute linux-rehearse --deployment-directory <source> --registration-sha256 <同hash> --backup drill-backup-01 --target restored-01 --timeout 300 --docker-executable /absolute/path/to/docker
```

全程独占 G/I/J 共用 `<source>/.runtime-owner.lock` 的同一 inode 非阻塞 flock；不嵌套重新持锁、不删锁。九 owner 身份在初次登记及操作时双重核验，防止同项目旧容器被替代。先禁 restart 并核读回，再逐 owner SIGTERM；只接受 exited/0、非 OOM/Dead/Error、无运行/重启/暂停。143/137、不确认退出、目录枚举异常均不发布完成备份，无强杀退路。

备份以 SQLite Backup API 合 WAL，五日志卷全目录原字节含内部 WAL、空目录；恢复到此前不存在目录，状态始终 restored_disabled。全事实指纹包括所有表/rowid/账本，另核 Memory guard；不能用旧包复活后来撤销/遗忘/unknown。源 authority 丢失仍拒绝。返回 `verification_sha256` 绑定当前 authority、恢复 UUID、完整事实与原备份逐文件核验；它不是公开业务验收结果。

代码选版依旧使用旧 prepare-update/rollback-code；不将“选版本”当作镜像切换，不恢复旧数据，也不自动激活原恢复目标。

## 一次性副本的公开功能读回

许可语义见 DRILL-PERMIT.md。协调先人工准备同 scope 的 `drill-inputs/<id>` 和许可，再单独调用；工具不会生成许可后自行运行。schema 兼容 C:/ 绝对路径以做 Windows 计划/边界测试，execute 仍要求 Linux flock。

`inputs.json` 严格为 `{schema_version:"dep-j-drill-inputs/1", files:{相对文件:sha256}, assertions:[...]}`。files 是封闭文件集，不含 inputs.json 自身，绑定两份 Compose、专属配置/TLS/合成秘密，以及需要的原版 contracts/tools/obs code。代码/入口/命令必须与 G 固定输入一致，私有凭据必须与原输入不同；不得从原配置随意替换或继承真实外部地址。复制只写新演练目录，执行 UID/GID 10001；不会更改源或禁用目标权限。

Compose 两项目名来自许可，四产品+五日志 owner，镜像使用已核本地 `sha256:...` ID、pull_policy=never、禁 build/pull。全部网络 internal，全部 bind 属于新副本，所有写挂载须与五卷合同/产品卷 owner 完全一致。端口仅显式 long syntax 的 127.0.0.1 高端口。未列出的 service 字段拒绝，原代码/command/entrypoint 不可换成任意 argv。

assertion 结构：`id`（data_readback/source_revoked/model_revoked/forgotten/unknown_no_resend）、`service`、`url`、`ca_file`、`token_file`、`expected_status`、`expected_json`。只允许到该 service 已发布 loopback 端口的 HTTPS GET，无代理/重定向；令牌取副本专属文件，CA 必须校验证书，响应有总预算/体积限制。expected_json 对对象按子集比较、其他类型严格相等。禁止 `/health*` 代替功能；实际端点和断言须由协调依据固定产品公开接口准备，不能使用测试里的 fixture URL/响应。

```text
python -B -m ops.recovery --root <scope> --scope-id <scope-uuid> drill-clone --permit <独立准备的许可.json> --permit-sha256 <另存hash>
python -B -m ops.recovery --root <scope> --scope-id <scope-uuid> --execute drill-clone --permit <同许可.json> --permit-sha256 <同hash> --docker-executable /absolute/path/to/docker
```

执行前复核许可时限/一次性 claim、源 owner 真停止、原恢复目标未挂载、原备份逐文件与全部事实。副本不能自证 authority。启动前持久写一次性 claim；未知/失败不重放。预算至少保留60秒用于正常关停，副本租约覆盖启动到关停。部分 Compose 启动失败也只对逐项核验过的确切 owner 发 SIGTERM；不能确认时结果是 stop_unconfirmed、CLI 非0，并保留容器/目录/报告/claim。

输出始终 drill_only=true、release_ready=false、original_restore_activation=false。缺语义或缺产品返回 partial_functional_coverage/非0，断言失败或关停未确认也非0。健康只决定何时尝试功能断言，不生成业务通过记录。

## 明确保留的未执行维度

- Linux 正式镜像/依赖/权限、九真实容器正常关停、真实备份恢复和公开HTTP功能断言：本机未执行。
- 需要协调提供合规的专属恢复演练配置、凭据/TLS和真实功能断言；若新凭据需公共CLI引导，应在独立合成准备任务中完成，不能SQL造授权或自动重签过期来源。
- 缺独立内部录制模型拓扑时，不增加第十个未登记 owner、不继承真实模型端点；相应语义保持缺失。本入口不宣称所有业务恢复已过。
- obs guard 正常SIGTERM修复由DEP-I负责并由协调固定集成；J不接受143作为替代。
- NAS/真实设备/真实模型/生产恢复/灾难恢复及原目标自动激活均未执行、未授权为本地工作者自动动作。
