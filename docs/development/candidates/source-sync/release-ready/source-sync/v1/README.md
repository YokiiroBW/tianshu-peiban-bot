# source-sync/v1 · 1.0.0

独立来源同步契约包，供协调者审查并发布实现基线。**文件存在、校验通过不代表已发布、产品实现或完整L0通过。** 发布状态由协调记录决定；本包不执行产品、迁移、设备或发布操作。

物理来源P与角色受理A分层，共享Core会话/轮次顺序，不共用私密角色记忆。只新增本包接口，已发布text-dialogue/v1和profile-memory/v1保持原样；版本与完整依赖哈希见[manifest](manifest.json)。

- 唯一接口目录：[interfaces.json](interfaces.json)，含HTTP、受信本地应用端口和引用的既有接口。
- 唯一规则：[semantics.md](semantics.md)；兼容与迁移：[compatibility.md](compatibility.md)。
- 字段：[shared.json](schemas/shared.json)、[sources.json](schemas/sources.json)、[workflow.json](schemas/workflow.json)。
- 合成验收：[正例](examples/documents.json)、[结构反例](examples/negative-documents.json)、[关系案例](examples/relations.json)。
- `rules.py`只做跨字段关系断言；`validate.py`独立校验本包、依赖、全部schema引用及正反例。不包含SQL参考模型或产品SourceAuthority。

在已发布目录布局中运行：

```text
python -B contracts/source-sync/v1/validate.py
```

发布准备目录尚无同级依赖时，明确指定只读的已发布合同根：

```text
python -B path/to/source-sync/v1/validate.py --contracts-root path/to/contracts
```

解释器需要jsonschema及referencing，采用既有合同验证环境（jsonschema 4.25.1已验证，需date-time/uri格式校验器）。可通过PYTHONPATH指定已安装依赖；脚本不安装软件、不访问网络、不导入产品、历史提案或工作区测试文件。仅本包和固定已发布依赖即可完成验证。
