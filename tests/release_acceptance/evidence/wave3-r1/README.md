# DEP-E R1：只读重评与测试时序定位

原执行提交为 `e6e505d725aff5cb47a149ae347729dbee7526d4`。`../wave3/`归档保持原字节，
没有重跑329秒四产品验证，也没有把新实现hash回填成原执行版本。发布结论仍incomplete/非release ready。

`renewal-recheck.json`由新 `recheck_renewal.py` 只读评估原报告和投递文件生成。
它先核原报告内容摘要、原投递文件摘要与数量，再执行新 `completed_after_expiry`。
结果：257条唯一成功delivery中15条事件时间严格晚于初始expiry；记录实际数量和首末时间。
校验器不使用检查时刻。新运行器从已受理输入单独收集correlation，并要求每项有唯一匹配的成功投递，
拒绝无记录、全过期前、恰好到期、坏时间、错误事件/结果/服务、重复或不匹配correlation与event_id。

原归档没有单独的提交correlation账本；历史逐请求匹配由原固定运行器执行，重评不补造该账本或独立签名。
收据明确是evaluation_only，不是新的产品运行证明。可在原归档外的新文件复核：

```text
python -B tests/release_acceptance/recheck_renewal.py --archive tests/release_acceptance/evidence/wave3 --execution-commit e6e505d725aff5cb47a149ae347729dbee7526d4 --output <原归档之外的新收据.json>
```

协调首次47项全套出现1失败：观察期限测试期望1连接实际2，46项通过；28.377秒。
其单独复跑通过不能抹去首失败。`budget-diagnosis.json`保留返修前固定12次原观察逻辑诊断的全部结果。
诊断只包装Client.request记录进入时间、父deadline和失败时剩余量，真实socket/响应流/观察逻辑未替换。
第11次复现2连接：第一次socket timeout返回时GetTickCount64仍剩5ms，第二连接开始于同一父期限内，
四次调用的parent deadline完全相同。机器的monotonic时钟分辨率15.625ms，perf_counter分辨率100ns。
这定位了“第一次socket超时必然耗尽单调时钟预算”的测试假设，未观察到生产代码重置预算。

只修改该测试夹具：第一次真实请求必须抛transport_deadline_exceeded，随后等待**同一既有deadline**到达再返回失败。
原0.13秒预算、总耗时<0.4秒、1连接、4失败断言全部保留；新增4次调用共享同一deadline断言。
生产 `acceptance/observation.py` 和 `acceptance/transport.py` 没有变更。

`validation.json`保留修改后一次全套55项通过（27.477秒，0skip）及固定12次稳定性验证的全部结果。
12次均通过，其中2次实际执行了等待下一个单调时钟刻度的夹具同步。没有循环重跑至绿后丢弃失败。
新时间判定8项正反例另先执行通过；原协调首验失败也记录在收据中。
最后审查又补充非法时区分钟及UTC换算越界反例，并仅重跑这8项通过；新评估收据绑定最终校验器摘要。
