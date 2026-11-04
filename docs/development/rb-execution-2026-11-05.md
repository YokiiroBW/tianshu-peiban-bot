# RB第六版并行执行记录 2026-11-05

用户已明确批准第六版26卡开发，要求主聊天统筹、代码能并行的全部交给新聊天并行，使用gpt-6.1-terra / max / Fast；主聊天最后集成验收。用户同时明确选择：“清空天枢全部业务数据和自有文件，仅保留配置”。这覆盖旧方案保留独立媒体/项目业务数据的约定；天枢的全部功能仍须保留。其他NAS服务、外部共享资源和非天枢所有文件不在删除范围。

主协调聊天：01a1a72e-d357-7ba2-9e01-6373ebb7741e。用户明确允许协调者发送任务并接收回执；正式原话可通过read_thread在主聊天核验。实现聊天可向主聊天回报接口、阻塞和完成结果；主聊天用wait_threads/read_thread回收，回执工具不可用时仍写handoff并给final，不因此中止代码实现。

本批授权覆盖下方旧的只做规划、DSH执行、旧模型/人数及不部署限制。各新聊天只实现分配功能、编写必要回归用例、静态审阅diff并提交；不要逐小改动运行测试、构建、烟测或新审批门禁。全部功能收齐后由协调者集中执行必要验证，变更后仅补受影响检查。不得以效率为由省略功能、写占位成功或自行将缺项当完成。

一个窗口一个独立worktree和codex/分支；只写卡内范围，默认协调目录既有混合修改保持。共享product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局pyproject/lock/deploy根由主协调者单写；域SQL/模块注册在所属窗口。禁止reset/clean/stash他人改动，禁止跨窗口修改对方工作树，禁止自行合主线/推送/操作生产。

代码实施按 docs/development/rb-execution-2026-11-05.md、rb-contracts-2026-11-05.md 和本窗口RBW卡；历史第六版仍是完整需求来源，执行覆盖以本条最新用户要求为准。整块完成后提交全部本卡代码/必要测试与docs/handoffs/RBW-<包>.md，回报准确SHA、接口/迁移/装配需求和未验证项。所有生产读写、配置导出、清理、构建发布、部署核验和最终半小时自动检查由主协调者串行完成。


## 13个代码并行包

| 包 | 目标 | RB范围 |
|---|---|---|
| [RBW-runtime](rb-work-packages/runtime.md) | 运行时与故障隔离 | RB-03、RB-05、RB-22 |
| [RBW-models](rb-work-packages/models.md) | Models异步执行与计时 | RB-04 |
| [RBW-sources](rb-work-packages/sources.md) | 身份来源与失效传播 | RB-02 |
| [RBW-memory_write](rb-work-packages/memory_write.md) | Memory写入历史与共享整理 | RB-07、RB-08、RB-17 |
| [RBW-retrieval](rb-work-packages/retrieval.md) | 检索与索引投影 | RB-09、RB-07 |
| [RBW-growth](rb-work-packages/growth.md) | Companion学习与表达样例 | RB-10、RB-17 |
| [RBW-life](rb-work-packages/life.md) | Life日程与后台执行 | RB-11、RB-13、RB-14 |
| [RBW-experience](rb-work-packages/experience.md) | 日记创作与语义经历 | RB-15 |
| [RBW-conversation](rb-work-packages/conversation.md) | 对话接入与上下文表达 | RB-06、RB-12、RB-16 |
| [RBW-capabilities](rb-work-packages/capabilities.md) | 独立能力与数据边界 | RB-18 |
| [RBW-logs](rb-work-packages/logs.md) | 日志后端与持久链 | RB-24 |
| [RBW-operations](rb-work-packages/operations.md) | 运维Agent、统计与宿主通知 | RB-23、RB-25、RB-26 |
| [RBW-frontend](rb-work-packages/frontend.md) | 前端接线、日志与统计页面 | RB-19、RB-24、RB-25 |

协调者承担RB01公共合同、共享入口接线、RB20配置种子/全新安装/旧版清理部署、RB21集中验收。工作包不是新增功能卡；原26卡每条仍需关闭。

主集成工作树：`C:\Users\Administrator\.codex\worktrees\rb-integration\tianshu-peiban-bot`。运行记录`rb-dispatch-2026-11-05.json`是线程/提交状态唯一入口。各实现窗口独立工作树，当前无生产变更。配置/清理状态和部署实证将单独写回该记录，不能把已派发或已提交称已上线。
