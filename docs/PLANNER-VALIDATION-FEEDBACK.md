# Planner 安全校验反馈 · D13

状态：合同、实现、后端隔离验收与主窗口独立复核完成；真实自主修正与内容质量尚未通过。
基线：`008d8f9`。

## 最小兼容接口

`execute_tool(raw, source, already_read)` 已增加 keyword-only 参数：

```python
execute_tool(raw, source, already_read, *, detailed_feedback=False)
```

只有显式 `detailed_feedback=True` 时，`INVALID_ACTION` 反馈可增加有界 `validation_issues`。默认返回的三元组、字典字段和值保持原样，保留全部旧精确比较测试。成功、SOURCE_NOT_READ、checked_plan 的原错误代码和接受判据保持原样。Action/ReadArgs/SubmitArgs/Shot/ShotPlan/checked_plan 不放宽、不替换，也不自动把字符串解包、修复 JSON 或补 plan。

本次生产源码仅改 protocol。正式 D08/D09 调用点、外部响应、SYSTEM 和工具 schema 均不修改；正式调用默认仍使用泛化反馈。本轮独立pilot显式选择详细反馈，并仅将结果追加到该任务私有工具上下文；试验已因新的unknown停止。该能力的显式开启，不等于已在所有正式任务默认启用；后续实测须重新固定明确的实现与任务版本。

## 反馈形态

JSON 字符串作为顶层 Action 的例子：

```json
{"ok":false,"code":"INVALID_ACTION","validation_issues":[{"path":"$","code":"OBJECT_REQUIRED"}]}
```

缺 plan 包装至少反馈 `{"path":"$.arguments.plan","code":"REQUIRED"}`。同层多出的字段只报告最近的已知容器 `$.arguments` 和 UNEXPECTED_FIELD，绝不复述额外字段名。

issues 最多 **8** 条，去重后按固定 `(path, code)` 排序，确保确定性。每条严格只有 `path`、`code`；不含 message、input、ctx、异常类型/文本、错误值、URL、源文或任意 extra 名称。Pydantic errors 只读取 type/loc，排除 input/context/url，忽略 msg。额外字段错误的末端名字一律丢弃。

### 固定字段路径

只允许以下 schema 位置；数组索引只投影为 `[*]`，不返回真实索引或 shot id：

- `$`、`$.tool`、`$.arguments`、`$.arguments.plan`
- `$.arguments.plan.source_snapshot_hash`、`.duration_ms`、`.ratio`、`.shots`
- `$.arguments.plan.shots[*]` 及其 `.id`、`.duration_ms`、`.visual`、`.camera`、`.audio`、`.depends_on`、`.depends_on[*]`

这里的缩写仅为阅读方便，实际返回完整固定路径字符串。路径必须按验证层级投影（Action、ReadArgs、SubmitArgs），不能只靠字段名判断。未知/额外位置收敛到最近的已知容器，不拼接任何不可信字符串。

### 固定错误类别

`JSON_INVALID`、`OBJECT_REQUIRED`、`REQUIRED`、`UNEXPECTED_FIELD`、`UNSUPPORTED_VALUE`、`STRING_REQUIRED`、`INTEGER_REQUIRED`、`ARRAY_REQUIRED`、`INVALID_FORMAT`、`LENGTH_BOUND`、`NUMERIC_BOUND`、`INVALID_VALUE`、`INVALID_STRUCTURE`。

JSON 语法/重复键/非有限数/过深结构等解析失败返回根位置 JSON_INVALID。Pydantic 已知类别映射到固定码，未知类别收敛 INVALID_STRUCTURE；不使用原 exception message 推断或公开信息。

## 接受与执行边界

- 既有 code 与严格判据不变；详细反馈只解释被拒绝的位置和类别。
- 不增加工具权限、模型请求次数、自动重试、repair/step 上限、预算、token 硬界或费用权限；不自动执行修复。
- 不改 D08/D09 安全投影、策略/许可/请求构造、normalizer、生产 disabled 状态或旧任务/context/journal；详细反馈只用于显式开启的后续私有工具消息。
- 历史 unknown 和风险保留，本补丁不读真实源/回执/配置，也不构成内容质量验收。

## 红回归与验收

