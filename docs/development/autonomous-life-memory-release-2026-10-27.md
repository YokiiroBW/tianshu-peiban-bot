# 自主生活与记忆扩展联合发布

用户 2026-10-27 最新明确授权：**“你这边推进完成后，连着记忆系统的更新，一并推送并且部署。”** 本授权覆盖本批先前仅本地、不推送部署的阶段限制。当前聊天负责统一发布协调；记忆扩展原协调者已收到不得并行生产写入的通知。无需再次索要同一授权。

当前状态：五产品已正常快进推送 main，尚未切换生产。AL08/TS318、AL11/TS320–321 已本地验收；
真实 SDK/Platform 双进程联合已通过，集中复核发现的 claimed 分页和已确认源派发期限
边界已在原归属修复、复验。正在构建固定产物并闭合 actor 原生备份恢复后串行发布。
不能以 schema 自检、本地合成结果或镜像标签替代实际运行源码与实机验收。

## 已确认输入

- Memory 扩展：`a2de620c676a24d7c08bb33ef3f1be2488b71666`。
- Companion 扩展基线：`671deeadb04301b42f56a3a2020548117698bbc0`；最终须完整包含 TS320 integrations-only 后续提交。
- Platform 扩展：TS316 `3e2cc412c676450a90dc0cbd3c3387d1c945e5e2`；TS318 内容发现 `6d8dbaf29979c210c027f36a8333b5a605f7c3e6`；最终须完整包含 TS321 后续提交，不重复合 TS310。
- Chat/Gateway 取 `memory-extensions-delivery-2026-10-26.json` 的最终完整提交及实际依赖，不能遗漏后默认为只更新三个服务。
- Chat 真实生产来自 `66097a98e8613437f96a76ddae4e23bb7e1fd609` 发布快照，main 不是现网基线。
  TS322 `6aa6e7f2765ff2702b443b1fe318b38ca91a9057` 已合 main 与 TS315 两侧历史，逐字保留
  66097 的 message/import 两模块和原生产身份测试；相关43P，原 Windows fcntl 收集错误保留。
- Companion/TS320 固定 `d416b68c2bf47f6a5675c8f170c5185062e66688`，适用29P/1 AstrBot SDK skip。
  实际 OneBot 发行需要 adapter0.6.0 和 storage0.1.3 两 wheel，不能只更新核心 Companion 镜像。
- Platform 最终 `620b1e0b0a6c76ac69f538a13e00c9c6d53b112d`；Gateway 最终 `5ff98947f7eaf82384d359ca84ad581a5cf113f0`。五产品的上述最终提交均已实读远端 main 确认，正常非强推，原 root index 未变。
- 新 AL11 候选合同提交 `188b52a6cc8fe8736bf569d117bcf7517cfc1ddc`，manifest LF SHA256 `347b60a1dde6967adf559597e5bd7dfaa948f5b268d158d04ea3e3ad78b7c826`；两消费者实际联合10项闭合，生产合同安装尚待。
- 原扩展合同完整快照 `.runtime/memory-extensions-20261026/contracts-v7` 未改；AL11 消费快照是复制的 `.runtime/autonomous-life-20261026/contracts-wave2`，不以 root 混合工作状态构建发布。

来源：[记忆扩展交付](memory-extensions-delivery-2026-10-26.json)、[本批交付](autonomous-life-wave2-delivery-2026-10-26.json)、[上次实际发布](memory-contextual-release-2026-10-26.md)。当前拓扑按最近实机记录为 `.230` 计算、`.210` PostgreSQL/Redis/媒体；正式操作前须只读核当前版本、服务和部署目录，不照搬过时 NAS 计算拓扑。

## 发布边界

根原 index、混合改动与其他任务保留；以独立精确提交串行集成，核对远端当前历史再正常推送，不 force 覆盖其他更新。冻结构建输入、镜像及合同版本，沿现有备份/恢复和部署流程，保存实际回滚材料。真实操作的精确卡与命令待只读发布准备核实后补齐，不在此写未经验证的部署命令。

保留现有配置、数据、凭据与其他服务；必要的应用 schema 更新沿原 owner 与备份流程执行，不清库、不回填虚构人物或经历、不重置用户对话记忆。角色自身记忆维持独立域。部署不等于授权外站账号互动、真实 QQ/模型测试或新增账号权限；验收先使用当前健康/只读端口及本批隔离检查，确需真实侧效应时按实际范围判断，不默认发送测试消息。

