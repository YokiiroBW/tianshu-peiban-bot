# 自主目标与真实阅读闭环：R14 接续

用户要求继续 AL06、AL09/AL10，随后补 AL12 旁听记忆沉淀；基于已完成的单容器模块化版本，不回到旧多容器或旧产品 main。用户对话记忆保持原逻辑，角色经历只进入独立 actor 域。先协调写入边界，独立部分并行，功能稳定后集中审查验证并更新原 18 项队列。

## 基线与写入归属

- 协调检出从 R14 最新交接 `b930ef638b95b8d363fc889de8527bbf711987a9` 建立；R14 application 运行提交 `48eee53d6ba97efae5c44385cb53a894ffa0c215`。原根混合修改和 index 保留。
- Companion 运行基线 `225f62a9c201acfc283bc029562f118f16d5e524`。
- Memory/Knowledge 运行基线 `8d01f9d9d39fba33b6ca8dca9d2599730c9c7e1e`，交接 `34d3175a07f853613680d76bf815611489a9edec`（仅多文档）。
- Platform 运行基线 `177e2eb25a104a9d58bfb71f53040baa481ddbf6`，交接 `4ef6edb922e14b66d5a252e70f518c4bc70ff907`；Gateway `0f491be3573af9257703b7871aaa746598d66b9f`。
- 已向既有“梳理天枢的13个服务”和“记忆系统隔离测试接续”请求只读交接；两位协调者均确认原写者结束、冻结检出 clean、没有待合入新候选；本批仅独立分支写入，不改其冻结检出。

标准 workspace.py 分配器仍依赖可变旧产品 HEAD 和干净协调树，不能表达本批精确线上基线；沿 R14 既有办法建立显式 SHA 的独立 worktree 并记录 workspace-context，不重置或暂存原改动。

| 新卡 | 负责范围 | 顺序与边界 |
| --- | --- | --- |
| TS-384 | Companion AL06、AL10：既有 concern/activity 滚动目标；同一 SkillRegistry 的后台能力/发现/实读/理解/actor 派生闭环。 | 唯一 Companion 实现写者；不写用户记忆提取/画像/摘要/召回路径，不另建计划账本或后台工具定义。 |
| TS-385 | Knowledge AL09：字幕/转录输入、真实音视频区间、覆盖与缺口、长内容续读。 | 唯一 Memory 产品写者，仅内容读取模块及测试；不改 actor/user schema、segment 或用户记忆。与 TS-384 并行。 |
| TS-386 | AL12：旁听的 observer 经历、来源校验、可靠写回和后续探索。 | 在 TS-384 稳定交付后串行写 Companion；必要 Platform 窄来源装配由协调者独占。候选 TTL 不充当来源有效性。 |
| TS-388 | AL12 Memory：原 Observation owner 的 actor 来源 facts/check 窄适配。 | 在 TS-384/385 稳定后独立 Memory worktree 串行接续；复用原归档、别名及 suppression，不改用户提取/摘要/画像/召回，不扩大 Platform 身份。 |
| TS-387 | 协调者：跨产品接线、单容器联合验证、集中审查及队列更新。 | 根合同、装配、状态与最终集成单写。真实 QQ/付费模型/外部账号动作仍不计替身验收。 |

## 完成条件

1. 无新用户消息也能选出具体目标，优先保留已约定事项；可继续、延期、改观或放弃，重启和跨日保持因果。计划、真实执行与失败分开。
2. 后台从既有能力注册表获得实际可用能力与来源，在权限和原预算内发现、选择、实读、理解、可靠写回独立 actor 经历/认识，并用于下一次目标。未知执行结果先核对，不自动重复副作用。
3. 阅读实际覆盖与缺口由真实文件/区间与版本支撑；标题、字幕、音轨、静音画面不能冒充完整观看，未安装转录能力明确报告不可用。
4. 未回复但有价值的群观察可保存为“看到群里讨论”的 observer 经历，原群范围/来源撤回/角色解绑仍有效；后续探索不能把群内私人内容公开化。
5. 复用原 actor outbox、来源/受众/撤回权威和单容器进程内传输。普通用户对话不新增必经规划模型，不扩大总模型槽。
6. 稳定后审查完整 diff，运行相关产品回归与实际单宿主隔离联合；SQLite/PG、替身/真实接入、开发/集成/部署分别记录。不重复未变化成功检查，不把健康状态当连续生活验收。

