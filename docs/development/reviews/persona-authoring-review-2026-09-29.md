# 人格编辑首版审查与返修

审查基线：Platform bdd71c6fa3bbbfba0cd6494cfd8ae811ebcf1582，Companion 99d76d922ea601d814923a1d91e15cfab568e3e0。总控与独立 Sol/xhigh 审查。

最终代码放行：Platform fd1eb9538bb78252859cb1128bf33be24670a5e9，Companion da3907288d816bef1c7d25df6cd0f74de012fc3a。下述四项均已修复，独立窄复核通过。额外发现未启用 Personas 的旧角色没有 content 字段；总控集成补丁改为兼容读取，并补真实模型请求回归。6 项人格/模型测试通过，Ruff 与 diff 检查通过。浏览器必须先重新 Vite build，再运行真实 HTTPS 测试：2/2 通过，覆盖后退保护与两类覆盖后的状态变化。旧 dist 导致的两次浏览器失败不计为代码放行证据。

候选合同源提交 473191e5600723902f4f717cc04ccbcb936bb178；总控校验 schema、实例和文件摘要后发布 persona-authoring/v1。新增写合同独立于旧 persona-management/v1，后者保持不变。运行时仍以显式配置/授权及代码校验启用，不把合同发布误称为自动启用。

## 已交回同一实现任务的四项问题

1. 应用档案时，留空的 tone/style/address 会继承目标旧字段。应删除四个编辑字段的旧值，再覆盖档案内容，保留非编辑扩展字段。
2. Core 模型 system 消息只消费 persona；语气、风格、称呼必须明确编入模型输入，名称/说明不进入提示词，旧在途快照保持旧值。
3. hash 路由浏览器后退/前进绕过链接点击与 beforeunload 防丢保护，需要导航拦截或可靠恢复未保存输入，并做浏览器验证。
4. 档案“已应用”只比较本档案上次应用版本；目标被其他人格覆盖后错误显示当前生效。应核对目标发布指针，区别当前生效与曾应用。

原子事务、幂等账本、版本检查、角色创建边界、启动导入路径未发现确定阻断。生产前须固定返修提交、复验并明确发布新增写合同，不能改旧只读合同。

## 总控已执行验证

旧只读与 published 套件：47 通过，1 项 Windows 清理 SQLite 临时文件时 WinError 32。精确失败为 `PersonaPageTests.test_removing_the_persona_credential_ends_the_session_and_states_the_gap`；单独在原生产基线和新任务工作树执行都通过（各 1/1）。不是断言失败，不能把整套报告成全部通过。

复现环境：`TS012_CONTRACT_DIR=contracts/text-dialogue/v1`，`TS025_CANDIDATE_DIR=contracts/persona-management/candidate-v1-r2`，`TS013_TLS_PYTHON=.runtime/nas-a1-r1-venv/Scripts/python.exe`，均使用绝对路径；pytest `tests/backend/test_persona_published.py tests/backend/test_personas_web.py -q --tb=short`。

NAS 只读预检：Companion SQLite v9，actor:household 现有一份人格、一份修订、一份发布；Platform 已配置专用 HTTPS 人格连接和 persona.read。启用计划仅增加编辑开关、既有角色应用范围和三个操作权限。停写冷备并 SQLite backup；不改已有生产人格内容，不发送真实 QQ 测试消息。
