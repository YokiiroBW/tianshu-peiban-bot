# NAS-A4 resident 实机验收（2026-09-26）

## 范围与固定输入

在协调者明确放行九服务启动后的低影响窗口内，对 NAS `YokiiroNAS` 的固定候选做一次集中验收。代码协调基线为 `c75776de3fdf9d604ded59e33e7e879dec844e00`，OBS 来源为 `a194fa7b527ac2da0f13b8c3e95e76a1b836b4b3`；两个项目为 `tianshu-v2-resident` 与 `tianshu-v2-resident-obs`，Platform 固定容器 ID 为 `4572a934aa570a064426ddf1f6eb3d566f0329f6cd359ca484b9eee64a4feddb`。导出锁 SHA-256 为 `ca75ccfc482da21ffbbbba6d6b1a4e9377cd6612ddc0c9d4ef016afa855b3a13`。源日志窗口为 `2026-09-26T03:09:20.264548Z` 至 `03:12:03.052160Z`；原授权来源到期时间为 `03:14:56.445648Z`。

原始脱敏回执仅保存在 NAS 的 mode `0700` 目录 `/volume2/tianshu-v2-resident-tooling/evidence/acceptance-20260926/`，各文件为 mode `0600`，执行脚本原字节归档于其 `scripts/` 子目录。脚本在本机私有 `.runtime/nas-resident-a4-acceptance-2026-09-26/` 准备并按 SHA-256 对照传输；巡检使用 Docker Go template 的最小字段投影，不读取全宿主容器的环境变量。没有直接写入或篡改源日志、修改容器或产品配置；没有模型调用、故障注入、重启或清理。

## 结果

| 核验 | 结果 |
| --- | --- |
| 九服务与两 Compose / Dockge 保存副本 | `01b-static-retry.json` 于 `03:17:55Z` 通过：九个固定 owner 全部运行、四核心健康；六个网络、镜像 RepoDigest 与 image ID、first/final Compose 来源标签、导出锁、bind、端口、资源和权限匹配。此时已超过原授权来源到期时间。保存副本字节匹配不表示 Dockge 创建过运行容器。 |
| 可观测性 TLS | 首轮 `01-static.json` 于 `03:17:03Z` 唯一失败码 `observability_tls_reachability`。随后的独立分点检查使用同一 CA 和 `127.0.0.1` TLS 地址，Guard `/ready` 与 Grafana `/login` 均返回 200；同一巡检复查通过。首轮具体异常未捕获，原因未证实，回执保留。全程未关闭证书验证。 |
| 管理员 Web | `02-web-login.json` 于 `03:18:06Z` 通过：一次首页、会话、管理员登录、鉴权 cookie 与退出确认。密码只从 NAS mode `0600` 文件读入，回执无密码、cookie 或 token；未请求对话或模型。 |
| 四源日志 | `03-log-reconcile.json` 于 `03:18:18Z` 通过：Platform 21、Companion 5、Memory 3、Gateway 3 条真实源事件，共 32 条。每条按 event ID 经 Guard 鉴权 TLS 查询 Loki，且 `service`、`instance_id`、`event_id`、`sequence`、`timestamp` 一致；未写入源日志或推送合成事件。身份集合 SHA-256 为 `cc0f7c5340dadff7e7ff478fa4ca09bf640ea404c730cf9129ea9df6747cde20`。 |
| 容量守护 | `04-capacity.json` 于 `03:19:43Z` 返回 `ready`，九服务、心跳年龄 3.175 秒、最小可用 `468537327616` 字节、部署树 `7329467` 字节。unit 为 `Type=notify`、`WatchdogSec=30s`、`ExecStopPost=fail-close`，`active/running/enabled`；配置的空闲下限与树预算均为 `21474836480` 字节，轮询 5 秒、TERM 上限 120 秒。 |

回执 SHA-256：`01-static.json` `db8119721d24fe2e2cc9fd6975e1dc6f6e15fd52d6210ccfc4064f62aff946a7`；`01b-static-retry.json` `fa1e3119cfe1db210e409fd77e10f8145758d9a76aa7ebe43e112133e4ee3e98`；`02-web-login.json` `e469df55f5751a5b7a4faff17ec511cbac6cd79879320197cb12489db5e62d2a`；`03-log-reconcile.json` `d79b00a5685cbee960cd8d42f8a8920b36091f4bd599a28c948bbec1cf17ba84`；`04-capacity.json` `134817a7d784d99888f4526a5c2c986673416efc4c27b4fbd1322ce2c3eef0bf`。

## 判断与边界

本次低影响验收的九服务静态状态、Web 鉴权、四源日志链和容量守护当前状态通过；首轮 TLS 可达性短暂失败仍需在后续自然运行中留意，不能据此推断证书或服务根因。容量守护的真实停机故障路径、物理 ENOSPC、30 天留存及长期负载未在此窗口触发验证。`release_ready=false` 保持不变；总协调者负责合并其他任务证据并作最终状态判断。
