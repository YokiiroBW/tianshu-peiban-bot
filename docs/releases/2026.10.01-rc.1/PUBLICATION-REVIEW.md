# 公开发布内容与许可检查

2026-10-01，用户决定六个新仓库公开。本轮只检查指定协调仓库和六产品的拟发布可达历史，不读取 Codex 旧会话、凭据存储、生产 DB 或聊天原文。保留原本地历史与现有未提交修改。

## 已执行范围

拟发布源版本共 1,708 个可达提交、11,934 个 blob。Git 原对象检查涵盖文本敏感模式和数据库、私有配置、运行目录、归档/权重及第三方路径；非文本资产按类型和出处另核，不把模式检查称为完整安全审计。报告记录路径、行号和 blob ID，不保存命中的敏感值。未发现可达数据库、真实运行配置/凭据文件、聊天会话存储或模型权重路径。所有 201 个模式命中均在测试路径：

- Platform：1 个浏览器 onboarding 断言中的密码常量，使用 route.fulfill 的合成会话和请求。
- Companion：4 个历史 blob 的 private-key 标记属于日志脱敏测试文本，没有完整可用私钥。
- Gateway：2 个历史 blob 包含完整的固定自签测试 TLS 私钥。文件开头明确声明合成、临时本地账本、无 NAS/真实账号/外联；源码引用均位于 tests。证书 subject 为 CN=tianshu-ts103-fixture，issuer 相同，SAN 只有 localhost 与 127.0.0.1。这是可公开测试材料，不是生产凭据；不得用于部署、外部信任或真实账号。
- AssetLibrary：4 个命中来自包装/试用身份测试常量；不属于客户端实际凭据存储。
- Chat Audit：190 个历史命中均属于密码哈希迁移或应用工厂的接受/拒绝生产配置测试函数；生产模式标志是在临时测试环境验证边界，不能据此认定真实生产密钥。

公开前仍需以最终发布 commit 核对新增文件和上传 refs。禁止 --all/--mirror、上传 ignored 工作区或把模式扫描零高危命中当作凭据永不存在的证明。

## 第三方材料

AssetLibrary 的官方控件/示例材料保留 Microsoft MIT 许可；扫描可达路径没有 ttf/otf/woff 字体文件。Windows SDK/WebView2/WinAppSDK 等部署依赖的完整 notices 和锁定版本审查存在于 infra/windows-client/notices/。本次只是原样源码发布，没有新分发 Windows SDK 二进制或重定产品版本，不宣称所有后续二进制打包都已核准。没有自行给用户代码添加 MIT 或其他开源许可。

Chat Audit 的 vendor/wheels/imageio_ffmpeg-0.6.0-py3-none-manylinux2014_x86_64.whl：SHA256 c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282，与 [PyPI 0.6.0 官方文件记录](https://pypi.org/pypi/imageio-ffmpeg/0.6.0/json) 完全相同；现有 GitHub 远端也已包含相同 blob。Python 包保留 BSD-2-Clause 许可，但内含 ffmpeg-linux-x86_64-v7.0.2 的二进制，检测到 --enable-gpl，不能只按 BSD 批准整个 wheel。[FFmpeg 官方许可说明](https://ffmpeg.org/legal.html) 明确 GPL 组件改变 FFmpeg 整体许可；相关 [7.0.2 静态构建说明](https://johnvansickle.com/ffmpeg/release-readme.txt) 标明 GPLv3。构建来源一致性尚未逐字节确认；当前上游 release-source 索引仍列 4.1 及旧依赖，不能把它当作已验证的 7.0.2 完整对应源码。

因此“远端已有相同文件”只证明来源/对象相同，不证明许可合规。本轮禁止新发布包含该 wheel 的分发包或 Chat Audit FFmpeg 镜像；不自动改许可证、重写或销毁既有公开/本地历史。可继续的安全处理是从已整合两个远端 README 提交的固定树制作独立源码快照，排除 wheel 并记录源 SHA/排除清单，作为新的版本分支或源码发布；保留现有远端 main 和本地完整合并历史，不 force push。该快照尚未制作/推送，不把建议记作已完成。

NAS resident 十服务没有 Chat Audit 或其 FFmpeg 镜像，许可缺口不能被健康探针代替，也不应引入无关媒体服务更新。
