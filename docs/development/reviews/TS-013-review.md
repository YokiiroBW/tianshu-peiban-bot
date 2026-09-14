# TS-013 首轮协调复核

2026-09-14，交付b2cd85eaa2d267bcb5525d4f90e642f15bfe9a36，暂未合入。

协调审查TLS传输/服务入口、SQLite迁移与备份，独立审查来源/回填授权尚未确认阻断。完整diff与ruff通过。显式设置合同、集成网关路径、TLS工具Python及禁止字节缓存后复跑backend：45项中44通过、1失败，无跳过。

失败：test_stale_process_expired_entry_and_migration_backup在到期瞬间仍得到actor-a allowed。测试用原time.time浮点加5，而配置经utc字符串微秒舍入；疑似测试时间未等于实际序列化边界，尚待工作者确认，不能直接当产品已通过或降低断言。要求从真实配置解析边界测试前/精确/后，若产品有错修根因，修复提交后再验。

其余实跑包含旧平台/网关子链、新来源用例和4项TLS；Core仍合成HTTP owner，Memory合成调用方，不是三产品L0。等待修复和独立收尾审查。
