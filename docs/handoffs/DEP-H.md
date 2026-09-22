# DEP-H 四真实服务故障验收接线

## 目标与基线

按第四批精确卡完成七项dependency_missing的真实产品接线与本地执行。
根基线 `eced6de3ed0e2292d8c767d9f37fea36d0e04476`；仅修改 `tests/release_acceptance/` 与本交接。
最终包含本交接的Codex提交HEAD随交付消息报告，之后停写；不合并/推送、不改根板/产品/部署模板。

固定产品：platform `0a3cf65b8da19eabdd5d72f9f999281cbb52fdac`，
gateway `ec20f95e3849ebee968c4f00d7a31d14eccaa40f`，
companion `e94b609099365f75ca933d9fed03cdfbc83ec235`，
memory `9a3b2bed6aebff9f0677f2c62e979859769e0c9c`。
产品从协调Git对象archive到本任务私有目录，不读其他工作者活动检出。
合同读取协调根原字节，按清单及包manifest核后复制；续期manifest仍为
`c5017724187c1386b647fcc5b41ab3cb1702f27d6192f3a87fcefb23e7a5a61c`。

## 实现

- 拆出进程生命周期、模型端口、sender TLS故障代理、故障控制及场景模块；product_stack由660余行降到约450行。
- 默认plan，实际执行需`--execute`；新`--faults`只在全新合成scope执行，不能与257长程混用。
- unknown来自真实平台sent回包丢失；保留Core真实closed_unknown和原始日志，重放两次不重发。
- 四产品崩溃重启后公开历史/unknown回执保留、旧会话失效、无重发；复核新PID实际JSON加载摘要及鉴权ready。
- 来源撤回采用已有操作员register-input(revision2/retract)及dispatch-fanout，服务在线并存成功；不增加权限、不SQL造状态。
  先证明短上下文实际包含旧文，再撤回核公开snapshot正文不可用和下一次真实模型请求排除旧文。
- 模型公开revoke-config后网页立即503/model_not_configured，冷网关403，上游/发送增量0；撤销保持，不重新签发/发布。
- 合成provider延迟配合2秒网关限额，验证真实超时、公开取消，实际释放两条迟到响应后无重试/投递。
- 模型断TCP只断言真实failed，核原始generation失败、禁止成功delivery，不改称unknown。
- Windows强制字节锁覆盖活动/轮转日志段，逐四产品实际ready503、新业务无模型/发送副作用；解锁+重启后配置/ready复核。
- 修复旧日志枚举漏`.jsonl.N`；原wave3/wave3-r1以及全部65个旧证据文件原字节保留。

## 实际验证及证据

Windows，Python3.12.14，本任务独立venv；完整包版本见 `tests/release_acceptance/evidence/wave4-h/validation.json`。
这不是四镜像正式安装依赖证明。没有Docker/Linux/NAS操作、系统安装、真实账号/模型/数据。

实际从本任务根执行（Python路径 `.runtime/dep-h/venv/Scripts/python.exe`）：

```text
python -B tests/release_acceptance/run.py snapshot --manifest tests/release_acceptance/evidence/wave3/tested-manifest.json --contracts-root C:/YOKI/Codex/tianshu-peiban-bot/contracts --repositories .runtime/dep-h/repos.json --output .runtime/dep-h/snapshots
python -B -m unittest discover -s tests/release_acceptance -p test_*.py -v
python -B tests/release_acceptance/product_stack.py --execute --faults --manifest tests/release_acceptance/evidence/wave3/tested-manifest.json --snapshots .runtime/dep-h/snapshots --repositories .runtime/dep-h/repos.json --output tests/release_acceptance/evidence/wave4-h/final-fixed
python -m ruff check tests/release_acceptance
git diff --check
```

61项单测通过，0skip，30.813秒；6项新增覆盖真实TLS回包丢失、200/failed不改写、鉴权拒绝、轮转段读取、OS追加锁与无文件拒绝。
新增测试初稿语法错误已修正，未隐去；最终单测原输出保留。
最终四产品报告：**17 pass、0 fail、0 dependency_missing、3 not_run**，48.797秒，verdict=incomplete（Python退出2）。
七个故障全部有实际执行证据，长期记忆/归档pass仍只代表disabled_verified，非写入/归档完成。
plan实测不创建输出目录、不启动产品。完整diff与静态检查通过。

报告原字节SHA256 `f227ebce9601279e42ee263f6fdbbc92493472590468cb773e276859333ba14e`。
被测清单SHA256 `08c12281065b5e5e6e1cd515a1caae25cbd87d587ffb454c287a9e58e84e57a1`。
最终运行前后实现hash不变，结束后源码快照完整复核；所有65个旧证据与根基线Git原字节逐项相等。
`attempt-1`、`attempt-2`、`final`均保留原报告/原诊断，不回填新实现hash；原因及范围见wave4-h/README。
没有重跑或替换旧257轮；新证据不修改旧输入清单或发布状态。

## 限制与下一步

- 仅本地Windows真实进程+HTTPS与合成端口；Linux镜像、NAS、浏览器、真实模型质量、24h均未验。
- 日志故障依赖Windows强制字节锁；POSIX运行明确dependency_missing，需协调安排Linux权限/挂载故障注入。
- 重启是crash recovery。Memory两次超5秒后forced_owned_process_crash，其他退出码也未证明正常停写。
  `normal_stop_proven=false`及逐PID/退出码独立记录，不能代替DEP-J正常停止/恢复。
- 模型撤销仅证明网页即时拒绝和日志故障重启后的冷网关拒绝；未证明暖缓存撤销传播时延或进程内来源续期撤销。
- 来源撤回是已有操作员公开CLI能力，不冒称网页撤回功能。短上下文正/负对照不是自动长期记忆消费者证明。
- 私有凭据、ref、模型请求正文和DB未落交付证据。报告hash是内容完整性，不是独立真实性签名。

协调下一步独立审查固定提交与证据，再串行集成；Linux/NAS及上述剩余维度单独验收。
本任务未要求新产品适配：最初来源入口疑问已由协调指向现有register-input/dispatch-fanout并实际闭合。
