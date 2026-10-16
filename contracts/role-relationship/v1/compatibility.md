# 兼容决定

首次发布1.0.0、版本域role-relationship/v1。五DTO的$defs及oneOf与candidate-v1完全相同；只改变schema的$id/title以建立正式身份和哈希。candidate包保持历史字节。

新增正式外层schema说明已经实现的HTTP、managed/check/history及Fault边界，不新增权限、写入类型、表或自动迁移。candidate_schema_path是保留的配置键名，其值必须显式指向正式包；没有候选自动兼容路径。Companion旧candidate版本pin不能被本版本接受，按既有流程重新准备。没有已部署关系数据需要兼容的事实；生产Memory首次迁移仍需现场前置确认。

后续修改必须保持发布字节不可变。破坏性DTO、权限或语义改变需新主版本/明确迁移；非破坏版本须固定哈希并重新验证受影响生产者/消费者。
