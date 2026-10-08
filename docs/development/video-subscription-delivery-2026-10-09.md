# 天枢视频订阅本地集成交付

用户授权的 B 站首期视频订阅已完成前后端开发及本地系统接入，三个 gpt-6.1-sol/xhigh 开发者在独立工作树并行，协调者串行集成。入口是「资料与资源 → 订阅与下载」`/#/resources/2`。未推送、合入产品 main、部署 NAS、执行生产迁移或使用个人账号；本记录的完成范围是可运行的本地集成候选。

## 实现与复用

- 账号：扫码状态、本人的 Cookie 导入、加密存储、校验和撤销；网页复用现有登录、CSRF、会话失效处理。
- 链接与订阅：分 P、真实引擎质量探测、指定清晰度及显式降级；收藏夹、合集、系列、UP 主四类来源，链接/ID规范化，历史或仅新增、规则试算、暂停恢复、完整基线与新增 CID 检测。
- 执行：持久 SQLite 台账、租约与版本保护、有限退避、取消和重启恢复，已完成 P 校验后复用；下载由固定 yt-dlp 进程执行，以 ffprobe 验证成品。
- 发布：复用 TS090 元数据/NFO、TS098 规则、TS099 成品预检；Platform 只生成完整暂存包，AssetLibrary 唯一写最终媒体库。支持首次发布、新增 P、元数据修订、原库清晰度升级、索引失败恢复和发布回执。
- 体验：原生网页、历史任务分页、中文状态、元数据补全、任务中心；桌面/平板/手机布局。Emby/Jellyfin 分别核对全部 CID、路径、标题、简介、作者和实际封面，配置的服务器全部通过才显示完成。

订阅从首包即使用稳定的 multipart 剧集布局，只有一个 P 也如此。手动单视频可用 single；已有手动 single 不会被偷偷改成订阅目录，跨布局迁移未实现。规则更改不默默导入历史，明确质量策略变更可生成受归属保护的新版本。

## 固定版本与运行入口

- Platform 集成：`worktrees/video-subscription-20261009/integration`，分支 `codex/video-integration-20261009`，提交 `44de324`。基线 `6a2ed3d` 包含已部署 `c1d717c` 及后续本地改动，不能把整个基线称已部署。
- 后端来源 `327f568`；前端来源 `519c880`、`a4d68b0`；集成依赖/锁 `50681f8`，实际资产联合脚本与配置示例 `6cc42b2`。
- AssetLibrary：`worktrees/video-subscription-20261009/assetlibrary`，分支 `codex/video-publish-20261009`，最终含交接提交 `7742d1edb368a84d1032bc58e750439ec9deb7ab`，代码提交 `4e3067e55021acfaac7b66a2b3ff6303287f799e`，基线 `54b2beb`。保留独立产品迁移版本24–54，新增55–58，没有空迁移填号；旧SQL哈希未改。迁移工具和Host使用一致的保留版本规则，只在本轮全新隔离库应用27条实际迁移。该独立产品交接见 `assetlibrary/docs/handoffs/MEDIA-PUBLISH-20261009.md`。
- 工作区合同 `contracts/media-package/v1` 已通过本地运行联合验收，旧 candidate-v1 保留，生产发布授权仍为 false。
- 平台运行说明：`integration/docs/platform/media-runtime.md`；API：`integration/docs/platform/media-api.md`；配置片段：`integration/deploy/platform/media.settings.example.json`（以上路径均相对本批 worktrees/video-subscription-20261009）。需要替换可信服务地址、媒体库映射和专用凭据，保留 SQLite/key/staging 同步备份。

## 实际验证

