# DSH接管

2026-09-14用户明确：DSH负责所有产品实现，Codex负责架构、验收和排错。当前三个执行卡见dsh/TS-070.md、dsh/TS-042.md、dsh/TS-015.md。共同规则见dsh/EXECUTOR.md。交付在各工作树.runtime/dsh-delivery，协调统一读取；不要求DSH越沙箱写根output。

当前DSH桌面服务已通过真实RPC读取/控制，端口属于本次运行，重启后需重新发现。保留DSH配置模型，不改全局DSH配置/权限。测试沙箱临时目录故障由协调处理，不允许变更业务测试来掩盖。
