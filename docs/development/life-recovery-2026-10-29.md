# 日程与自主生活恢复

用户授权修复本次截图中的日程中断、自主目标读取及活动暂停。记忆系统线程确认无新候选、迁移或 Companion 写者，并释放边界；本窗口为唯一生产写者。保留用户对话记忆逻辑、配置数据和旧 TS306/307 原稿。

基线为实际运行 R26a：C07f85f9 / P31f7bcc / M57a1a14 / Ged631d8 / application5badd01，不能退回 R25 覆盖已经部署的 OneBot 接入。根已 FF 合入该运行历史；原脏协调检出未改。

| 卡 | 修改归属 | 本地结果 |
| --- | --- | --- |
| TS417 | 原 DailyLife 调度、回执核对与失败历史 | C5430300（含局部 PG CAST 补丁），14 项定向通过；补丁复跑 1 项 |
| TS418 | life/read 错误编号、Activities/RuntimeExecution 诊断及安全续接 | 集成 Cc4af05e，12 项定向通过 |
| TS419 | Platform 自主目标复用现有已授权 web-life reader | P6645d64，固定 Linux 镜像 4 项 HTTP 定向通过 |

澄汐旧 life_access 不含独立管理 reader，而普通 web-life reader 已获授权。TS419 保留 owner、memory.read、会话前后和版本检查，改用该现有读取通道；不添加授权、不自动复活撤销。错误返回沿用已验证的 query.request_id，避免用 invalid_upstream 掩盖原错误。

日程自动恢复只在原回执证明前次本地执行终结时开始新纯生成尝试，次数和回执查询均有上限。旧无请求编号的中断不猜测回执，维护恢复沿既有显式 retry，一次开启新 attempt 并保留历史；已完成智乃计划不重发。

活动保留真实 checkpoint、原工具结果和未知执行；已知失败有界退避，真实推进后重置连续失败额度。预算让出沿同一操作和消息/回执继续。未知工具不重放。线上历史模型断流另按实际记录处理，不把未知结果改为成功。

仅复用上述定向结果，装配后核对安装版本、启动健康与受影响网页入口；不跑全量、双后端、完整浏览器或固定探针等待。无 Memory/schema/依赖图变化，无全库备份迁移。

## R27a 已推送部署与真实日程恢复

2026-10-29 正式运行 `2026.10.29-unified-linux-r27a`，镜像 `sha256:3f15d359d8320bf5cc96aa3d21be211335185ad6cc99eb6ed032b484e76aa700`，容器 `0389cc5885c522b25c1ea85e9e2cb95cc79db06f5bcfba92a37f927ce7a0481e`，容量守护 r23。产品 main 已正常推送：Companion `5430300bc37e1ff17002c25e764acdd4689b9f24`、Platform `6645d642faa2fae34447f9a0f951a2dcbf0926cf`；Memory `57a1a1468382641ea1d33cae9498c9ed170f6a7a`、Gateway `ed631d8a6f26890628a9df833792210e9a09e31c`、application `5badd018e23d89fb89d0778375aaa3c7e69436ba` 保持。83 个前端文件与既有 OneBot 接入继承，不覆盖 R26a。

R27 实机暴露新 recovery_checks 查询的 PG text/integer `42804`，同一只读谓词对照证实显式 INTEGER CAST 可消除错误，修复放在 DailyLife 自有查询，不扩展共享方言。R27a 仅替换 Companion wheel。旧成功检查未重跑，追加的现有 SQLite unknown 恢复节点通过；正式队列随后确实完成生成。

R27a 离线构建首次达到 Docker 历史层数上限，改为三条增量指令；运行用户校验又发现 wheels 目录遍历权限不足，修正两级目录为 0755 后重新构建。失败候选均未激活，没有弱化鉴权、数据或容量保护。最终镜像以 uid10001、network none、readonly 检查 120 个 Companion 安装文件和发布元数据通过，65 个未变 wheel、前端及 application 继承。

最终替换正常停止旧 R27，退出码 0，约 124.24 秒，无强杀。新容器 healthy、0 restart、无 OOM；六业务与五观测当次全 ready，原鉴权 ready 200（9 checks）、网页 HTTP 200。37 个挂载精确一致，1133 份配置保持；另一份仅原 owner/scope 启动租约例行续期，未增加权限。激活时另外 14 个已有容器的镜像、运行状态和重启计数保持；20 GiB floor 与 r23 armed/心跳有效。该次核对不等于长期稳定性或全系统重新验收。

用户正常重新登录后，真实网页已看到两角色自主目标，原 invalid_upstream 消失；未增加管理 reader 授权。澄汐当日日程沿既有按钮仅显式重试一次。R27a 启动初期 preparation 的 dependency_unavailable 未提交模型，随后既有退避自动恢复；最终原计划和原任务 completed，request_attempt=3，旧 interrupted 与启动依赖失败两条历史保留。真实新模型请求仅一条，OpenCode DS `deepseek-v4.1-flash`、HTTP200、execution completed、17.98 秒。智乃原日程的 completed_at、attempt=1、request_attempt=1 与无重试历史保持，未重发。过去阶段不编造离线经历。正常业务恢复调用与隔离测试分开记录。

智乃旧活动仍为 protected unknown：旧 marker 无 generation/model_request_id、步骤与工具结果均为零，原 Gateway transport_unknown 也未证明执行关闭。未清空 checkpoint、伪造完成或重放未知步骤。澄汐当前目标为保留作息而暂停，最近规划反馈 invalid_input；本批恢复读取和诊断，不宣称该次模型输出已成为有效新目标。10 月 4 日私有范围的睡眠活动与当前 actor-only 目标不同，未误改历史活动。

重载后原网页会话再次失效，未要求用户重复登录；最终日程完成通过正式存储及网关回执只读核对。重载前自主目标页面截图在本窗口 `.runtime/life-recovery-r27a-20261029/goal-page-proof.png`，业务结果及最小启动核对保存在同目录，不提交私密运行材料。未发送 QQ、测试外站账号或重演完整浏览器套件。AL14/15 继续延期，AL18 长期连续体验不能提前标完成。