初期阶段记录：边界确认后 TS384/385 并行，TS386/388 串行接续。最终状态以下方交付收口和交付 JSON 为准。

AL12 只读设计发现：原 participation 候选仅在 active 模式产生，不能把 silent 分支当作 observe 模式的通用旁听入口。应从真实已归档观察回调独立判断有无保留价值；Memory 通过原 Observation owner 提供 source facts/check，actor authority 只委托。现有 actor 合同已支持 producer=observation、observer、observed 和空 reading coverage，无需伪造阅读。停止新自动处理不等于删除用户历史；待办保原 per-group generation，避免 off/on 或 A→B→A 复活旧包，已完成历史则仍按原来源撤回/forget 权威核验。

## TS-385 稳定候选

Memory 候选 `3f75fdf96e80221039e8204d9ffbb70cf5367dd0` 已提交且干净，26 个不同本地节点通过；首次 codec 夹具失败、AAC 首片缺失和小预算字幕问题及定向复验保留在产品交接。协调者已审查完整实现 diff，未发现需阻断集成的未解决问题；原内容权限/解码后 current/access_version/hash 复核保留。候选仅扩真实字幕/音轨/帧与续读，不改 actor/user/schema/锁/合同。Linux 安装包及单宿主联合仍待 TS-387，不把本地成功算部署。

## TS-388 稳定候选

Memory `9453e5e3fd43c80a41f9bd4601f0181e2e7c6db5` 已提交且释放写权，14 个新增及 56 个原有本地节点通过，1 个 root-exchange 节点留完整环境。独立完整 diff 审查未发现可实证阻断；新只读 observation-source 口沿用原 actor caller/角色权限与前后来源核验。只消费原 Observation owner 与 Core checkpoint 的只读部分，用户算法、业务投影、schema 与 guard 不变。Linux/PG/单宿主联合仍待最终组合验收。

## TS-386 与最终安装包验证

Companion 首个 AL12 冻结候选 `d099962c108618af2895a9e1a94cbf2f2d6aa8d6` 已包含两项 AL06 集中评审修复及原 outbox 共锁修复，本地 74 个适用节点通过。集中审查另发现旁听 hint 消费后仍可能约束后续不同目标的外部查询，已交原写者最小修复；不会以首版候选宣称最终验收。

精确 d099/9453 安装包已在 R14 上构建隔离镜像 `sha256:ef7b5bfe0760d36950159edf8e3d8ab32b979f6e3f2f998c3f69653543b538a8`；仅替换这两个候选 wheel，运行依赖元数据与 R14 一致，未重新解析依赖。组件完整回归、原生自主阅读链已启动；独立 PG17 仅 Unix socket、network=none、无生产挂载，产品修正后补受影响检查。

## 最终收口

最终 Companion `16ca5a81490255a892d697d80b5be0fa43644688`、Memory `9453e5e3fd43c80a41f9bd4601f0181e2e7c6db5` 已完成本批隔离候选装配、集中审查和有效验证。前述 pending 均为阶段历史；hint、召回预算、timer 和 PG expiry 真实缺口已修复。相关组件308个不同节点适用通过；SQLite/PG自主及旁听链、原PG用户记忆/日程链通过。全量测试仍有已记录限制，不宣称全绿。24个专属容器及测试临时库清理，R14正式healthy/0restart保持。

TS384–388 done仅表示协调者接受的隔离R14候选，不代表产品main或生产更新。[最终说明](autonomous-life-wave3-delivery-2026-10-28.md)与[精确证据](autonomous-life-wave3-delivery-2026-10-28.json)为权威收口；18项其余范围继续保留。
