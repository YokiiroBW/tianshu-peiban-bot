# RBW-sources：身份来源与失效传播

先读AGENTS.md最新RB授权与[公共合同](../rb-contracts-2026-11-05.md)。完整需求以第六版队列为准，最新配置保留/全部天枢业务清空覆盖旧数据保留条款。

## 文件边界

- `src/tianshu/identity/`
- `src/tianshu/sources/`
- `tests/identity/test_rb_*.py`
- `tests/sources/test_rb_*.py`

保留原权限上界/当前交集、来源AND/OR及同事务证明；原件及附件补充的来源合同配合Conversation，程序不授新权限。分角色来源通知不回滚原事实；新历史和独立能力都受来源失效。域SQL自行写，公共迁移注册交协调者。

公共product/migrations/config/schema/types和部署根目录由协调者写；确需改变时回报精确接口/代码片段，不改他人文件。你可新增职责明确的本域文件；shared path白名单优先，不创建平行系统。

## RB-02 稳定场景、来源与分型证据

### 交付要求

- 使用稳定 scene_id，权限集合及授权 epoch 独立版本化；内部引用用明确类型
- 单一来源直接引用；多来源才用规范化 evidence set，区分 required、alternative support、generation dependency
- 提供同事务来源证明、批量证明与当前源状态查询；授权刷新仍受原 scope/permission 上界约束
- Sources 与 Identity 不反向依赖 Memory/Companion/Life
- 对保留模块的主体/scope/source及JSON嵌入引用保留最小完整集合；身份主键和已签发引用不能因BOT清空而变成悬空。
- 来源反向定位覆盖新版Episode语义历史、Companion学习历史、词义/样例与搜索投影；同步失去读取资格，异步清理不影响撤回生效。新进程/新任务沿原授权上界与当前权限交集执行。

### 移除或替换

- 删除 scope 哈希充当业务对象身份和多处复制来源展开列表的实现

### 最终集中验收要覆盖的行为

- 同事务新建回执可验证；别连接尚不可见不能把真实成功变成目标 0
- 来源撤回后立即失去使用资格；AND/OR 及多主体归属不退化为一个扁平集合
- 全局管理员订阅 actor_id=null 仍能合法刷新授权；一个源失权只影响该源

## 完成回执

提交本工作包全部实现和必要测试代码，写`docs/handoffs/RBW-sources.md`：基线/提交、已完成条目、公开接口与DTO、DDL及注册顺序、共享装配需求、未完成和未执行验证。不要跑测试/构建/烟测。不能把假实现、未连接的helper或只写文档视作完成。以完整功能块收口后再交协调者，不对每个小改动发验收请求。
