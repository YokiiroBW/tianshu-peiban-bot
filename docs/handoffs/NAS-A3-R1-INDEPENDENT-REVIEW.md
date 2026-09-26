# NAS-A3-R1 常驻候选独立审查

## 结论与固定范围

对协调基线 `a47b70c28e3d71dca80023a98e8b5360694b27e5` 至 A3 最终提交 `8e381646cee06f37a61e80c16e9e2b50cd5984a9` 的差异，及固定 OBS 源提交 `a194fa7b527ac2da0f13b8c3e95e76a1b836b4b3`，完成有界只读审查。**未发现阻断离线 `resident_candidate` 导出的新增代码缺陷。NAS 部署及长期运行仍为 `needs_validation`，不能按此报告标记为验收通过。**

本审查核对公开初始化、OBS 配置、绑定与导出链，覆盖 QA 规则保持、固定源码和九个镜像引用、绝对挂载与私有输入、六张网络、LAN HTTP 与内部 TLS、重启及维护停机语义。只报告影响部署、正确性、数据或宿主安全的具体边界；未连接 NAS、修改实现或代替 A1/A2 联合验收。

## 审查依据

- `deploy/tianshu/configuration.py`、`resource_profile.py`、`bundle.py`、`compose.py` 将 resident 范围约束为唯一项目名、CPU 6/7、内存限制、无 CPU quota/PID 假设、RFC1918 IPv4 与匹配端口的 HTTP origin、显式 core 辅助子网及四套 operator-supplied TLS。`preflight --release` 拒绝 resident；普通 preflight 只返回 `resident_candidate`、`pending_live_acceptance` 和 `release_ready=false`。QA 与 LAN QA 分支仍有原来的独立测试范围；固定 OBS 提交只增加 resident 绑定，保留三网络模板。
- `deploy/tianshu/observability_release.py` 通过公开 OBS CLI 导出固定 Git 对象、配置五服务、记录源码与 manifest 绑定。`resident_export.py` 又逐文件比对 OBS Git 字节与绑定哈希，核九个 `@sha256` 镜像引用、两项目名、六个私有不重叠子网、OBS access 仅供 Guard/Grafana 和两个回环端口、bind `create_host_path=false` 且来源限于声明的 bundle、私有 env 文件固定位置。输出只含两份 Compose 与散列锁，标明候选及尚未验收。
- Core 对外仅 Platform HTTP/IP 端口；内部服务仍使用 HTTPS 及经初始化握手验证的 TLS 输入。OBS `observe`、`storage` 为内部网络，`access` 非内部网络只接 Guard/Grafana。九服务使用 `restart: unless-stopped`；现有导出合同明确维护前对准确 ID 关闭并读回 restart policy，TERM 后等待，而不是把这个离线文本合同当作已执行的停机证明。
- A3 交接记录本地固定 Git 对象的初始化→OBS 配置→导出联合用例 3 项、资源配置 22 项、打包 27 项及 OBS 配置 6 项通过，Windows 测试仅替换 Linux chown。审查 `git diff --check` 无空白问题。未重复执行未变化的成功用例，也没有 NAS、Docker 或容量压力测试。

## 仍阻断实际部署验收的边界

1. **宿主容量与停止路径尚未闭环。** 固定 A2 审计 `256e05aa64714b175acbbb38ef26982c75b92e37` 说明四源各 1 GiB、Vector 4 GiB buffer、Guard 1 GiB reserve 和 80%/85% 告警并不是宿主硬额度或自动停业务。导出锁明确记录 `host_capacity_protection=pending_live_acceptance`。受控首试前须核目标卷总空间及保留余量、实际水位读取与通知、停业务阈值/最晚时点和责任人，并观察停机收据；无人值守运行还需接通并验证自动处置。不能把 A2 合成背压或 Guard 503 当作 NAS 容量保护证明。
2. **目标与九服务实态未核。** 离线导出不能证明 `/volume1/tianshu-v2-resident` 在 NAS 上为空、权限正确、私有文件可由适当 UID/GID 读取且不外泄、镜像 RepoDigest 与锁相符，或 Dockge 实际采用两独立项目及声明的重启/CPU/内存/网络/端口。A1 安装及联合验收须在任何激活前核这些目标条件；三网络 OBS 的回环可达、四源安全事件经 Vector/Guard/Loki 对账、真实维护停机与恢复也仍需实测。旧两网络 A1 成功记录不覆盖新 OBS 源。

上述两项是已知的 live gate，不是本次离线导出新增回归。A3 文档和锁均未宣称其已通过。首个可运行候选仍需 A1、A2 和协调者按各自职责收据化验收，且 release gate 保持关闭。

## 交接

本审查只读比较固定提交与现有交接、A2 审计；未改 A3 分支、NAS、协调主线或产品代码。报告由 A4 独立检出提交，供协调者审查和合入。
