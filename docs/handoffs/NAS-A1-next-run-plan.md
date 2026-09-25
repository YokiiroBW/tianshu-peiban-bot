# NAS-A1 下一轮单次驱动与预演方案（仅架构）

## 适用范围与结论

本方案基于固定恢复工具提交 `07dfbb81bffd814834f169b6b7d061b9c7308512`、[r2h 原始证据索引](NAS-A1-r2h-evidence.json)和 2026-09-25 的一次 NAS 执行。它只定义后续**新 scope**的顺序、边界和验收资料；没有再次操作 NAS、改变产品/恢复代码、降低 180 秒闸门或许可重放。

当前 r2h helper 包不能直接作为新 scope 的“完整 dry-run 后执行”工具：31 个 helper 中 30 个写死 `scope-a1-r2h` 绝对路径，多数在模块顶层直接执行，几乎没有参数化 `--dry-run`。现有恢复测试有合成 Docker/HTTP 替身，但没有 `a1_r2h_*` 助手链集成用例；本轮 19 例本地 dry-run 仅覆盖初始化 guard。现有 `linux-rehearse` plan 和 `drill-clone` plan 都跳过真实 owner/恢复事实及短时来源入场，后者明确返回 `actual_owners_and_facts=not_checked`。因此，当前工具**可以做静态和替身预演，不能在新 scope 创建前证明真实九 owner、Platform 签发、SQLite 冻结事实或六项克隆 HTTP 断言**。若要防止 NAS 上逐错试，需先交 DSH 将 A1 helper 链参数化并增加无副作用的 fixture 模式，再经独立审查；恢复核心工具的安全复核不得因预演而删减。

## 本轮实测预算

r2h 在 14:31:44.960Z 完成最终输入密封；最短 unknown 请求截止为 14:36:29Z，当时共有约 **284.040 秒**。config/actor 产品来源分别到 14:36:43.159696Z、14:36:44.262127Z，产品 TTL 是 300 秒；许可自身 600 秒期限至 14:43:10Z，均不能延长更短的 unknown 截止。工具只在 claim 前固定要求最短剩余 **至少 180 秒**，运行中继续查实际期限。

| 实测阶段 | 单调耗时 | 对预算的影响 |
| --- | ---: | --- |
| 最终 v5 发布、双来源签发与输入密封 | 4.686 秒 | 最短截止从这里开始倒计时 |
| 密封完成到 `linux-rehearse` 开始 | 10.784 秒 | 可预先固定调用以消除交互空档 |
| 九 owner 停写、SQLite 快照、禁用恢复和事实摘要 | 56.724 秒 | 必须真实执行，不能预演替代 |
| 恢复完成到克隆主机预检开始 | 9.201 秒 | 可由同一驱动进程立即接续 |
| 克隆专属主机预检 | 0.648 秒 | 仍需实时重查路由、端口、网络、资源 |
| 预检完成到许可命令开始 | 6.820 秒 | 可预先固定调用以消除交互空档 |
| 许可签发及当前寿命校验 | 1.902 秒 | 返回最短剩余 198 秒，许可未 claim |
| 许可完成到 `drill-clone` plan 开始 | 25.513 秒 | 主要用于查找已知命令格式，应在密封前完成 |
| 完整许可/输入的 plan | 0.797 秒 | 必须在许可签发后执行，不能用空许可代替 |
| plan 完成到 execute 开始 | 9.698 秒 | 同一驱动进程可立即接续 |
| execute 至入场拒绝 | 5.075 秒 | 包括不可省的动态复核；未输出检查点的精确秒数 |

五段人工/工具往返空档合计约 **62.016 秒**。本轮 execute 在 14:33:47.045Z 开始，最短期限尚余约 161.955 秒，随后在 claim 前以 `drill_origin_lifetime_insufficient` 拒绝。只删除许可后的两段空档、**不把前半段也算作收益**时，从实际许可收据的 198 秒扣除 plan 0.797 秒与本次 execute 入场调用 5.075 秒，推算复核点约 **192.1 秒**，仅比 180 秒阈值多 **12.1 秒**；驱动开销和主机抖动还会消耗它。若所有可避免空档都消失，按本轮动态阶段耗时推算复核点约 **218.9 秒**，这是理想排程算术，不能当作新 scope 保证值。

