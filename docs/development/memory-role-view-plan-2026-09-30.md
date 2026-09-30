# 记忆页面角色选择与查询隔离

用户请求：记忆页面没有角色选择，担心角色数据混在一起；检查并解决。此前用户要求后续开发在可见Sol/Luna新任务，沿用这一偏好。总控负责集成与NAS更新。

## 已核准事实

当前WebMemory固定web_memory.entry_id；Memory browser_registration固定account/actor/scopes，查询groups.scope/profile_shares.actor_id，未发现全角色混查。页面只读默认角色且不显示角色名。多角色NAS已部署17af1b4/9863800/870d926；projects/main包含独立未部署QQ身份511b631/fc4d04c/5a635aa。本任务从已部署基线开发，不夹带QQ身份migration或权限。集成由总控以单独增量合入main并保留独立部署SHA。

## 实现与验收目标

1. 记忆页面顶部使用现有设计组件增加清晰的角色选择，显示友好角色名，默认保留既有默认角色。所有记忆概览、人物/群画像、本人记忆查询和分页绑定所选角色；无角色、不授权、停用、记忆读取关闭有清晰提示，不自动切回别角色或展示旧数据。QQ身份账号关联属于独立档案功能，本轮不改入口/协议。
2. 浏览器只能选服务器返回且当前登录主体有权读取的角色标识，不能输入或绕过account/person/conversation/scope/origin/URL。Platform以该操作者既有入口生成精确actor来源范围，动态来源/权限必须由后台从当前管理目录派生；不要把前端传入actor_id直接替换到scope。
3. Memory继续作为数据与读取权限所有者。增加窄的动态角色浏览授权配置：保持原注册account和person/audience/conversation模板，只有显式允许runtime roles且当前精确role grant允许的actor可在模板范围内浏览，不能通过通配角色绕过来源/人物群范围检查。当前停用/配置失败/记忆read禁用按既有拒绝语义处理，角色目录可展示不可读原因，保留历史不删数据。不得直接改Memory DB或产生第二套记忆事实；旧单角色配置不变时保持原查询。
4. 所有计数、人物群列表、详情及游标有actor隔离。选B不能用A分页游标；无同scope原文/身份/缓存串用。角色切换立即清空所有旧结果、游标、详情与错误并取消旧请求；慢A响应不能覆盖B。刷新和跨记忆子页面选择保持（按账号隔离，登出清除）；后台撤销/角色版本变化重新核验，禁止保留旧数据。
5. UI保持现有柔和风格，控件统一；不要技术字段堆砌、不要让用户改配置文件，NAS配置由总控更新。若需要新网页请求合同，候选输出docs/contracts-candidates/memory-role-browser/v1；已发布内部合同不原地破坏。
6. 最低有效验证：真实HTTPS Platform+Memory，使用两个角色在同person/conversation下不同语义组及人物/群投影，确认概览/分页/记录分离、伪造actor和跨角色游标拒绝、grant撤销/记忆禁用拒绝、旧静态角色兼容、重启恢复。实际浏览器桌面+手机角色切换及慢请求测试，数据用合成，不发送模型/QQ消息。仅fake Peer成功不能宣称完成。范围稳定后一次集中回归与类型/构建，避免反复广泛测试。

## 独占范围与交付

实现者只写 C:/YOKI/Codex/memory-role-worktrees/platform 和 .../memory 两个产品独立worktree；允许web_memory.py/web_readers.py/WebConsole必要接线、MemoryPage/css和相应测试、Memory browser.py/授权窄接点与相关测试。原role_runtime可仅新增只读目录复用端口，不改调度/Persona/Provider/BOT策略；共享root、projects、其他任务检出和NAS不写。先把来源派生/读者授权小方案发总控确认接口边界后继续常规实现。不要自行扩大功能。

基线Platform17af1b43b432f4bd6e250192fcfad35f8b04a8c1、Memory870d9269d33b88ed34c6445eb7d95f791b43a914，Companion9863800f0fcca67f59aa285ae14802d391f77e50只读。交付固定干净提交、docs/handoffs/MEMORY-ROLE-20260930.md、实际命令与截图、配置差异、未覆盖范围。测试工具可复用既有本机依赖但不要修改其他工作树；本任务无NAS/真实密钥/模型/QQ权限。