实际 D09 FastAPI quote/grant/resume、worker、MockTransport、strict normalizer、真实 execute_tool 和私有反馈再入 wire 的两条链，分别模拟顶层 JSON 字符串与 submit 缺 plan 包装。两例最终合成计划都合法，保持一次格式修正、3 次合成请求、固定本地计价和不暴露公开字段；红点仅为下一请求工具反馈中缺 validation_issues。

基线红回归：**2 failed / 0.92s**，均 `KeyError: validation_issues`；网络出站/真实 HTTP/付费均 0。证据：`docs/evidence/LTV-PLANNER-CONTRACT-20261022/backend-red.*`。

新测试通过测试侧显式 opt-in 接入同一真实 runtime seam，不更改正式 runtime 调用点；全部旧测试保持原样。已验证默认兼容、只依赖安全反馈的合成修正、额外秘密字段名/嵌套位置、未知类别收敛、确定性排序/去重/上限、严格原判据、公开投影和历史字节兼容。

显式 opt-in 接口红回归也已运行：`backend-optin-red.*` **2 failed / 0.74s**。默认三参输出精确 generic 断言通过，显式 detailed_feedback=True 因当时接口尚未实现而失败。该组测试在功能就绪后以测试侧显式绑定通过实际 controlled runtime 闭环；不修改正式调用点。

## 实现、边界修补及最终验证

原 executor 仅改为私有函数名，AST 与原实现完全相同。公开 execute_tool 包装层先得到原判定；仅显式布尔 True 且 code=INVALID_ACTION 时补充诊断。详细诊断只再次解析并校验输入，不再次执行工具或调用模型；成功与原语义拒绝结果直接沿用。None/1/字符串等 truthy 值不会开启详细反馈。

安全投影补充了明确的“容器回退”：准确已知位置保留其字段；未知/extra 深层位置只允许回退到已知对象或数组，不能落在已知标量叶子。例如未知 `visual/…` 回退到 `shots[*]`，未知依赖项下级回退到 `depends_on`。该边界先有 **1 failed / 0.78s** 红回归，试验停止获准后最小修复；对应原用例 **1 passed / 0.75s**，最终以7种层级/类别组合覆盖。路径仍只输出固定常量，绝不输出其不可信尾部。

最终结果：

- 新增 **79 项**合成回归；D08 与 D09 的显式 opt-in 测试都证明模型替身必须读取对应安全反馈才修正成功。未开启时维持原泛化反馈并受原 repair 上限约束。
- 定向 **382 passed / 31.58s**，隔离全量 **656 passed / 50.86s**；无 failure/error/skip。
- 定向、全量、当前 **205 文件 manifest** 完全相同。对照上一已验收204文件，只有 protocol 改变并新增本轮测试文件；全部旧测试字节不变。
- **24 项机械源码保护检查**通过：原 executor AST（除私有改名）、schema、parse_json、checked_plan、LIMITS/原常量保持；正式 Planner/Execution runtime、actors/store、policy/transport/normalizer/API安全投影全文件原样。Ruff 与限定路径 diff 检查通过。
- 测试来自隔离源码副本，排除配置/凭证/私有运行状态；Python 父/子进程 socket 出站禁止。真实模型 HTTP、付费、网络出站均 **0**；不启停服务、不安装、不 stage/commit/push。

最终 protocol SHA256：`0e786584d01ce2a0e7869dae93fa6d8ccc8f0d81d377f0e3188e494c8b0e44fc`。

主窗口独立试验此前固定的是旧协议 SHA `0aba0a1adb1af4712c013e5448872c5ea068f6bfe3de76155ad220b080718dd8`。补丁在主窗口确认停止后实施，不把最终 SHA 回填到旧试验。本窗口未读取或修改其 plan/state/summary/receipt/caller，也未检查真实网络根因或账单；最终修补版本没有本窗口真实模型质量验证。

最终机器结果、完整清单、红绿日志/JUnit、源码保护、Ruff 及协议版本边界在 `docs/evidence/LTV-PLANNER-CONTRACT-20261022/`。产品各入口默认启用、真实自主修正成功、内容质量和用户终验均须另行验证，不能由这些合成测试推出。

统筹复核了205项清单与当前源码；独立审查另跑79项合成回归、420组基线判定对比和2520次非True开关检查，无阻断缺陷、出站0。真实调用、最新平台错误观察与下一项恢复工作见[本轮验证记录](./PLANNER-CONTRACT-VALIDATION.md)。
