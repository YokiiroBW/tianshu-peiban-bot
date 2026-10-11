# 本地媒体存储联合验收

本次只写独立联合测试与本交接，产品文件由后端、前端 owner 实现。目标是证明无 AssetLibrary 目标也能通过实际 Platform HTTP 完成存储登记、目录选择、纯监控订阅、本地媒体落地和重启恢复。

最终验证源为 `worktrees/media-storage-20261012/platform`，固定集成提交 **`9667373c1800f7cb75b4f300efde2619e89c89ad`**；运行前后均核实 SHA，工作树干净。共同开发基线为 `be98a630636ff610fb86de665967c467bf9f921a`。联合测试文件为 `tests/integration/test_media_storage_joint.py`，本地实际字节 SHA-256 为 `3d6e4ccfc90c91028c34f8310d6c567ddb9e970bcdce610fc8b7cea1542d431c`。协调文件由父级提交。

最终 **2/2 通过，33.306 秒，exit 0**，原始输出位于 `C:/YOKI/Codex/tianshu-peiban-bot/.runtime/media-storage-20261012/joint/final.log`。场景覆盖：

- 已存在的临时存储根登记、容量观察、真实 mkdir/browse、登记媒体及下载两用目录；无静态 AssetLibrary 目标。
- monitor 无 target 记录成员且零任务、零下载；转 download 沿用基线不自动补历史，显式 recompute 发布两 P 的真实 FFmpeg 合成视频、NFO、source.json。
- 实际 HTTP 服务重启后 client receipt 重放、已有任务/发布回执保持、重复扫描不入队；修改目录标签后追加第三 P，仅下载新 CID，最终完整树保留旧视频路径、哈希和分 P 编号。
- 真实本地发布器已经提交文件和 journal、工作器尚未保存发布回执时暂停返回，随后重启实际服务；保留正常三十秒退避，恢复同一 job/operation/package receipt，不重下载。

测试复用既有 `Upstream`、`SyntheticEngine`、`web_settings` 与登录夹具；实际 `create_app`、Media 生命周期、Worker、Repository、成品渲染器和 LocalPublisher 均运行。逐项核对 receipt 中所有文件大小及 SHA-256、实际 NFO XML 和 source.json 编号，单独验证成品归属标记。没有使用 PublicationFixture 或自建发布替身。Ruff check/format 通过。早期试跑仅因生活任务 Python 环境缺 PIL 未启动，已改用现有媒体联合 Python 环境，未安装依赖；候选预联另为 2/2、33.310 秒（`prejoint-2.log`）。

最终实际命令（PowerShell，工作目录为本 coordination 检出）：

```powershell
$env:PYTHONIOENCODING='utf-8'
& C:/YOKI/Codex/tianshu-peiban-bot/worktrees/video-subscription-20261009/integration/.runtime/venv/Scripts/python.exe -B tests/integration/test_media_storage_joint.py `
  --platform C:/YOKI/Codex/tianshu-peiban-bot/worktrees/media-storage-20261012/platform `
  --contracts C:/YOKI/Codex/tianshu-peiban-bot/.runtime/life-autonomy-20261011/contracts-input/contracts/text-dialogue/v1 `
  --ffmpeg C:/YOKI/Codex/tianshu-peiban-bot/.runtime/video-subscription-20261009/ffmpeg/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe `
  --ffprobe C:/YOKI/Codex/tianshu-peiban-bot/.runtime/video-subscription-20261009/ffmpeg/ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe `
  --runtime C:/YOKI/Codex/tianshu-peiban-bot/.runtime/media-storage-20261012/joint/final 2>&1 | Tee-Object -FilePath C:/YOKI/Codex/tianshu-peiban-bot/.runtime/media-storage-20261012/joint/final.log
```

边界：这是 Windows 本地临时文件系统联合验收。B 站 HTTP 输入、账号身份及下载引擎为合成夹具；FFmpeg/FFprobe 处理实际合成视频。未操作生产 NAS、真实 B 站登录/下载、付费模型、Emby/Jellyfin，也未验证 SMB/NFS 挂载或断电持久性。本次中断是实际服务生命周期停止后恢复；两个 rename 崩溃窗口、跨 publisher 线程互斥、越界/重挂对象变化/未知目标保护由产品 owner 的专项测试负责，本交接不把它们冒称为本联合独立覆盖。测试服务与临时目录已清理。

只读审查发现的标签版本阻断、UUID 别名登记、indexed 后残留 claim、调用方取消但复制线程仍执行等边界已由 owner 收口；最终代码有位置身份校验、重复物理目录登记拒绝、内核锁、非阻塞实例复制锁及 owned cleanup。下一步由父级提交协调文件并记录本地验收，生产部署及真实已挂载存储验收另按用户授权推进；不需要追加未变代码检查。
