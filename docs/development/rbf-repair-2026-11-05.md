# RBF：自动巡检根因修复，2026-11-05

用户已在主聊天批准四项根因修复方案并要求“安排对应窗口修复吧”。本轮为新修复批 RBF，继承 gpt-6.1-terra / max，普通速度 service_tier=default；旧 Fast、旧只读阶段与已完成卡限制不适用于本轮明确范围。只在指定的新工作树写本卡范围；旧聊天原 cwd 和原任务分支保持。所有窗口不运行测试、构建、烟测，不操作生产、真实模型、QQ或其他设备；编写必要回归用例、静态审阅完整 diff、完整实现后提交并交接，由主协调者集中执行必要检查。不能为了省验证而省功能或伪造通过。禁止 reset/clean/stash；共享 product.py、migrations.py、config.py/config.schema.json、runtime/schema.sql、types/、全局依赖/部署配置由主协调者单写，提供明确接线说明。现有配置、新业务数据、原请求/回执/unknown、来源/权限/CAS和纯计算恢复预算保留，本批没有再次清库权限。不要引入平行队列、全局业务状态机或仅按错误字符串盲重放。用户已明确授权主聊天发送任务并接收回执；可向主聊天 01a1a72e-d357-7ba2-9e01-6373ebb7741e 回报接口、阻塞与完成。若消息工具不可用，交接文件和最终回执仍必须完成。

基线：33e5282f60fe4c803c3b16d498d1d924621ed1b9。集成检出：C:/Users/Administrator/.codex/worktrees/rb-integration/tianshu-peiban-bot。原数据重建已结束，本批按现有新数据持续演进。

## 已批准方案

1. 真实业务变化才重算：缓存刷新与语义变更分离，单角色有效在途计算与最新待处理版本。
2. 状态切换与唤醒一致：领域记录是工作权威，队列负责唤醒；当前指针、取消/换代、due与入队原子或有明确可恢复准备态。
3. 事务与资源治理：慢外部操作在事务外，身份/来源/领域行固定锁顺序，同事务连接显式复用；确认回滚后只恢复本地提交。
4. 获取、计算、解析、业务提交分期：只恢复失败阶段，原请求/回执/unknown和费用未知状态保持。

## 当前证据

本地只读证据位于 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rb-execution-20261105/ ：root-cause-readonly-20261105.json、root-cause-triggers-resources-20261105.json、monitor-observations.jsonl及各detail文件。不要将正文或凭据复制进提交。能力expiry即使结果相同也发通知，product.py回调随机UUID被规划fingerprint纳入；一角色连续8份已比较语义分量相同但指纹不同。Memory有batch时dependency_probe_in_progress落入terminal；规划supersede先提交而当前指针未原子切换；视频范围解析错误经无effect Fault被映为unknown。数据库47次PoolTimeout及锁等待/死锁已记录，但历史具体池/阻塞链未完全定位，不能据当前空闲快照或12并发/4连接比例武断定因。

## 协作边界与接口

Runtime先发布明确等待/本地资源失败处置合同；Life/Memory/Growth消费，未收到新接口时先实现本域逻辑与回归构造，不自行改共享文件或发明平行政策。Catalog先发布真实语义变化与缓存新鲜度合同；Life消费，根改product.py。Models先发布状态查无记录的正常/异常语义；Observability接事件，根处理必要共享装配。Sources先核对依赖锁约定，领域自行改内部提交路径。接口用各自handoff或消息回根，由根同步所需窗口。

## 集中验证

全部整块完成后由协调者复用有效既有结果并只运行此次必要检查：缓存到期无效重算、真实能力变化与取消；换代中断后续跑；依赖等待后原材料完成；并发DB争用与连接释放；模型成功后只重试落库；视频非法范围正确分类；统计覆盖和404真实/预期分流。部署及生产写入仅主协调者，任何实际发布都保留新业务数据、配置和原回执。

## 任务

- [RBF-01 运行时等待协议与数据库资源](rbf-work-packages/runtime.md)
- [RBF-02 Life语义触发与原子规划续接](rbf-work-packages/life.md)
- [RBF-03 Memory等待恢复与材料重复归因](rbf-work-packages/memory.md)
- [RBF-04 能力观测语义与媒体阶段恢复](rbf-work-packages/capabilities.md)
- [RBF-05 来源与领域提交锁顺序](rbf-work-packages/sources.md)
- [RBF-06 Models状态查询与原回执语义](rbf-work-packages/models.md)
- [RBF-07 Growth临时资源失败与恢复一致性](rbf-work-packages/growth.md)
- [RBF-08 统计覆盖、事件归因与巡检摘要](rbf-work-packages/observability.md)
