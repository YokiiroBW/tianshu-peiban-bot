# RBW-logs：日志后端与持久链

先读AGENTS.md最新RB授权与[公共合同](../rb-contracts-2026-11-05.md)。完整需求以第六版队列为准，最新配置保留/全部天枢业务清空覆盖旧数据保留条款。

## 文件边界

- `src/tianshu/runtime/events.py`
- `src/tianshu/runtime/logging.py`
- `src/tianshu/runtime/diagnostics.py`
- `src/tianshu/runtime/rb_logs*.py`
- `deploy/core/observability/`
- `tests/runtime/test_rb_logs*.py`

只日志后端与原诊断API，前端归frontend包；supervisor输出改动通过补丁需求交runtime包/协调者，不同写supervisor.py。完整日志保留，非阻塞批量落盘/缺口可见，准确级别和源端完整续页；不凭opaque cursor假称上游完整。现有system/logs入口保持，system统计由operations提供。

公共product/migrations/config/schema/types和部署根目录由协调者写；确需改变时回报精确接口/代码片段，不改他人文件。你可新增职责明确的本域文件；shared path白名单优先，不创建平行系统。

## RB-24 独立日志页与不阻塞业务的日志链

本卡仅实施上方边界属于本工作包的部分；其余部分由同号卡其他包完成。

### 交付要求

- 后端复用本地日志/Vector/Loki；业务事实与外部回执先在领域事务保存，日志收集批量异步，不由Loki/监控查询可达性决定业务准入。
- 完整日志不采样不静默丢弃；本地缓冲、补传、磁盘容量、覆盖缺口和采集状态明示；资源真正耗尽按受影响写域处理。
- 保留原始level、module、role、trace/job/attempt、error_family与依赖引用；敏感值在存储/导出/送Agent之前脱敏。
- 前端增加独立日志导航：时间/级别/模块/角色/关联ID检索、排除对话事件、自动跟随/暂停、关键词、详情与脱敏导出；虚拟列表保持大日志可用。
- opaque分页游标处理同时间戳边界、去重和迟到事件；无新日志、断连、未配置、查询不完整分别显示。
- 错误详情可跳转原任务、模型尝试和运维事件；日志页不负责直接改数据库。
- 日志续页不能只包装opaque cursor：固定查询范围/过滤条件和原event_id、instance_id、sequence，验证上游同时间戳超过limit的可枚举性；必要时按来源有界分片或从现有完整持久日志补取，不能凭timestamp加减或无限扩大limit宣布完整。
- 复用原追加日志的sequence与采集checkpoint，区分已生成/已持久/已采集。正常退出排空；进程强退未落盘范围标为可能缺口，业务结果以事务回执为准。trace/job/attempt保留在日志字段或兼容的structured metadata，不作为无限增长的索引标签。

### 移除或替换

- 替换stdout所有级别均INFO、只按timestamp减1翻页、每次业务开始必须等待日志fsync的路径。
- 不新建第二套日志平台，不把聊天正文、完整提示词或密钥默认复制到平台日志。

### 最终集中验收要覆盖的行为

- 原WARN/ERROR在筛选和统计中正确；超页数量同时间戳日志不漏不重。
- Loki断开、writer慢、可恢复日志写入故障不会导致无关业务入口全503；采集恢复能补传并声明缺口。
- 并发大量日志时页面不无限增长；暂停不丢查询位置；管理员看到明确数据覆盖和采样时点。
- 完整日志要求与容量边界一致，不能以偷偷采样/删除未采集片段获得测试通过。
- 同时间戳超过服务端页限、跨来源、迟到补传与断线续页在实际支持的查询后端不漏不重；不能只mock一个理想cursor。强退后不伪称完整日志已持久。

## 完成回执

提交本工作包全部实现和必要测试代码，写`docs/handoffs/RBW-logs.md`：基线/提交、已完成条目、公开接口与DTO、DDL及注册顺序、共享装配需求、未完成和未执行验证。不要跑测试/构建/烟测。不能把假实现、未连接的helper或只写文档视作完成。以完整功能块收口后再交协调者，不对每个小改动发验收请求。
