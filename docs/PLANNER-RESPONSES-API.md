# Planner Responses 协议补充 · D10 v1

2026-10-20 Asia/Shanghai，LTV-TUDOU-RESPONSES-001，基线3ae82a6。本合同为D09 additive补充，不改变路由、创建/同意/撤销/动作请求、状态、错误、单journal财务不变量或默认disabled。无真实模型/供应商HTTP/付费，无新key加载/自动启用；仅合成策略与transport验证。官方OpenAI协议不是土豆兼容性/模型来源/账户价格/计费上界的证明。

## 1. 策略、历史兼容和安全投影

- 服务端策略新增可显式选择的`wire_api: "chat_completions"|"responses"`，客户端所有D09写请求仍拒绝wire_api/model/endpoint/价格/usage等自报字段。
- **旧策略缺wire_api只解释为legacy chat_completions，不补写私有字段，不重新计算旧policy_hash/quote disclosure/permit/receipt/帧hash。** 新ExecutionPolicy未显式给wire_api时frozen序列保持旧字段集合；显式协议被纳入新policy_hash与quote披露。缺字段不能猜Responses。
- 新显式策略要求endpoint协议匹配：chat以`/chat/completions`结尾，Responses以`/responses`结尾；仍只明确HTTPS无用户信息/query/fragment。精确model不换型号/渠道，无任何protocol/model/provider fallback。策略变更使旧grant不能新提交；旧已确认回执始终按该operation冻结quote策略解释和计价，不按当前策略重解析。
- 新显式协议同时进入operation及permit的wire_api，并与policy/endpoint/model/context/wire绑定；缺tag只能整组legacy chat，不能用一个新tag替代其他绑定。旧journal原样可读，旧在途/未知hold不升级或重发。

D09返回仅增加：

| 对象/字段 | 值 |
| --- | --- |
| SafePolicy.wire_api（policy/quote内） | chat_completions或responses；旧记录安全投影为chat_completions，私有原hash不变 |
| OperationSummary.wire_api | 同操作冻结策略；旧为chat_completions |
| OperationSummary.response_status | Responses已识别的completed/incomplete/failed/in_progress/queued/cancelled，或null；legacy chat为null |
| OperationSummary.cached_input_tokens | 已验证input子集数，未知/缺失/null为null，不虚构0 |
| OperationSummary.reasoning_output_tokens | 已验证output子集数，未知/缺失/null为null |

原finish_reason保留：chat仍stop等；Responses没有Chat finish_reason，返回null，不能伪造成stop。原provider_request_id/response_id、input/output总tokens、金额/hold、receipt_status等按D09保留。**原文、reasoning摘要/文本/encrypted_content、raw、annotations、上下文均不进入公开投影。** 旧FE忽略新字段可运行；新FE容忍旧payload缺上述字段。

## 2. Responses wire（仅文本，无状态续链）

请求体仅为服务器构造的固定字段集合：
```json
{
  "model":"exact-server-model",
  "input":[{"role":"system","content":"serialized local system instructions"},{"role":"user","content":"serialized local context"}],
  "max_output_tokens":2000,
  "stream":false,
  "store":false,
  "background":false,
  "tools":[],
  "tool_choice":"none",
  "parallel_tool_calls":false,
  "truncation":"disabled",
  "service_tier":"default"
}
```

input使用官方easy message的system/user/assistant文本content，内部本地工具结果按user文本序列化，保留完整已确认本地tool反馈；没有远程function_call_output或hosted tool。max_output_tokens取冻结policy上限，包含reasoning。无messages/max_tokens、不追加SDK默认参数、不加入temperature/搜索/文件/图像/音频/previous_response_id/conversation/include/远端上下文，不重播reasoning item或encrypted_content。Planner继续每轮只提交一个D08白名单JSON动作，本地执行read_frozen_brief/submit_shot_plan。

