# 天枢联合开发工作区

这里是天枢平台、陪伴核心、记忆、模型网关及相关集成项目的统一协调目录。

**当前阶段：开发流程与工作区准备；尚未进入业务实现。** 当前产品依据为 [整体架构 V2](docs/architecture/tianshu-system-architecture-v2.md)，具体开发入口为 [并行开发流程](docs/development/parallel-development-plan.md)。

- [项目清单](workspace.json)：开发检出、旧代码参考、来源与验证入口。
- [任务板](docs/development/tasks.json)：依赖、允许写入范围、当前状态和交付门槛。
- [跨项目契约](contracts/README.md)：先发布最小共同契约，再让消费者接入。
- [本轮交接](docs/development/CURRENT.md)：只保留当前事实与下一步。
- [已认可视觉](output/ui/README.md)：本地视觉文件保留，生产页面尚未实现。

`projects/` 中每个项目有独立 Git；根 Git 只管理统筹文档、契约和工具。并行任务在 `worktrees/<任务编号>/` 中工作，禁止多个编码体同时写 `projects/` 中的协调检出。`references/` 是独立参考快照，不能当现役服务运行。

当前目录中的旧代码副本不会移动或改变原来的开发目录。来源未提交的变更另存参考覆盖包，不自动应用到 V2。第三方运行服务、NAS 数据、模型文件和密钥不复制为开发源码。

检查：`python scripts/workspace.py check`；查看：`python scripts/workspace.py status`。本机 Python 路径记录在不入库的 `workspace.local.json`。真实业务测试命令由各项目首个脚手架任务核定，不把这里的准备检查当业务测试。
