# TS-030 记忆首切片审查通过

2026-09-14。修复3ff1c373，交接/合入端点358e4a664e14eee734494646f3aa3a0bc666a222。

已复核首轮三项问题的实现与回归：中文单字不再作为证据，保守词项门槛、完整组覆盖度与BM25排序先于预算；零预算先核验身份/精确scope/版本，SQL authorizer和trace证明不读正文或创建FTS，field/item先限候选且不走全文；墓碑correct返回400 invalid_input，版本/历史/确认不变，旧forget幂等重放保留。

协调者运行ruff check、ruff format --check通过；完整本产品pytest **49 passed，2个已知第三方弃用warning**，包括独立HTTP进程和数据库重启。diff --check通过；服务随测试关闭。18个带干扰与预算反例的检索证据已更新。此阶段是词项/FTS检索，不声称开放域语义或embedding质量已验证。

快进合入记忆协调检出，TS-030按本人subject本地首切片标done。生产来源核验/确认签发、Chat Audit、PG、embedding、双账号证明与跨产品L0/L1仍未完成。原文/关系候选通过明确local_fixture，默认缺真实来源返回503，不把该缺口当已接通。