AL12 尚未实现时普通候选仍静默；NoneBot 的普通自动领取只在受控独立宿主有真实完成收据时可用，AstrBot 仍不伪造完成证明。真实经历不自动注入可公开发布的虚构日记。内容发现真实搜索服务/账号配置与后续实际阅读消费须分别核实，不能以本地 HTTP 夹具宣称已接通。

## 尚待收敛

发布代理正在只读核对现有脚本、配置落点、五产品集成与构建顺序、合同/Guard 资源更新、备份恢复及实机检查；本文件会在功能收口后更新为实际精确执行卡。失败、跳过、未覆盖和实机证据独立保存；发布完成前不把本批标已上线。

## 实际盘点与验证接续

只读证据在 `.runtime/autonomous-life-20261026/release-inventory-20261027/inventory-summary.md`。
五核心仍在 `.230`，24个原PG逻辑源 active、guard ready；Chat app/独立 backup-worker 在 `.210`，
同一 FFmpeg 镜像、独立 PG 与备份目录；OneBot 在 `.230` 独立 SQLite，真实 import 是
`/app/tianshu_nonebot`，固定 NoneBot2.5.0/OneBot2.4.0。

现网只有一个 active角色和已授权私聊，群策略实际为 observe_only、无群 actor/白名单或
可用 WEB 群 reader scope。本批保留该策略，不凭空生成人物/群映射或将角色内记忆公开。
新域及后台固定身份可以配置；群历史与普通自主参与的真实范围沿原管理配置另行明确。
AL12 未接入时普通候选继续静默。

TS321 初始058f9a的15个新行为已在自有UUID非生产PG全部通过，源码前后不变，1数据库/1角色
已沿原生命周期清理；证据 `.runtime/autonomous-life-20261026/platform-pg-r1/receipt.json`。
后续三文件边界修复仅续验5个受影响真实PG节点，全部通过，最终620源码未变，自有UUID库/角色已清理；
证据 `.runtime/autonomous-life-20261026/platform-pg-r2/receipt.json`。不是新版本全量重跑。

TS323 在独立 workspace 接续已实际使用的 recovery 基线 b794fbf，仅补 actor-memory 的
原生PG/guard备份消费，不改用户 Store；将新域纳入发布后的完整备份清单。
最终`8fd0a34799021dadf67029f4bdf5af8b71dfb178`已集中审查；相关组件26P/7条件PG跳过，
另外在`.230`用现有PG17原生客户端执行新增actor真实用例1P/0S，两个独立archive均实际
dump/restore并检查guard、installation/schema隔离；两个scratch DB均不存在，自有测试库/角色已清理。
证据`.runtime/autonomous-life-actor-recovery-20261027/receipt.json`，不是生产恢复或部署。
构建材料在 `.runtime/autonomous-life-release-20261027/build/`，最终 manifest SHA256
`689ccdc75df740f239e61bfddd4e226fd6af6bfa27a6dbe3d86258d4ce8149ec`。
本地wheel与68个Web产物已构建，payload已上传两主机的新stage，七镜像隔离构建全部通过，生产未切换。
最初wheel hash与Windows链接权限两次失败原件保留；wheel差异实证仅7文件的LF/CRLF和RECORD，
归一后精确匹配d416 rawGit，重建wheel实际SDK启动通过；链接使用经路径核验的junction，
仅移除本次junction、不递归触碰借用依赖。没有修改共享安装环境。

逐owner合同合并保留现网每份原始字节，0个旧文件替换，6组42项真实loader检查通过。
Memory/Knowledge保原内容合同pin，不能统一挂载275文件快照；统一快照导致Knowledge实际加载
失败的原件已保留。Platform原`/contracts`之外的媒体包、人格exact及diagnostics rawCRLF均已核对。
用户extraction schema6与独立actor candidate.2保持，生产尚未应用；精确目录清单见联合发布JSON。

配置准备曾有一次脱敏遗漏，部分既有服务凭据进入工具输出。已停止通用配置值输出，
后续只记录字段白名单、哈希和计数。受影响范围私有记录在 config/incident.private.json，
已证实PG管理员身份及口令跨天枢、Chat及其他服务复用，Redis身份亦被control-hub复用，
同secret还有其他身份用途。共享轮换超出本次天枢发布范围，已向用户询问是否扩展授权；
在答复前不改共享角色或影响其他服务。3项天枢专属token完整候选已准备但未应用。
Chat APP_SECRET_KEY用于历史备份验签，新旧备份恢复闭包必须单独处理，不能只改配置后称已恢复可用。
不在本文件记值，不冒称已经轮换。
