# TS-115 Companion：关系投影与表达

状态 done / local_verified_not_integrated（2026-10-01最新交付，覆盖原preparation_only门禁）。父级已放行同一既有任务的串行本地实现、验证及提交；工作树 worktrees/TS-115/tianshu-companion，分支 work/ts-115，交付 ea5c044719bfa76de384b0b69bbfd7ed1439996b。未合产品main、正式发布、部署或真实QQ/模型验收；BOT UI归属仍不冒称已核验，不新增窗口。基线和证据见本批计划、产品同号handoff及固定delivery JSON。

## 责任与白名单

relationships/承载客户端、版本化投影装配与事件候选；core.py只调叶子模块，不保存或重算权威分数。可信身份的actor_id/person_id确定关系，聊天昵称、引述、模型输出和人格文本不能指定真实身份。人格只表达，不放鉴权/内部识别协议。

相对本产品白名单：
- src/tianshu_companion/relationships/
- src/tianshu_companion/clients.py
- src/tianshu_companion/core.py
- src/tianshu_companion/app.py
- src/tianshu_companion/context.py
- src/tianshu_companion/runtime_cli.py
- tests/test_relationship*.py
- docs/relationships.md
- docs/handoffs/TS-115.md

已有clients/context/app/runtime_cli仅必要装配/配置/注册；不改store.py迁移、personas业务、qq_identity规则、已有权限，不复制Memory数据库或策略算法，不改依赖锁/根合同。

## 具体增量与验收

- 以当前actor/person及受众范围读取有界投影；私聊分数/亲密关系不能默认进入群公开上下文。无法读取/未授权时保守缺省，不假装分数0或正常。
- 关系类型与阶段影响表达；管理员身份与能力仍按现有可信链。静态人格升级、角色切换不复制别人的关系。
- prepare/generate/send沿用现有来源和权限复核，并核对关系版本；关系或冻结配置变化使旧投影失效，需重新装配或拒绝旧发送。关系版本pin不替代SourceAuthority。
- 只有真实已结算的获准轮次/行为可提交事件候选，候选无可任意设分数字段；模型提议不得直接写分。失败发送、重复消息、转发/引用、机器人自述不得凭空记用户互动。
- 冻结时Memory自动分数不变，但Companion短期情绪/生活继续；解除冻结不回放旧事件，不自动授予发送/工具权限。

新 tests/test_relationship*.py覆盖同人多角色、同群多人物、伪造身份文本、在途撤销、迟到Memory响应、冻结/情绪独立、失败发送和事件去重。来源/共享Core装配变更按AGENTS跑受影响已有与完整组件套件；联合使用固定MemorySHA、隔离TLS及合成渠道/录制模型，缺依赖或未设TLS工具明确skip/blocked。交接 docs/handoffs/TS-115.md，不将隔离测试称真实QQ验证。
