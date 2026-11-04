# RBW-experience：日记创作与语义经历

先读AGENTS.md最新RB授权与[公共合同](../rb-contracts-2026-11-05.md)。完整需求以第六版队列为准，最新配置保留/全部天枢业务清空覆盖旧数据保留条款。

## 文件边界

- `src/tianshu/life/diaries.py`
- `src/tianshu/life/diary_*.py`
- `src/tianshu/life/writing.py`
- `src/tianshu/life/writing_*.py`
- `src/tianshu/life/generation_control.py`
- `src/tianshu/life/publications.py`
- `src/tianshu/life/rb_experience.sql`
- `tests/life/test_rb_diary*.py`
- `tests/life/test_rb_writing*.py`

不写Life service/worker/module/schema/day_plan，补充集成需求给协调者。独立日级请求、真实语义经历、正文已保存只对账、手动自动同对象、创作章节和发布读者授权均保留。消费失败不影响Memory/Growth，先用公共模型submit/observe协议分阶段。

公共product/migrations/config/schema/types和部署根目录由协调者写；确需改变时回报精确接口/代码片段，不改他人文件。你可新增职责明确的本域文件；shared path白名单优先，不创建平行系统。

## RB-15 语义经历与独立日记闭环

### 交付要求

- 实际 action result 与想象生活分型；discovery/read/失败/未知图片区分，技术补取不生生活经历
- 角色+当地日期+稳定作者场景标识 diary day，不随 scope 哈希变化复制日期对象
- 从当天有效语义经历构造独立 200000 输入请求，无前日、聊天、失败半截上下文串入
- 只要正文已可靠保存，用原回执对账派生状态；实际无材料可以合法不写
- 日记与创作作品/章节各保留手动请求、生成、取消、对账、修订、审阅、发布及已发布读者授权；共用可靠生成/回执能力，按对象/版本分开，不能为独立日记删除创作链路。

### 移除或替换

- 删除每个微小虚构休息事件都当独立高价值学习材料的全量重复处理
- 取消对旧数据库歧义日的永久兼容迁移；新数据的来源版本/正文与回执必须保真

### 最终集中验收要覆盖的行为

- 同日唯一已保存正文只更新正确派生状态；多候选场景不能猜作者或覆盖正文
- 旧日期失败不阻塞后一天；首次深夜启动不误闭日
- 日记/反思不递归喂给自身成长；25 条想象时段不会显示为读了 25 次内容

## 完成回执

提交本工作包全部实现和必要测试代码，写`docs/handoffs/RBW-experience.md`：基线/提交、已完成条目、公开接口与DTO、DDL及注册顺序、共享装配需求、未完成和未执行验证。不要跑测试/构建/烟测。不能把假实现、未连接的helper或只写文档视作完成。以完整功能块收口后再交协调者，不对每个小改动发验收请求。
