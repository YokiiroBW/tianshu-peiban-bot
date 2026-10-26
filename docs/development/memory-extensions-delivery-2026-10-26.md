# 记忆扩展本地交付

2026-10-27，用户授权的五项扩展已按完整功能块并行实现、串行收口，TS313–317均为
`local_verified_not_integrated`。五产品候选已提交且clean；沿用原worktree，新分支统一为
`codex/memory-extensions-20261026`。本批未合产品main、未推送、未部署，已发布核心继续保留。

| 产品 | 最终本地提交 | 归属 |
| --- | --- | --- |
| Memory | `a2de620c676a24d7c08bb33ef3f1be2488b71666` | TS-313 |
| Companion | `671deeadb04301b42f56a3a2020548117698bbc0` | TS-314 |
| Chat | `1c2ee12c42d466bd9b48d1dd67d491429753f395` | TS-315 |
| Platform | `3e2cc412c676450a90dc0cbd3c3387d1c945e5e2` | TS-316 |
| Gateway | `5ff98947f7eaf82384d359ca84ad581a5cf113f0` | TS-317 |

Chat在原授权归档上提供关键词、作者、时间、分页和邻读；当前渠道及全部已知原生别名共同
核对来源与遗忘，模型不能替换账号、群、角色或远程地址。原话不自动灌入每轮提示词。

现有群记忆页增加reply/@互动图、可缩放二维话题点云、热度时间桶与证据回看；复用Chat统计、
Memory已整理话题和原Web登录/Origins。互动强度不等于好感；布局不写回事实；原始消息热度与
整理覆盖分开显示。统计按物理消息、单窗最多10000条，不推断一条多段BOT回复的逻辑数量。

用户记忆与BOT自身经历保持独立。BOT实际阅读的区间、原件版本、覆盖和受众在原Reading事务
中形成来源与outbox，再由Memory独立actor域保存；认识只能引用本角色实际召回的episode版本。
召回、派生和后续使用继续核对当前来源、角色及受众。内部材料不因一次群授权自动公开；撤回后
派生闭包失效，旧回执重放不恢复事实。原后台phase/公平调度与受认证background模型路由复用，
没有新增持久队列、模型步骤、语义命中规则或模型槽位。

默认Knowledge回查改为按原Memory连接配置单独构造、登记关闭的现有JsonService，避免三个
外层Memory请求占满门后又等待同一门回查。各客户端仍为原3/4容量和15秒预算。显式Knowledge
配置与直接Core注入保持原行为；这项回归用固定MockTransport，实际服务TLS联调另有独立收据。

## 验证与证据

- Companion先跑一次完整组件，再集中修合同、工具归属和夹具，仅重验受影响组；最终适用
  1728父项为1680P/48S、451聚合子项P。默认客户端补充组36P，90子项是重验，不重复加总。
- Memory相关142项适用119P/23S；Chat78P/1Windows范围跳过；Gateway59P；Platform相关
  后端51P/1未配置PG跳过。真实隔离PG另有70项适用通过，数据库和账号均已清理。
- 群页面桌面/手机10个行为适用通过；保留首轮两项CSS失败，修复后只复验受影响行为。
  TypeScript、Vite构建及相关静态检查通过；原格式和构建警告在各产品交接中保留。
- 实际本地TLS联合：Chat→Platform→Memory1P，Companion→Memory→Platform自身记忆2P，
  Companion→Platform→Gateway后台路由2P。原fixture失败、XML、源码hash及适用关系均保留，
  不把多轮适用结果称为一次全绿完整重跑。
- 根workspace检查因51个历史done任务目录缺失失败，这些路径和状态在本批前已相同。
  没有扩修无关任务元数据，不把它算成产品验证通过。

精确提交、原始收据hash、失败/跳过和适用范围见
[结构化交付](memory-extensions-delivery-2026-10-26.json)，产品交接在各worktree的
`docs/handoffs/TS-313.md`至`TS-317.md`。

## 本批边界

真实actor联合仍用固定Knowledge原文TLS peer、原Harness用户对话锚点，不是完整Knowledge/
Core/OneBot实机验收。较长原文超过原4000字事实预算时保留精确引用，不编造缩略理解。
未映射且不受支持的QQNT别名会过滤；受信external-ID导入可按无已知遗忘查询，但不冒称Core-current。
schema6无法携带新Chat/Actor来源证明的BOT派生材料会省略，用户原话保留。

真实经历暂不注入可公开发布的虚构日记，因为其现有发布ACL没有逐来源的当前性/遗忘闭环。
本批可用于内部计划和原授权对话。外站账号动作、AL08内容发现、AL11观察参与及完整自主生活
队列属于后续独立任务，不能把本批最小闭环称为全部完成。

真实模型调用本批0新增，账本17540/18000、余460；无QQ测试消息。原分支、改动、worktree、
根HEAD与原index均保留，不reset/clean/stash。共享合同和本交付将另存本地分支，根CURRENT/
任务板混合工作状态不整文件带入该提交。任何推送部署仍按后续用户明确授权。
