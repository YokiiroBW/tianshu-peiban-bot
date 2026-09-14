# TS-022 跨角色续接需修复

2026-09-14，交付1c4d20460a8d9e8377b431f04897303dc2298e9c暂未集成。协调全套94 passed/23 subtests passed，15.02秒，无跳过；ruff/diff通过。迁移/facts/后台check及P/A有限审查未发现其他确认阻断。

独立真实Core+SourceHarness内存复现：A提供方案已sent，B插话已sent，再对A请求继续方案。_process按全conversation最近1轮选择B为dependency，_check_dependency精确scope拒绝，A第三轮failed/scope_changed、不生成。

要求有界选择当前actor/person/audience/conversation的前序候选，然后继续原发送/版本/来源复核；不可放宽权限接受B，不因近轮无效静默跳至旧方案。补群私、多人物、失效和未送达边界后再审查集成。真实三方L0仍待此修复及产品集成。
