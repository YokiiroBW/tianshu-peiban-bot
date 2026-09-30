# 记忆页面角色选择增量审查

日期：2026-09-30  
结论：**代码审查通过**。此前发现的 P2 缓存可见性问题已在最终 Platform 候选修复；复核未发现可确认的角色越权缺陷。

## 固定审查范围

- Platform：已部署基线 `17af1b43b432f4bd6e250192fcfad35f8b04a8c1` → 最终固定候选 `95ca75c3a441847c015e391f0fbaf3a179d98539`；缓存修复代码提交为 `d65bd68c10e8d9fa7c367050ee2aa4f6240f0e68`，最终候选另含交接文档提交。
- Memory：已部署基线 `870d9269d33b88ed34c6445eb7d95f791b43a914` → 最终固定候选 `087f9c956848887b3a86d50cadbabe8ba14e2625`。运行代码仍是 `da14cfd91675b2a0d400227b916e3f3a299904b6`；后续只增加交接文档。
- 审查只覆盖上述固定差异及其必要的 HTTP 分发、来源解析与页面呈现支撑路径；未运行正式全仓安全扫描。

## 已修复观察项

### [P2] 标签页恢复时，旧记忆仍显示在重新授权请求返回之前

位置：Platform `apps/web/src/features/memory/MemoryPage.tsx`，`verify` 与 `visibilitychange` 处理，约第 313–379 行。

原实现中 `verify()` 在文档隐藏时跳过轮询；页面重新可见时立即发起 `overview` 授权读取，但旧概览、人物/群列表和记录仍留在状态并继续渲染，直到授权读取失败才清除。

修复提交 `d65bd68c10e8d9fa7c367050ee2aa4f6240f0e68` 在恢复可见时先中止旧校验、同步清空缓存并进入重新核验面板；授权成功后重读当前支持的页面（概览、人物/群列表或本人记录），拒绝时通过 `failRead()` 保持内容清空并标记角色不可读。活动标签页仍每 15 秒核验一次；没有服务端撤权推送，因此不是实时撤权，15 秒轮询间隔及请求返回前是明确的可见页检查边界。

新增页面测试在角色 B 本人记录已显示后暂停恢复时的 overview 请求，断言 pending 阶段旧记录数为零；成功后断言本人记录重新读取并出现；模拟精确 grant 403 后断言旧记录仍隐藏且角色不可读。该用例使用 API route 替身，只验证页面状态和门控，不代替真实后端撤权联验。

## 其他检查结论

- 角色目录由 Platform 服务端从本地 role runtime 行派生，并按当前 console principal 过滤；`state` 路由由外层 WebConsole 先验证登录会话。角色 ID、友好名称和状态没有匿名枚举路径。
- 网页读取必须成对提交 `role_id` 与 `role_version`；两者都要匹配服务端当前目录。旧客户端省略两字段时只选择原静态默认角色。未知、空值、过期版本、停用角色和关闭 `memory.read` 均拒绝，不会自动回退到默认角色。
- Platform 从原 `entry_id` 重新派生 account/person/audience/conversation 与 route，只替换服务端目录已核验的 actor；动态读取使用独立临时 entry 和 origin，完成或取消后清理。上游响应返回前再次检查角色版本、scope 与 origin。
- Memory 同时要求 caller 与 browser reader 显式开启 runtime roles、当前精确 actor grant 为 enabled、account 相同，且 actor 替换后的完整原 scope 模板完全匹配。静态 actor 的旧授权仍按原白名单与模板工作；显式停用 grant 会拒绝静态及动态读取。
- Memory 概览按完整 scope 查询；人物/群投影按 actor 过滤；分页 cursor 的签名和 expected scope 包含 actor，跨角色 cursor 被拒绝。网页角色切换会清空旧结果与游标、取消当前页面请求，并以 generation 阻止迟到响应覆盖新角色。
- 5ff7b11 的最小业务增量把 `state=disabled` 明确投影为 `role_disabled`，理由映射合理；其余新增内容是 Chromium 联测夹具。`git diff --check` 在两产品最终审查范围内均无输出。

## 验证与证据边界

- 总控报告最初的后端联合 `test_memory_joint.py` 通过 `1/1`、用时 `4.51s`；本审查者未重复运行该测试。该联验覆盖真实 Platform 应用与 Memory TLS 进程、合成来源数据、两角色投影/分页、跨角色 cursor、grant 撤销与重启。
- 修复后总控归档的 [`acceptance.json`](memory-role-browser-2026-09-30/acceptance.json) 记录固定 Platform `95ca75c` / Memory `087f9c9`（Memory 运行代码 `da14cfd`）：`real_http_web_to_platform_https_memory_chromium` 为 1 项通过、10.037 秒、未拦截 API，视口 1440×1000 与 390×844。此真实 Chromium 联验覆盖本地登录、双角色切换、人物投影、本人记录和刷新后选择保持，但不覆盖精确 grant 撤权后恢复标签页的时序。
- 该 browser fixture 的页面入口由 `start_http` 提供 loopback HTTP；Platform↔Memory 的读取与来源解析连接使用 HTTPS。若验收要求浏览器到 Platform 页面本身也必须覆盖 HTTPS/Secure Cookie，该夹具尚不能证明这一点。
- 原及新增 `apps/web/tests/memory-role.spec.ts` 全部拦截 API，只证明页面状态逻辑，不作为真实后台端到端证据。修复后归档结果记录 `role_switch_and_visibility_pending_revoke_ui` 2 项通过、6 秒，覆盖 pending 隐藏、成功重读和 403 保持隐藏；慢响应用例也不验证真实网络延迟。合成 schema 样例另记录有效 6 项、无效 5 项通过。
- [`acceptance.json`](memory-role-browser-2026-09-30/acceptance.json) 列出的 4 张桌面/手机实时页面与 pending 状态截图已由本审查者目视检查；本地重新计算的 SHA-256 均与清单一致。记录明确 `production_browser_session_tested=false`，这些结果不代表生产账号会话。
- Memory 手工交接记录的套件结果是工作者报告，未由本审查重复执行；没有 NAS 操作、真实账号/数据或部署验证。

## 最终 gate

最终固定候选的**本地代码审查与所要求的本地验收证据通过**：P2 已修复；归档记录显示真实 Chromium 联验 `1 test in 10.037s OK`，缓存恢复 UI 状态用例 `2/2` 通过。真实联验中的浏览器入口由 loopback HTTP 提供，Platform↔Memory 使用 HTTPS；它没有证明浏览器到 Platform 页面本身的 HTTPS 或 Secure Cookie 行为。候选合同定义同源端点及 WebConsole 会话/CSRF 门禁，没有定义浏览器入口 TLS 传输验收；若部署 gate 另要求浏览器入口 HTTPS/Secure Cookie，应补独立 TLS 浏览器证据。没有执行 NAS 更新、生产部署或真实账号/数据验收。