r2g 已完成的五断言克隆 execute 在任务原始会话中的 11:58:04.051–12:00:35.646Z 实测 **151.597 秒**（最终仍因缺 Gateway 独立断言而部分覆盖）。从实际许可 198 秒出发，即使立即 plan/execute，按这一旧运行时长也**至多约 45.6 秒**可留给第六项 Gateway API、额外桥接、主机波动及期限缓冲；r2h 六断言克隆没有实际运行时长，不能宣称足够。更不能把许可 540 秒运行上限误认为产品来源可用时间。下一轮每次只依据新 scope 的**实时时钟、实际来源/unknown 截止和固定 180 秒闸门**决定是否 claim；门槛未过即保留现场停止，不自动续签或重试。

## 单一固定驱动进程

由一个预先审查、固定 SHA256 的 NAS 端驱动进程串行调用现有公开 CLI/助手；它可以启动短命子进程，但不能在中间等待人工找参数或靠多次 SSH 往返。许可仍由**独立 helper 子命令**签发并落盘，`drill-clone` 执行器不替自己造许可，符合 DEP-J 的分步约束。驱动预先固定新 scope 的绝对路径、项目名、Docker/Python 路径、命令模板、输入文件 SHA 与允许的输出字段，动态取得的 registration、verification、permit SHA 只能从上一阶段的结构化回执读取并做严格格式/绑定检查。

```text
在签发短时来源之前（不计入最后寿命窗口）：
  完成新 scope 静态包和所有 source 语义；四 core healthy、五 OBS running、源端 Gateway 四组账本读回；
  生成独立克隆占位输入；采集最终 runtime identity 并登记 authority；
  运行 linux-rehearse plan、两份 Compose config、公开权限/包预检；
  跑完整的只读宿主网段/路由/回环端口/内存门槛；固定并本地预演下列全部命令模板。

驱动开始后，以下阶段连续执行，每阶段落 UTC+monotonic 收据：
  1. finalize_clone_inputs：发布 v5，取得两份 Platform issue 原回执、密封 184 文件和 unknown 截止；
     失败即停止前进，不再签发许可。
  2. linux-rehearse --execute：以已登记九 owner 身份正常停写、备份、恢复到 disabled；
     严格解析 snapshot/verification SHA 和九 owner stopped=9。
  3. 实时只读克隆宿主预检：精确七个 /28、六端口、路由/IPv4/项目名与 >=11.5 GiB；
     拒绝则没有许可和 claim，保存 source/restore 状态。
  4. make_clone_permit：复核冻结恢复事实、184 个文件及双来源实际到期；
     取得同一次许可 ID/SHA 与 >=180 秒入场收据。
  5. drill-clone plan：使用刚取得的许可原字节 SHA；要求 planned 和六断言输入。
  6. drill-clone --execute：只调用一次；工具自身再次检查实时宿主、许可、输入、九 owner
     停机身份、恢复事实、双来源与 unknown 截止，然后才可写 claim/建 clone。
  7. 任意结果后只读收尾核对 source/clone owner、claim、恢复目标和网络端点；
     不自动重放许可，不改冻结 source/backup/restore/inputs。
```

驱动在每阶段记录 `utc_started`、`utc_finished`、`monotonic_ns` 差、阶段退出码、固定命令摘要、非敏感结果字段与原始回执 SHA。私有来源/凭据原文只留在事先创建的 mode 0700 收据目录；脱敏索引不写入 source、backup、restore 或克隆封印输入。CLI 自带 `linux-rehearse --timeout 300` 和 drill 的 540 秒许可预算/90 秒清理预留仍生效；外层看门狗只发受控取消并等待工具自己的正常停机，不能对 Docker 广播 `kill/down/prune`。若停机无法核实，报告 `stop_unconfirmed` 并保留现场。任何失败阶段不自动重试；已 claim 的许可永不重放。source 与 clone 只串行运行，固定 12 段 `/28`、12 回环端口、9 owner 7.5 GiB 限制，`.192/26` 尾部不使用。

看门狗建议上限是密封 30 秒、`linux-rehearse` 内置 300 秒且外层最多观察 330 秒、主机预检 60 秒、许可 30 秒、plan 30 秒；它们是**异常中止上限而非可花掉的来源预算**。execute 仍受许可 `max_runtime_seconds=540` 和内部 90 秒清理预留约束；若外层到 540 秒仍未退出，只向本 CLI 发送 SIGTERM 触发其取消路径，再给 90 秒核实清理。任何外层超时都停止后续阶段、保留原收据并只读审计；不能用 `subprocess.run(timeout)` 直接杀掉子进程而跳过清理。密封若部分成功却失败，先由审过的 exact-owner 停写/备份分支收束运行 source，不进入许可；若该分支无法确认九 owner 状态，报告 `stop_unconfirmed`，不得自行用普通 Compose `down` 或强杀。

