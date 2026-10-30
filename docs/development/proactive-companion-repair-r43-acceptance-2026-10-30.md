# R43 发布后的真实业务验收（2026-10-30）

状态：`in_progress`。本记录补充 R43 发布时的健康收据；没有再次部署，也没有把发布时尚未发生的业务结果倒填到旧快照。W1 尚未完成，W3 保持禁止。

14:09:12（Asia/Shanghai），经已登录网页、QQ 管理解锁及原生“测试主动联系”，智乃向用户明确授权的测试账号送达 **1 条真实主动消息**。原生渠道回执确认 1 个表达、1 次投递、1 条渠道消息；实际 Memory v2 bundle 的 scope 与候选精确一致，模型回执记录输入 7128、输出 522 token。发送前后复查同一命令，仍是原动机、原候选及一次表达/投递，没有重复消息。分页后真实私聊来源亦已在 R43 页面验证可用。

订阅原限制保持：每天 1 条、未回应上限 1、冷却 3600 秒、静默 22:00–08:00。发送后 unanswered_count=1，反馈还在 waiting，实际回应截止约 15:09:12。另一个原生观察候选已被 cooldown 延后，表达尝试为 0；尚不能把它记为 unanswered/suppressed。R42 原未知候选仍保留原身份、版本与一次表达尝试，零投递；没有重放。自动符合受众的新生活事件尚未验收，不回放旧事件或修改 actor_only 制造进展。

两位启用角色都通过新草稿入口提交 **真实当天 2026-10-30** 的日记准备请求，各取到 2 条完整真实素材。两条记录实际在 generation 阶段 unavailable，均有原模型请求编号及 unknown 诊断，**draft 仍为 0**。澄汐遇到 Gateway ReadTimeout，原回执 cancelled_unknown；智乃已收到模型输出，但 Gateway 在 30002 ms 超时，原回执 timeout_unknown、无 finish_reason。没有 usage 的请求保持未知，没有记零、补写草稿、放宽 episode_refs 或自动重试旧请求。此前的原输出结构问题仍以 R43 定向验证为代码证据，不能由本次失败推断已得到真实合规新草稿。三个真实日期的连续产出继续待验。

PC-02 已按用户选择在 R38 移除未正式启用的 household 安装占位角色，历史、正式角色权限与授权检查保持。固定新决策 cohort 从 R38 实际启动时间开始，跨发布不重置：当前 completed 8 / total 13（61.538%），含 failed 2、unknown 2、interrupted 1，尚不足 24 小时和 80%。两条 failed 是 application/result_unknown，原模型回执虽成功，不代表业务应用成功，不改写计数。一个 unknown 有原 30 秒超时回执；另外重启时的 interrupted/unknown 原记录保持。

R43 排空前完整 30 分钟日志有 **1 次 unauthorized**，不是零。该关联号在 Platform 有原 started/finished/auth 三事件，在本次有界 Companion/Gateway 日志中未找到同关联调用；由于原 Platform 日志没有路由与主体，不能猜成网页登录失效或服务凭据失败。保留这个不利窗口，后续另验当前版本真实完整窗口，不换算法排除它。

本次只做原生管理动作、受影响 PostgreSQL 的 repeatable-read/read-only 查询与有界原日志/模型回执核对。没有重新运行输入未变的产品测试、全量或后端矩阵，没有新权限或数据库写入捷径。账号、正文、凭据及原始细节只在忽略运行目录。后续：日记生成故障、真实未回应抑制、符合受众的自动事件、原 24 小时与三个真实日期观察。

14:32 后续独立观察：R43 连续运行的真实 14:02:26–14:32:26 完整 30 分钟窗口，Platform started/finished 均 6012、auth succeeded 5972、auth rejected 0，字节预算完整、坏行 0。此前一次 unauthorized 的窗口仍保留，不据此倒改历史。固定 cohort 新增真实决策后为 completed 9、failed 2、unknown 3、interrupted 1，共15条，成功率60%，仍未满24小时或达80%。原始新证据为忽略目录 r43-observation-1793341947593667300.json。
