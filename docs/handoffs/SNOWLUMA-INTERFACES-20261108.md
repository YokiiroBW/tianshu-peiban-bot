# SnowLuma 统一接口交付

用户已授权推送部署。开发入口见 [接口文档](../development/snowluma-native-interfaces.md)，
README 已提供链接。发布正在进行，线上事实在完成后补记。

实现复用现有 Actions、Expression、Channels、FileStore 和插件 capability 端口，
不新增业务队列或数据库迁移。固定上游 `f2ea3354f8bbe8672cbb4ae6a2b4e1fbeff44faf`：
222 个动作、6 个别名；213 个动作有执行路径，9 个上游占位动作明确拒绝执行。

59 个不同定向用例通过，复用未变有效结果；全部 222 份输入/输出 schema、223 份官方示例参数、
目录字段完整性及 wheel 资源打包已检查。管理页既有 3 个 execute mock 签名错误已在原代码复现。
未执行真实模型或 QQ 消息测试，不能把框架覆盖当作全部动作的真机验收。
