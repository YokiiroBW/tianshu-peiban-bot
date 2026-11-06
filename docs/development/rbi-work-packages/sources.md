# RBI-10：多上下文来源批预锁，保留 Life Goal 原子合同

本包为 RBI 实现期间发现的必要公共依赖，交既有“RBW 身份来源与失效传播”窗口。用户已授权对应窗口修复；不新建聊天，不续写旧完成卡。

代码基线仍为 b32a606d，本批公共准备为 18d640c4；新 Sources 工作树从根本次准备提交开始，精确 SHA/分支/路径见派发记录。本包不操作生产或清数据。

## 必须实现

在既有 Sources.prepare_event_writes(connection, context, events, *, additional_refs=()) 演进可选 scoped_refs=()，每项为 (original_context, refs)。用途是 Life 当前结果 context、goal/planning_context 的真实完整读集一次预锁，不统一成宽权限上下文：

- 保留现有返回 aliases、event/source IDs、旧调用和错误语义，不改返回 bool，不另建平行准备服务。
- 在 event/advisory/source/领域行锁前先规范取得每个真实 context 的 Identity 锁，然后将事件根/写集合、主来源依赖、scoped refs 的递归闭包一次规范排序锁定。
- 各 scoped 组只能按自己的 context 权限、scope epoch、来源状态与版本预检；不可把 Goal 私有来源扁平追加到结果 context 或用 admin/context 替代。
- scoped_refs 是附加的独立上下文锁集，不自动变成主事件的必需授权。合法context对某scoped组的可见性=false不能新增主事件整体失败：后续Goal仍按原check_many_on/validate_persistence布尔决定跳过或执行，方法只锁定闭包和按各自context预检，不能把锁存在当授权。主事件/原additional_refs的强制检查保持不变；格式/真正身份非法仍按原准确错误退出。若需向Life暴露组结果，优先原同事务check_many_on重入已锁闭包，不造新返回DTO或破坏aliases。
- 不在 Action 锁后补新 Source 锁。旧事件/别名及共享图证明、原函数54855325的reuse与AND/OR/深度/权限保护保持。
- 同一事务复用已锁闭包与证明时必须区分授权 context，不能把一个 context 的可见性结论当成另一个的结论。
- 不在 Sources 中执行 Life Goal、Action 或业务转换；Life 负责预读目标Goal及上下文/版本并在 Sources准备→Action fence→领域行锁之后CAS，Goal和原receipt保持同事务。
- Sources 无额外后台扫描，无新的中心恢复服务；本包预期无DDL，只有具体必要时才提根。

准确签名及必要检查尽早回执根和 Life RBI-04，立即推进实现，不因其他窗口未完成而停止。新增参数默认空，其他域旧调用行为保持。

## 允许写

- src/tianshu/sources/service.py
- src/tianshu/sources/ports.py
- src/tianshu/sources/rbi_*.py
- tests/sources/test_rbi_*.py
- docs/handoffs/RBI-10.md

不写 module.py、types、配置/依赖、既有schema/迁移主线、Life/Conversation/Models产品文件、活跃探针或生产。

## 必要用例与交付

真实身份/来源隔离PG夹具覆盖两个不同权限context：合法的主结果源与goal公共源；planning_context无权看到private/result来源时不授予Goal写权限且按原逻辑跳过可选Goal、主结果仍可合法提交；同source不同context不可越权复用；来源撤回/版本/alias并发；空scoped_refs兼容；两个事务反向输入顺序仍取得一致锁序。用例只编写，不在本窗口执行。

完整实现、必要用例代码和静态审阅后提交，回报准确SHA、公开签名、锁序与已有调用方兼容、根装配和未验证项。测试/构建/烟测/真实模型/QQ/生产均不在本窗口执行；根收齐集中必要验证。

模型 gpt-6.1-terra/max，service_tier=default、fast_mode=false。唯一协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae，用户已授权本批协作派发与回执。保留原始身份/请求/回执/unknown、权限/CAS/代际/预算及所有既有修改，不 reset/clean/stash，不新聊天/子代理/自动化。
