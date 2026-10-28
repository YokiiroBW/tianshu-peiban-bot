# 对话历史有效性 v1，合同1.1.0（本地协调候选）

POST `/internal/v1/memory/context/history` 是 Memory 的只读授权查询，沿用 `context_query` 权限和真实来源解析。它不返回记忆正文，也不自行授予跨人或跨房间权限；可复核的范围只能来自当前服务端已证明的读取权限。

`expected_snapshot` 绑定本轮已经读取的内容快照，避免查询期间变化；`known_snapshot` 校验历史有效性。二者可分别为空，均由服务端核验。私聊关联范围仍来自既有显式关联。群范围仅可复核memory-context 1.1.0实际返回的requester及至多16个原群owner，全部同actor/group conversation且无association；它不是由请求者指定的授权集合。服务端完成相同group_read来源屏障、owner可读证明及当前版本核验后，才比较expected/known集合。任一owner失效拒绝旧快照，不枚举群成员、不回退为只验证viewer。

普通新增和语义重复合并推进内容版本，保留历史版本；遗忘、修订、当前事实被替换、撤销及其他未分类变化推进历史版本。来源编辑、撤回、身份与权限仍由原生来源核验独立检查。无历史元数据或检测到不经分类的内容版本变化时，保守以当前内容版本作为历史版本。

消费者只可在本轮仍有授权、同一角色及房间且来源仍有效时使用历史，并在投递前重查。旧轮次缺少本合同快照时继续使用既有严格内容版本检查。text-dialogue/v1及旧1.0.0发布物原样保留。本1.1.0显式依赖新的memory-context 1.1.0 manifest，schema和HTTP形状不变；不是对旧pin消费者的透明替换。

`group-history-relations.json`列出与query同一owner集合的语义关系；JSON检查形状不能替代来源及权限验证，实际运行验收在Q18交付另记。
