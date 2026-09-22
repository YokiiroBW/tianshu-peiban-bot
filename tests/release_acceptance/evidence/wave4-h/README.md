# DEP-H 本地故障证据

权威结果为 `final-fixed/report.json` 与 `summary.md`：17 pass、0 fail、0 dependency_missing、3 not_run，48.797秒。
七个原缺失故障全部在Windows、四个固定产品CLI、真实HTTPS及显式合成模型/发送故障端口实际运行。
报告SHA256：`f227ebce9601279e42ee263f6fdbbc92493472590468cb773e276859333ba14e`。
报告仍incomplete，不升级发布清单；模型质量/浏览器/24h未跑，Linux/NAS未跑。

被测清单复用未改动的 `../wave3/tested-manifest.json`，SHA256
`08c12281065b5e5e6e1cd515a1caae25cbd87d587ffb454c287a9e58e84e57a1`。
四产品源码通过完整固定Git提交导出；合同从协调根核manifest及原字节后复制，结束后再核源码快照。
`validation.json` 保存原65文件逐项hash、测试环境、最终实现摘要核验结果；`unit-tests.txt`为61项实际测试输出。

所有 `causal-*.jsonl` 都是原产品诊断行，未拼造事件。只读诊断/模型端口事实与报告断言结合使用，
hash不是独立见证签名。模型请求正文仅在进程内用于撤回正负对照，没有落证据。
临时凭据、CA私钥、来源ref、合成DB均随本次临时目录清理，不在交付中。

保留开发尝试：

- `attempt-1`：13 pass、4 fail、3 not_run。运行器错误预期timeout为outcome，漏读`.jsonl.N`，且误用默认关闭的网页模型管理入口。
- `attempt-2`：16 pass、1 fail、3 not_run。Memory日志触发请求误带浏览器Origin，在日志入口前被拒；不是产品日志故障误绿。
- `final`：17 pass、3 not_run。首次全故障通过，但实现文件仍有Windows行尾；未回填成最终提交字节。
- `final-fixed`：最终LF代码执行；额外重启配置PID/hash复核与结束源码核验通过。旧报告从未重写。

初次编写新增单测有一处生成式语法错误，修正后61项一次全套通过、0skip；静态检查及完整diff检查通过。
没有重跑原257轮。源撤回使用正式已有register-input/retract/dispatch-fanout，不增加source.observe权限。

边界：模型撤销验证网页立即拒绝及**冷网关**读取撤销，未测暖缓存传播时延。
Windows日志故障为强制字节锁，POSIX分支明确未实现；不能拿这份结果替代Linux权限故障。
四产品重启是崩溃恢复，Memory两次被强制终止、其他信号退出也不是正常停写收据；逐PID/退出码见fault_observations。
本轮不证明DEP-J正常停写或备份恢复，不涉及NAS修改、真实模型、真实业务数据、推送或部署。