官方说明Responses使用input/output Items且store默认开启，因此这里明确store=false；官方reasoning指南说明reasoning计入output使用量且max_output_tokens耗尽可能incomplete。本轮按受限最小文本子集实现，不承诺覆盖全部Responses能力。[官方迁移指南](https://developers.openai.com/api/docs/guides/migrate-to-responses)，[官方reasoning指南](https://developers.openai.com/api/docs/guides/reasoning)。

## 3. 完整回执的最小接受集合

原始HTTP响应仍先私有持久，再解释，单次请求/整体deadline/无重试/同锁hold+intent+callcount/跨进程不可重派发保持D09。HTTP非200、重复key、非有限JSON、乱码、超包络、歧义或未知维度均不释放hold。

Responses完整成功必须：object=response、status=completed、error/incomplete_details均null或缺省、exact model、有效response id及独立provider_request_id（header x-request-id或明确request_id，不能拿response id伪装）、输出为1–16个已识别Items，id唯一且有效。只接受**恰好1个completed assistant message、恰好1个非空output_text**；annotations/logprobs只可空或缺省。不得只取output[0]或flat output_text来掩盖混合项，不把refusal/function_call/搜索/文件/图像/音频/MCP/未知tool输出当普通文本。

顶层回显也必须符合固定请求：service_tier仅缺省/null/default，tools仅缺省/空数组，tool_choice仅缺省/none，store/background/parallel_tool_calls如提供须false，previous_response_id/conversation须空，truncation如提供须disabled。未知非null费用、用量、token或缓存维度（含顶层cache_write_tokens/cache_creation_input_tokens）保留hold；max_output_tokens如回显须为与冻结策略相等的严格整数，不能被误当独立计费维度或放过变更上限。

可另有reasoning Items，仅接受id/type/summary及官方已知可选status/content/encrypted_content；status如提供须completed，summary只summary_text，content只reasoning_text，文本/opaque blob受原包络上限约束。这些内容仅私有，不执行、不公开、不重播；允许官方stateless回执返回encrypted_content不代表启用远端对话状态。多message/多text块、未知字段/类型或非完成状态均保留hold等待核对。

本轮输出接受范围刻意严格；合法但超出此子集的供应商输出也保持未确认，不降级/拼接猜测。官方文本指南要求按output Items处理而非假定第一项文本；在此之上采用单JSON动作的产品限制。[官方文本指南](https://developers.openai.com/api/docs/guides/text)。

请求固定service_tier=default，不申请priority/flex/scale；回执只容忍缺省/null/default（缺省仅表示未见矛盾，不证明第三方计费已核实），auto未解析路由及其他tier保留hold。非null billing/cost/pricing/charge/fee/metering等明确费用块、工具/音频/图像用量块也拒绝，不只检查usage字典；普通metadata不一概拒绝。返回tools/tool_choice/store/background/parallel_tool_calls/truncation等已知回显如与固定wire冲突，或非null previous_response_id/conversation，均保持未确认。message.phase如提供只接受null/final_answer，commentary不作为最终动作。

## 4. usage与本地计价

- usage.input_tokens/output_tokens/total_tokens全部必需，严格非bool非负整数，total=input+output；input不超该operation可信actual upper及policy上界，output不超max_output_tokens。usage未知顶层计费字段拒绝，不从文本长度猜tokens。
- 可缺省/null的input_tokens_details如为对象，只接受cached_tokens及可选cache_write_tokens，cached_tokens必填且严格整数；cached+write不得超input。cache_write_tokens非0视为本轮未覆盖写缓存计费，保留hold。未知音频/图像/工具等维度即使包含文本也不忽略。
- 可缺省/null的output_tokens_details如为对象，只接受reasoning_tokens，严格整数且不超output。cached_input_tokens是input子集，reasoning_output_tokens是output子集，**既不额外相加也不减去**。所有input/output总tokens按冻结保守上限费率计价；不优惠缓存、不省略reasoning，Decimal微元向上舍入，不能因此声称真实provider账单已确认。
- Chat legacy归一规则/历史已计价值保持D09，不把Responses usage字段当chat兼容捷径；未知或错误回执local amount仍null、全hold保留、原op不重发。

status=incomplete或incomplete_details非null→RECEIPT_TRUNCATED；非completed/有error→RESPONSE_UNCONFIRMED；exact model不符→MODEL_MISMATCH；形态/缺项/未知计费→RECEIPT_INCOMPLETE；usage/字节/子集/上界不符→RECEIPT_OUT_OF_BOUNDS。仍使用D09 needs_reconciliation和原错误码，不新增绕过入口。

## 5. 不变的执行门禁与证据范围

protocol不改变账户/费用上限、revoke/expiry、未知hold、source/quote/grant绑定。可信input bounder必须绑定包含协议的精确policy_hash，并检查**实际Responses wire**；旧chat的bounder不能因换URL自动复用。无真实tokenizer或bytes=token默认算法，不能从OpenAI文档假定土豆实现计数端点/模型cap/有效账户费率。新protocol未核实时保持disabled/POLICY_UNAVAILABLE。

官方完整create reference体积过大且.md在当前读取工具失败，已实际fetch官方text/migration/reasoning指南，并读取已安装官方openai.types.responses类型源码补核输入/Items/usage。来源只证明官方形态；供应商能力/模型真实性/价格/输入计费上界仍另验。协议文档读取不计为模型调用；本轮推理HTTP/付费验证始终0。

2026-10-22完成本轮验收：后端全量489项、执行控制/Responses专项134项、前端118项通过；独立正式HTTP、旧历史与进程恢复也已验证。检查边界和真实渠道待办见[验证记录](./PLANNER-RESPONSES-VALIDATION.md)。