## 必须保留在真实执行时的复核

- `linux-rehearse` 必须按登记的容器 ID 与挂载、健康/运行状态复核九 owner，正常 SIGTERM 后确认退出 0，做 SQLite 一致性备份、fsync、禁用恢复与全事实 verification；预演不代替这些状态。
- 许可 helper 必须读取当轮 snapshot/restoration facts、finalized `inputs.json` 与 184 文件哈希，确认版本、九镜像、独立 TLS/凭据、六断言和 clone Compose 与 source 绑定；只读打开**已停机** source Platform SQLite，拒绝非空 WAL，核对 origin ref/entry/digest/撤销/当前 expiry 与 unknown deadline。不能把初始 issue 回执 TTL 或预先算的剩余秒数当入场结果。
- `drill-clone --execute` 在 claim 前仍须重新验许可原字节/到期、注册/运行身份、原九 owner 停止、恢复目标未启动、源/恢复全事实、输入/挂载/资源/项目来源、Docker 宿主资源、克隆项目闲置、文件预算及实际来源寿命；这轮入场阶段合计 5.075 秒，现有收据无法拆出子检查耗时。claim 之后的复制前后哈希、两组启动/健康、worker readiness、六项 HTTPS、Gateway 账本/日志桥和 10 秒 no-resend 观察也必须保留。动态结果不缓存、不跳过。
- 自定义主机预检可在短时签发前先跑一次，停写后再快速重查；执行器的 `check_host` 侧重 NAS 资源配置，不替代明确分配的地址/端口/路由核查。两者都保留。

## 新 scope 创建前的预演边界与交付门槛

| 可在本地/创建前完成 | 无法在创建前真实证明 |
| --- | --- |
| 参数化脚本的 Python 编译、精确路径生成、旧 scope 字符串残留扫描、单次执行 guard、输入文件清单及哈希、初始/最终 manifest 差异、固定产品镜像/提交、12 段 `/28` 和 12 端口不重叠、Compose 渲染、合同/JSON schema、权限模板 | NAS 当时的路由/端口/内存与 Docker 对象、实际九 owner 身份和 stop 结果、Platform issue/续期的数据库行、backup/restore 全事实、一次性许可、六项真实克隆 HTTPS 与有界 no-resend |
| 在临时合成目录与严格替身中跑**整个助手顺序**，模拟成功、初始/最终 manifest 误用、非空目录 guard、缺模型模板、先发布后绑定、凭据未重载、漏传 Docker 绝对路径、超时、部分签发、来源 179/180/181 秒、非空 WAL、重复许可、错误项目/网络；断言失败时不调用后续阶段、不触碰工作区/NAS | 替身结果不能当真实产品 API 或恢复副本断言通过；`drill-clone plan` 只校验静态输入并明确不检查真实 owner/facts |

要称“新 scope 前完整助手链预演”，DSH 至少需交付：一套通过 `--scope-root`/显式参数运行、默认无副作用的 A1 helper；只在 `--execute` 时创建本轮范围文件的写入边界；可注入的 Docker/Platform/Memory/Companion/Gateway 适配器；覆盖上述失败路径的合成夹具；一个输出确切阶段、输入/输出文件与命令摘要的计划；以及由 A4 独立复核的 fixture 结果和包 SHA。**现有 r2h 硬编码脚本与局部 19 例初始化 dry-run 不满足这个门槛**。在该能力到位前，可先做静态本地核对和 NAS 创建前只读宿主预检，但不得宣称“完整预演通过”，也不能通过跳过实时安全校验换速度。

## 决策输入

协调者/A4应分别判断：新 scope 脚本是否已经参数化并完成替身链预演；固定六断言是否具备足够实测运行余量（r2g 的 151.597 秒只覆盖五断言）；单次驱动的失败停止与 90 秒清理窗口是否经过独立审查。若这些条件尚未满足，保留 r2h `needs_validation` 与现场，先交 DSH 补助手链，不拿旧冻结 source 或未 claim 的 r2h 许可做试跑。

长期产品化方向仍是把克隆只读恢复授权与短时 source origin 分开：由受信恢复发行方在禁用态快照及输入哈希核验后签发**快照/authority/clone/端点/用途/时限全部绑定**、单次消费且可审计的能力；Platform、Gateway、Memory、Companion 必须共同验证撤销与到期语义。该能力不能通过放宽现有 180 秒门槛或修改冻结 source 数据伪造，需独立合同、DSH 实现及联合验收。