| 验证 | 实际结果及边界 |
| --- | --- |
| 最终集成 TypeScript / Vite | 通过；既有 renderer 541 KiB 提示保留 |
| 合同构建输入 | 既有 snapshot_contract.py 对固定manifest SHA及所有成员验证并复制原始字节通过，未改构建工具；不等于Linux镜像构建通过 |
| 最终集成真实平台 HTTP + 浏览器 | 桌面/手机 4/4，38.1 秒；B站/资产上游在此浏览器套件中是合成替身 |
| 独立真实服务整链 | Platform HTTP/SQLite → AssetLibrary HTTPS/PostgreSQL/文件系统 → Emby 4.10.1/Jellyfin 12.2，两 P、资产 entry_id 和两服务器 verified，最终 completed；B站及视频内容是明确合成夹具 |
| B站实际公开接口/固定引擎 | 匿名公开样本解析和 metadata-only质量80/64/32/16通过；没有登录真实账号或下载该公开视频 |
| 后端专项 | 14项分轮有效通过；纯媒体164项；既有任务中心19项分轮通过；WebConsole8项；Ruff通过 |
| 前端专项 | 三尺寸45项首轮44通过，测试定位修正后相关3/3；二维码修复相关6/6，StrictMode1/1；既有任务中心8/8 |
| 资产专项 | Release Packaging64/64；Transfer136通过、3跳过（2个既有Windows符号链接限制、1个缺少PG环境），其中PG用例随后独立实际通过；迁移工具23/23和verify_repository通过。实际HTTPS11类场景及4个indexed owned版本；合法2MiB请求/1MiB manifest通过，超限拒绝；rename后撤ACL的恢复实际通过 |
| 独立媒体库验证 | single/multipart × Emby/Jellyfin四种组合全部通过；实际取图并允许服务端转码差异 |

已复现的既有测试问题：资产页1项退出断言和应用壳4项旧标题断言在固定基线同样失败，未扩大本任务修改；应用壳另1项条件跳过。后端 runtime_health 的6项unit分轮通过，81项TLS测试因未配置 TS013_TLS_PYTHON 跳过，不宣称该套完整通过。上述分轮结果不改写成未经执行的新全量结果。

实际整链证据见 [real-service-chain.json](video-subscription-evidence-2026-10-09/real-service-chain.json)，资产 HTTP 原始观测见 [asset-http.json](video-subscription-evidence-2026-10-09/asset-http.json)，最终手机页面见 [mobile.png](video-subscription-evidence-2026-10-09/mobile.png)。前后端精确用例和失败修正记录见各自 `docs/handoffs/video-subscription-*-2026-10-09.md`。真实整链原执行使用 Platform `a2c132a`；其后平台仅加入复用脚本/说明及前端QR修复，后端执行代码未变，最终浏览器套件使用 `44de324`。资产整链之后的请求大小和权限撤销恢复修改采用各自实际HTTPS/PG专项验证，未把旧整链运行说成对最终所有代码重新运行。

## 尚未执行与运行限制

未构建或运行 Linux 容器，未验证 NAS 文件系统断电行为，未做生产部署、真实 B站账号扫码/个人Cookie或实际上游下载。Windows 本地进程、合成媒体和公开只读探测不能替代这些验收。所有本次自启测试媒体服务在取证后关闭，不影响 NAS 服务。

独立 AssetLibrary 源主线已发展到 schema54，与本轮稳定天枢基线分叉；后续合入其主线时需对齐迁移账本和已有 OperationTrash persistence，不能直接覆盖源主线manifest。本轮隔离PG使用fsync=off，验证的是逻辑恢复而非断电耐久性；Linux原生目录句柄路径尚未实机执行。

旧版本更新采用受控旧树备份再新树归位，两次 rename 之间可能短暂没有最终目录，不宣称原子交换。旧版本和下载暂存保留以支持恢复及增量，无自动历史清理。账号恢复后需在网页恢复订阅或重试任务。五平台扩展未实现，不标为可用。

根原有修改全部保留；只提交本轮明确所属文件。旧工作区分配器的 TS016 Git所有权故障另属环境历史，本轮使用固定 SHA 的独立 git worktree，不改全局 safe.directory。
