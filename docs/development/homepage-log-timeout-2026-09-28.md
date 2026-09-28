# NAS 首页日志准入超时修复

用户在上一轮部署验收后访问根地址，收到 HTTP 503、`dependency_unavailable`、`execution_state=not_started`。该故障使此前时点的成功检查不能代表持续可用。

现场平台 readiness 只有 logging failed；Docker stderr 为 `tianshu-diagnostics: log output unavailable (log_flush_timeout)`。平台日志目录约 4.7 MB，配置容量 1 GiB，容量监督仍 ready，其他核心服务正常。网关随后 configuration failed，因为平台不可用阻断 origin 续期。

平台运行配置未设置 `diagnostics.durability_timeout_ms`，实际采用默认 250 ms。实现的 durable admission 超时会调用 `_fail`，故障状态不因后续磁盘写入完成而自行清除；`recover()` 明确属于受控维护且需要真实 fsync 证据。单凭当前日志不能区分磁盘延迟、线程调度或 CPU 争用，不宣称已测出磁盘故障。

本次使用已有配置接口将等待上限改为 **5000 ms**（实现允许范围 250–5000），没有修改产品代码、丢弃日志、取消落盘确认或绕开身份授权。故障时仍拒绝开始业务。

## 受控恢复

现场目录 `/volume2/tianshu-v2-resident-updates/connections-20260928/homepage-recovery` 保留原平台配置、容量配置/unit、执行进度。停止容量 unit 使受管十服务准确停止后，修改平台配置；恢复九个原容器的 unless-stopped 和运行状态，官方 CLI 重新签发同一 config-entry 的网关 bootstrap，再仅重建 gateway。随后创建新的容量监督状态并重新 arm/start。

当前容量 state 是上述目录的 `capacity-state`，不是前一轮 `connections-20260928/capacity-state`；十服务 ready，管理员账号文件哈希保持一致。Compose 文件未改变，Dockge 副本仍有效。不要重跑本次单次恢复脚本；旧失败状态和配置备份保留。

## 验证

用户截图中的同一 Chrome 标签页刷新后已显示“工作台 · 天枢”，导航和已有账号登录入口正常。没有代填密码或重建账号。持续 HTTP 验证与最终五核心 readiness、容量结果记录在同名 JSON；不能由这次有限时长检查推断长期永不复发。

最终持续检查 364 秒、121 次首页请求全部 HTML/HTTP 200，页面 JavaScript/CSS 各自 HTTP 200；随后五个核心 readiness 全部 ready，十服务容量监督 ready。独立 Sol 只读复核及 7 项有效测试已归档到 Platform 提交 `88c07ae` 的 `docs/handoffs/CONNECT-B-NAS-LOG-REVIEW-2026-09-28.md`（包含首次测试导入失败的纠正记录）。该修复改变运行配置，不需替换产品镜像。

后续部署应保留 NAS 平台显式 5000 ms 配置。异常落盘超过该边界仍会拒绝业务；自动恢复策略若调整，需要独立定义受控恢复与故障告警边界，不能通过简单吞异常解决。
