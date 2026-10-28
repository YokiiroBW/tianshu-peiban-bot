# TS-188 书籍订阅与内置源仓库 NAS 发布

状态：**已推送、已部署、实机书库与源仓库验收完成，兼容性和下载观测限制如下**。2026-10-17 发布，2026-10-18 收口。用户授权保留现有配置及视频/音乐订阅，另明确授权与容器 UI 任务协调；本任务为唯一生产发布者，联合候选只切换一次。

## 固定版本与发布方式

- Platform `c843944ee0dcc00de4260890dcde6c0a68fea6d9`，镜像 `sha256:68805c0d11e5c5a3809bbf35e84784595bfed2fc27db9cdf1a263230678ca55a`。包含 TS183 `657a42b`、TS184 `5fbf3fb`、TS185/186 容器 UI 及完整既有音乐/基础设施历史；已非强制推送 Platform main。
- 原线上版本 `425e2b7581a5681915972694ce3c9db30e9f8232`；发布工具基于根 `055d5e1855be5a6978b91a21fad01b619b890bf8`，实现提交 `df4cc7f745e2a4b5af324d17ab0b5840fc8fabec`、`f928031cdb0de21ba7b515ed575226da435c3578` 已推送根 main。
- 复用 `deploy.infrastructure.image`、`resident`、worker、恢复/保留模块和 Dockge 同步。增加精确提交/哈希的依赖差异输入，以及保留已安装基础设施的更新模式；保留设置字节、SSH 引用、tmpfs、原媒体挂载和资源限制，没有另建发布系统。
- 只新增 NAS `/volume1/Documents/天枢书库` → Platform `/media/Books`。新叶目录 UID/GID10001、0755；未修改任何已有目录权限。书籍设置保存 `/media/Books`，自动元数据/Open Library 默认值保留。

## 数据保留与运行验收

38 个 SQLite 数据库完成冷备、独立离线恢复和一致性验证；没有把备份恢复到生产，也没有网关 origin 重签。当前唯一完整冷备为 `/volume2/tianshu-v2-resident-updates/books-20261017-c843944-release/cold-snapshot`；自动保留流程有完成检查点、无 deferred 回执，旧完整备份按既有策略退出保留。

切换后 10 个托管服务运行、guard ready，五个核心服务 TLS live/ready 均 HTTP200；387 个非目标容器身份和配置保持。两个 Dockge Compose 快照备份并同步，未因此再次重建服务。64 个公开网页资源与最终镜像哈希相同、缓存策略为 no-store。容器 UI 任务另完成已登录真实 397 容器分页、资源条和多尺寸布局核验，详情见产品 TS185/186 交接及根工作区 TS187 交接。

显式稳定字段哈希核对：6 个视频订阅、1 个视频账户/目标、3 个音乐账户、43 个歌单及 43 个 Navidrome 关联、音乐设置/维护配置均保持。1956 个音频与 9 个视频任务身份全部保持，数据库版本仍为 2。发布前后观察到的音频状态为完成1434/原失败89/等待重试433，视频为完成6/原失败3；这些既有失败没有被清除，也不宣称队列全部成功。

## 真实网页与成品验收

沿用户自行登录的管理员会话操作，没有读取或导出密码/Cookie。

1. 订阅侧栏直接进入书籍，打开内置 yckceo 仓库，真实列表100项；搜索“中华典藏”返回3项，查看详情并直接添加 `https://www.yckceo.com/yuedu/shuyuan/content/id/6319.html`。
2. 后台自动导入1条规则；再次“立即更新”得到新增0/更新0/未变1。规则订阅 active、间隔86400秒、下次扫描已登记；停用书源后再次同步仍保留停用状态，没有重复书源。未等待24小时来冒称每日定时周期已经完整跑过。
3. 实际搜索“道德经”返回 `book_rule_capability_unsupported`。该规则需要当前未支持的网页交互/App 能力，已停用其参与聚合搜索，保留规则更新订阅；本条不能用于证明远程正文搜索、下载成功。
4. 从真实页面上传公版《唐诗二首·部署验收》TXT，自动分为《静夜思》《春晓》两章；分别打开预览，正文正确。后台整理任务 `completed / book_published / book_completed`，2/2章。
5. EPUB 实际位于 `/volume1/Documents/天枢书库/未知作者/唐诗二首·部署验收 [27227af7]/book.epub`，2615字节、第1版；UID10001在运行容器内成功读取，UID/GID10001、0640。NAS与容器文件哈希一致：`a5a417489e238b2e834042fe0e1007356c5fec310706bb54d23cf3e3707b5aaa`。产品 EPUB 验证、ZIP CRC、OPF两章spine和两首正文均通过。

浏览器下载按钮已经尝试，但自动化未收到下载事件，未取得浏览器下载文件，因此**不记为浏览器下载验收通过**；NAS成品及应用读取已独立通过。聚合搜索/远程章节流程已有隔离实际HTTP测试覆盖，本次真实站点样本因规则能力限制未覆盖成功；外部元数据服务命中、追更新章节和全部书源兼容性也不作实机完成承诺。本次没有接入 Fish Audio、AI 朗读或音文同步。

## 验证与证据

- NAS 隔离 Linux 容器以 UID10001、只读根、无外网且不挂生产数据运行86项，全部通过、0跳过：书籍/仓库20、视频存储23、音乐书库23、媒体后台4、音乐基础16，包含固定解码器及POSIX行为。
- 联合最终镜像与书籍测试镜像545个非前端源/构建文件哈希相同，复用86项；新增容器前端10项全过/0跳过，联合类型检查和构建通过。
- 发布工具适用58项通过：首次57项通过，1项因旧验证解释器缺固定PyYAML6.0.3失败；换用已安装正确依赖的既有解释器仅重跑该项通过。原始环境失败保留，没有改产品来消除它。
- 原 TS183/184 的网页、后端和既有失败/跳过记录保留在各自交接，本次 Linux 适用用例无跳过，不把所有历史测试概括成全绿。

最小机器记录：[books-deployment-2026-10-17.json](books-deployment-2026-10-17.json)。本机原始回执在忽略目录 `.runtime/books-deploy-20261017/`：`release-c843944/remote-poll.receipt.json`、`media-preservation.json`、`final-health-storage.json`、`read-retention.receipt.json`、`verify-book.receipt.json`、`linux-checks-status.json`、`combined-validation.json`、`release-c843944/http-assets.json`、`release-c843944/sync-dockge.receipt.json`。实机截图 `nas-book-library.png`、`nas-book-preview.png`。

根旧分支和混合修改、协调产品既有 untracked build/ 保留。TS188 独立检出只提交本任务发布工具、交接和书籍交付记录，没有替其他任务提交混合根修改。
