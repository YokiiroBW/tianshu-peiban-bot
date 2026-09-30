# 记忆页面角色选择增量审查

日期：2026-09-30  
结论：**changes requested**。发现 1 项 P2 缓存可见性阻断；其余已审查的角色隔离与读取授权路径未见可确认的越权缺陷。

## 固定审查范围

- Platform：已部署基线 `17af1b43b432f4bd6e250192fcfad35f8b04a8c1` → 原固定候选 `c369861e072bbdce6d88bbadc7bd0b48483f52ff`，并复核后续最小增量至 `5ff7b11105f109940d4cf014d2a280202273910f`。
- Memory：已部署基线 `870d9269d33b88ed34c6445eb7d95f791b43a914` → 原固定候选 `da14cfd91675b2a0d400227b916e3f3a299904b6`，并复核后续 `087f9c956848887b3a86d50cadbabe8ba14e2625`。后续只增加交接文档，运行代码仍为 `da14cfd`。
- 审查只覆盖上述固定差异及其必要的 HTTP 分发、来源解析与页面呈现支撑路径；未运行正式全仓安全扫描。

## 阻断

### [P2] 标签页恢复时，旧记忆仍显示在重新授权请求返回之前

位置：Platform `apps/web/src/features/memory/MemoryPage.tsx`，`verify` 与 `visibilitychange` 处理，约第 314–335 行。

`verify()` 在文档隐藏时跳过轮询；页面重新可见时，`visible()` 立即发起新的 `overview` 授权读取，但没有先清空或遮住已有的概览、人物/群列表和记录。旧数据仍留在 React 状态并继续渲染，直到授权读取失败后 `failRead()` 才清除。

可复现路径：登录并读取角色 B 的记忆 → 将标签页放到后台 → 撤销 B 的 Memory 精确 grant → 恢复标签页，并通过网络限速或暂停测试 Memory 响应让新的 `overview` 保持 pending。此时此前已撤权的记录仍可从页面看到，直到上游响应返回。服务端会拒绝新的读取；问题限定在客户端缓存视图继续显示。隐藏时长不受 15 秒轮询约束，因为隐藏页面会跳过检查。审查者未在真实浏览器中单独复现撤权时序；阻断依据是固定代码中可直接确认的状态与渲染路径。

修复方向：恢复可见时同步进入检查态并隐藏旧结果，只有当前授权检查通过后再显示页面数据；同时明确可见页面缓存的失效边界。当前代码没有该门控，因此与任务卡“后台撤销/角色版本变化重新核验，禁止保留旧数据”的要求不符。

## 其他检查结论

- 角色目录由 Platform 服务端从本地 role runtime 行派生，并按当前 console principal 过滤；`state` 路由由外层 WebConsole 先验证登录会话。角色 ID、友好名称和状态没有匿名枚举路径。
- 网页读取必须成对提交 `role_id` 与 `role_version`；两者都要匹配服务端当前目录。旧客户端省略两字段时只选择原静态默认角色。未知、空值、过期版本、停用角色和关闭 `memory.read` 均拒绝，不会自动回退到默认角色。
- Platform 从原 `entry_id` 重新派生 account/person/audience/conversation 与 route，只替换服务端目录已核验的 actor；动态读取使用独立临时 entry 和 origin，完成或取消后清理。上游响应返回前再次检查角色版本、scope 与 origin。
- Memory 同时要求 caller 与 browser reader 显式开启 runtime roles、当前精确 actor grant 为 enabled、account 相同，且 actor 替换后的完整原 scope 模板完全匹配。静态 actor 的旧授权仍按原白名单与模板工作；显式停用 grant 会拒绝静态及动态读取。
- Memory 概览按完整 scope 查询；人物/群投影按 actor 过滤；分页 cursor 的签名和 expected scope 包含 actor，跨角色 cursor 被拒绝。网页角色切换会清空旧结果与游标、取消当前页面请求，并以 generation 阻止迟到响应覆盖新角色。
- 5ff7b11 的最小业务增量把 `state=disabled` 明确投影为 `role_disabled`，理由映射合理；其余新增内容是 Chromium 联测夹具。`git diff --check` 在两产品最终审查范围内均无输出。

## 验证与证据边界

- 总控报告最初的后端联合 `test_memory_joint.py` 通过 `1/1`、用时 `4.51s`；本审查者未重复运行该测试。该联验覆盖真实 Platform 应用与 Memory TLS 进程、合成来源数据、两角色投影/分页、跨角色 cursor、grant 撤销与重启。
- 后续 `tests/backend/memory_role_joint_browser.mjs` 使用真实 Chromium 页面，不拦截 `/api/web/*`，覆盖本地登录、双角色切换、人物投影、本人记录和刷新后选择保持。总控报告设置 `TS_MEMORY_ROLE_BROWSER_NODE` 后复跑 `test_memory_joint.py` 得到 `1 test in 12.917s OK`；桌面与手机截图位于 Platform 忽略目录 `.runtime/memory-role-browser/`，本审查者已目视检查。该 fixture 未覆盖撤权后标签页恢复时缓存仍可见的路径。
- 该 browser fixture 的页面入口由 `start_http` 提供 loopback HTTP；Platform↔Memory 的读取与来源解析连接使用 HTTPS。若验收要求浏览器到 Platform 页面本身也必须覆盖 HTTPS/Secure Cookie，该夹具尚不能证明这一点。
- 原 `apps/web/tests/memory-role.spec.ts` 全部拦截 API，只证明页面状态逻辑，不作为真实后台端到端证据。它的慢响应用例也不验证真实网络延迟。
- Memory 手工交接记录的套件结果是工作者报告，未由本审查重复执行；没有 NAS 操作、真实账号/数据或部署验证。

## 最终 gate

本地代码审查目前 **不接受**：先修复前台恢复后的旧记忆可见窗口，再由总控复跑真实 Chromium fixture 并确认截图与结果。当前 12.917 秒的 fixture 复跑已通过，但发生在缓存修复之前。除此项外，本轮固定差异未发现需要阻止集成的已证实角色越权问题。NAS 更新仍由总控另行执行。
