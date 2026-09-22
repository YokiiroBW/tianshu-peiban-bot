# NAS-01 四核心隔离资源适配

- 基线：协调根 `8a453cb62c3cec64f6880b15461d54b0e1f037c7`；独立分支 `codex/nas-resource-profile`。
- 新增显式 `nas-cpuset-qa-v1` 输入和资源策略模块，四核心及其一次性容器使用指定两颗 CPU、保持 1 GiB 内存；构建使用同 CPU 集合/2 GiB 内存。记录 PID 控制器不支持，不冒充硬限额通过。
- 仅接受回环访问、隔离测试 TLS、QA 项目，正式 release 检查拒绝；默认 portable 配置保持。五日志 profile 暂未验收，配置入口在写入前明确拒绝，避免无意运行混合配置。
- 实际 Docker 能力在容器修改前核验，HostConfig 在创建后、启动前及生命周期检查中核验；能力缺失与内存限制未生效反例都不启动容器。
- 验证：packaging 完整 79 项通过、0 skip；新增三项生命周期回归后资源专项 11 项通过。合计 82 个不同用例；这些是本地/模拟 Docker 验证，不是 NAS 四核心启动证明。实际 NAS 上前一轮四产品镜像构建成功独立记录，不能回填到此代码。
- 后续：固定新工具版本、candidate/tag/scope；公开 synthetic_init 加 `--resource-profile`、只读 plan 后，交管理员执行权限准备与核心 liveness。五日志/恢复 profile 和正式使用仍待后续实现验证。
