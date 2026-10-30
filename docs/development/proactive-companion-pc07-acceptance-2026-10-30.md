# PC-07 真实主动闭环验收

状态：`verified_production`，仅 PC-07 整卡验收通过；完整18卡目标和W1尚未完成，W3禁止。

受控账号的原生订阅登记入口已通过有效网页及QQ管理会话实测。R43在14:09:12（Asia/Shanghai）实际生成并送达一条主动消息；Memory v2真实scope/来源证明、模型回执（输入7128、输出522 token）、一条原生渠道消息均已核验。发送前后复查同一命令，仍为原动机、候选及一次表达/投递，没有重复发送。R44重启后这些次数及原R42未知身份仍保持。

原回应截止后，有限只读验收进程在 `2026-10-30T07:09:27.695262+00:00` 保存真实PostgreSQL快照：contact反馈为 **unanswered**，订阅unanswered_count=1、unanswered_limit=1；后续原生观察候选为 **suppressed / unanswered_limit**，表达尝试0、没有投递。既有daily_limit=1、cooldown=3600、静默22–08和全部gate保持，未更改时间/额度，没有再次发送测试消息。进程按原截止等待，读取原生业务状态并退出，没有写业务表或触发模型。

已核查只有一条proactive:event的原因：全局唯一历史marker属于image_completed且已经completed、游标null，当前该角色没有active订阅；不能仅凭当前记录还原其创建当时的全部订阅。智乃当前63条activity_boundary均actor_only，另4条schedule_reconciliation不属于可生成动机的种类。现有offer_event在这些条件下于任何对端调用之前返回，当前没有pending marker被403卡住。保留受众边界，不回放旧事件或改变actor_only来制造自动消息。

验收逐项对应原队列：active订阅、motive、sent候选、真实回执；原内容幂等；真实未回应计数与suppressed；受保护登记入口可达；历史事件路径诊断。真实新的自动事件自然效果和长期质量仍未验证，作为后续卡的限制保留，不额外增加原PC-07没有要求的完成门槛。

Companion上下文代码 `3c6d047b2a358a6d46be66015027d0e410ac04f3`，Platform管理入口 `bbd1e63abf8138d108f1e0d13304edbf25d71b97`，分页来源 `7f5e837593c8119e8c80e40c7f498314b730306e`，当前R44 Companion `c8b1c10cbea19c4b964bdd5456c40edc5d70c288`。此前定向验证输入未变直接复用；本次仅真实截止快照、原生渠道及受影响PG只读核对，没有重新跑模型、全量测试或浏览器套件。账号、正文、凭据与原始完整ID留忽略目录。

15:11 后续W1观察：R44自身14:41:35–15:11:35完整30分钟窗口auth rejected=0、日志字节预算完整、坏行0，未借用R43窗口；原较早一次unauthorized的不利窗口仍保留。固定R38启动后的新决策cohort现为completed12/total18（66.667%），failed2、unknown3、interrupted1都保留，未满原24小时且未达80%。活动paused为1且恢复时间非NULL，dormant6，未自动重开；这不证明有真实新日记或完整生活。
