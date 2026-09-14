# DSH 执行约定

Codex只负责架构、分派、验收和排错；所有产品实现交DSH。保留DSH当前配置模型，不继承旧Codex的Astra模型指定。

- 仅处理本卡；先读主AGENTS/CURRENT，再读任务所属项目AGENTS和本卡明确文档。不要每轮加载全部历史。
- 仅使用已分配worktree；不运行workspace.py start、不修改主任务板/根contracts/projects集成检出，不自行选择下一个任务或再开子开发任务。
- 一次完成完整功能块，再集中必要验证。已有且输入不变的成功检查不重复。
- DSH workspace-write临时目录遇PermissionError属于运行环境阻断：记录标准命令、错误及预期验证，交Codex验收。不得改tempfile/sitecustomize/ACL/测试断言绕过，不改系统权限或关闭沙箱。
- 旧Codex缓存运行时目前不可用。可读本机工具：C:/YOKI/ComfyUI_Anime/ComfyUI-zove4/python/python.exe（3.13.11），同目录../Git/cmd/git.exe。不要改ComfyUI文件或运行GPU。需要依赖时只在自己.runtime建环境/cache，不修他人venv。无法验证时诚实标needs_validation。
- 不推送、不合并、不部署，不操作真实QQ/NAS/设备/付费模型。
- 完成后本地commit（命令级Codex或DSH身份，不改全局），写docs/handoffs/<任务号>.md。再在自己worktree的.runtime/dsh-delivery/写delivery.md、delivery.json、changes.patch和必要脱敏日志；不能写主工作区output（沙箱外）。
- delivery.json至少task_id,status,base_commit,head_commit,worktree,changed_files,checks,remaining,handoff_path。status为ready_for_review/needs_validation/blocked。patch用git diff --binary <base> HEAD，不包括凭据/DB/依赖。
- 遇架构/跨产品缺口写.runtime/dsh-delivery/BLOCKED.md（问题、证据、最小方案），然后只继续独立且已授权部分；不自行扩大合同。
