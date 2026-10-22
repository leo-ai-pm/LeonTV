# Planner Chat 输出参数补充 · D11 v1

2026-10-22 Asia/Shanghai；LTV-GEMINI-PLANNER-001，基线17ab1eb。本轮只增加服务端参数选择能力；D09/D10路由与写请求、授权、计费上界、normalizer和未知hold规则不变。strict production仍disabled，不自动加载key或创建可信bounder，不将实测pilot称为已认证正式runtime。

## 1. 唯一新增字段

服务端ExecutionPolicy可显式设置`chat_output_parameter: "max_tokens" | "max_completion_tokens"`，仅限wire_api为chat_completions（包括旧缺wire_api的legacy Chat）。Responses显式提供该字段一律拒绝，包括max_tokens；其wire仍只使用max_output_tokens，规则不变。

客户端所有D09写请求仍拒绝该字段，不通过浏览器字段选择模型、端点、参数或价格。SafePolicy在Chat安全投影该字段，旧缺字段展示max_tokens；Responses不返回该项。OperationSummary同样仅对Chat增加此字段，**取操作自身冻结quote policy**，不能用当前策略补历史；Responses操作不返回该项。旧客户端可忽略新增字段。

## 2. Hash、wire和许可绑定

- 旧策略缺字段时，frozen仍保留原字段集、policy_hash/disclosure与旧Chat wire字节；不回填、不重算、不迁移旧journal、permit或receipt。
- 未显式配置该字段的model_dump也不输出新默认字段，避免既有服务端用model_dump拼装Responses策略时被隐式Chat选项污染；显式配置始终校验并进入frozen/hash。
- 新显式字段即使选择max_tokens也进入新policy_hash与quote披露；变更后旧grant不能授权新提交。operation/permit显式保留同一字段，完整性校验与发送前sink核对其身份/存在性和实际wire。
- Chat实际发送体只包含选定的**一个**键，值沿用冻结policy.max_output_tokens；另一个键与Responses的max_output_tokens均不发送。禁止根据响应失败自动换参数、重试、换渠道或型号。
- 缺字段仅解释为legacy max_tokens，且仅限同组legacy记录；显式新policy的operation/permit缺字段或错绑均拒绝派发。实际wire/context仍重算、比对，错误在构造client前拒绝。

## 3. 不变的费用边界

policy.max_output_tokens仍是冻结的严格输出计费上界，不因换参数名而增加。usage超过该上界仍RECEIPT_OUT_OF_BOUNDS、local_units=None、全hold保留；不硬编码Gemini、不扣/加固定差值、不把reasoning遗漏或挪到免费部分。输入可信上界、跨进程额度/调用计数、撤销/到期、整体deadline、原键恢复与D10 Responses归一均不变。

已有两条Gemini实测表明max_tokens=128/1时平台报告completion=140/157，总平台请求日志计费CNY0.001132；正式normalizer均拒绝。根后续P03/P04参数对照中，max_completion_tokens=128返回completion104，但=1仍返回completion531、finish=stop并有140个可见字符。因此**换参数名也不能保证当前渠道遵守用量上限**，不能声称本配置功能修复了Gemini计费边界，更不能推出固定偏移或可信上界。费用的本地估价、平台逐条日志和发票确认保持区分，P03一次符合请求值也不构成保证。

真实M01可由根在独立、清楚标注的受控实测pilot中复用现有两工具/结构校验和预算账本；经验风险预留不等于认证上界。本轮不新增pilot收费入口或全量runtime，真实调用仅由根负责。

## 4. 必要验收

1. 旧D09 fixture原hash/历史字节/legacy wire不变，旧确认receipt恢复仍按旧策略；当前新策略不能改历史投影。
2. 两种Chat参数各只发一个键；显式字段改变hash，quote/grant/operation/permit一致，错绑/缺字段/篡改实际wire零派发。
3. 客户端自报拒绝；Responses显式字段拒绝且原wire/usage/hold规则不变。
4. 不论参数名称，超过冻结输出计费上界继续拒绝；不注入生产bounder或伪造billing_verified。合成测试仅复用离线fixture，不证明真实Gemini能力。
5. 同源隔离定向/全量、Ruff和manifest；不读真实配置/key/剧本，不真实HTTP、不启停共享服务、不安装依赖、不stage/commit/push。

## 5. 实现与验证

本后端窗口已完成最小配置/绑定实现并冻结：隔离定向166项、全量521项通过（旧489+新32），Ruff通过；定向、全量及当前202文件manifest一致。新增合成回归覆盖旧hash/wire/历史字节、两个单键wire、错误/Responses字段、quote/operation/permit绑定及篡改零派发、操作自身投影、旧费率回执恢复、旧grant/bounder不可自动复用、通用超界继续拒绝。

strict normalizer函数和Responses模块均与17ab1eb机械比对完全一致；本窗口真实模型/收费/测试出站0，不包含根另行执行的真实探针或M01 empirical pilot。根的参数对照不能被描述为已认证的正式上界；生产仍disabled。原始日志、manifest及代码hash见[后端进度](evidence/LTV-GEMINI-PLANNER-001/backend-progress.md)与[机器结果](evidence/LTV-GEMINI-PLANNER-001/backend-result.json)。
