# 图片请求与响应生命周期 · D42

2026-10-28。[上一版运行合同](./IMAGE-RUNTIME-CONTINUATION-API.md) · [本轮验收与真实任务状态](./IMAGE-RESPONSE-LIFECYCLE-VALIDATION.md)。

D41第三镜在120秒许可内进入collector，但准备和权限校验占去了约64秒，留给响应约56秒。D42为**未来明确批准的请求**分开提交期限与响应期限，复用原任务、运行批准、媒体账本、预算和collector。旧请求的日期、nonce、权限、回执和unknown保持原样。

## 明确批准的合同

原 `ImageToolPolicy` 结构与hash不变。`quote_runtime_continuation` 新增可选 `response_window_seconds`，省略时仍创建原v2合同；值必须为整数1至180。新报价披露完整 `response_policy`，批准时仍核对原报价hash、明确consent、最新帧链、现行代码、租期和既留资金，不因增加等待时间预留资金或调用次数。

| 层级 | 新合同 | 保留的旧合同 |
| --- | --- | --- |
| 报价披露 | schema 2，`image_runtime_continuation_disclosure_v2` | schema 1 |
| 原生批准重建的authority | schema 3，`image_continuation_authority_v3` | schema 2 |
| 请求描述符与permit | schema 4，`controlled_image_http_v3` | v1／v2及仅GET观察合同 |
| collector claim | version 2 | version 1 |

`response_policy`冻结：schema 1、`image_response_observation_v1`、提交窗口120秒、响应窗口、cleanup 5秒及`contract_hash`。它进入报价与authority的hash，permit再绑定合同hash和响应截止时间；collector binding包含这两个字段和响应窗口秒数。严格重建会拒绝混版、额外字段和自洽重签名后的合同替换。单独构造合法字典不能代替原生批准。

## 时间含义

设permit签发时间为T、批准的响应窗口为W：

| 边界 | 规则 |
| --- | --- |
| 最晚允许发出请求 | 必须在T+120秒之前；Key前后、最终collector和worker HTTP入口继续核验 |
| 接受响应的截止时间 | T+120+W秒；原始响应的`received_at`必须不晚于此时 |
| collector默认观察结束 | 响应截止时间；显式较短的观察设置只能提前退出 |
| cleanup结束 | 响应截止后5秒；用于持久化和退出，不增加响应接受权限 |
| worker硬上限 | 新合同最长305秒；旧路径仍为150秒或更早的原cleanup期限 |

W=180时，响应截止为签发后300秒。180秒是**从最后允许提交的时刻向后保留的响应窗口**，不是从实际发出请求起精确计时的网络timeout。较早提交可等待更久。整个响应窗口必须位于新authority的有效期内；租期不足就拒绝签发，不自动缩短窗口或续期。

收到原始响应的时间与持久化完成时间分开判断。截止前收到的完整响应可以在cleanup内完成落盘，之后通过只读恢复采用；截止后收到的响应即使保存成功，也不能变为accepted。原绑定下唯一且完整的record／commit／ready仍是恢复前提。

## 原有执行入口

新合同复用 `image_runtime_operator`，没有额外执行器或预算账本：

```text
python -m core.execution.image_runtime_operator quote \
  --control /absolute/private/image-control.json \
  --valid-until <ISO-8601-time> \
  --response-window-seconds 180
```

这只是参数示例，不能复制为当前真实任务的重跑命令。报价输出会显示完整响应策略，`activate`仍需要原quote和disclosure hash。`--response-window-seconds`只用于quote；status和activate不能偷偷改变它。

已有图片操作端根据批准的authority构建新版本POST或独立GET。费用、交付、精确参考版本和累计序号的约束沿用原规则。已经受理的v3 POST也可作为明确批准的仅GET观察续接来源；该观察GET保持自己的120秒旧合同，不继承POST的响应窗口。

新unknown仍阻止后续付费操作。更换响应窗口、重新批准代码或租期、读取状态、重放、恢复，均不能清除unknown、增加次数或自动重新生成。

## 当前真实任务

D42没有激活真实新合同、发送POST／GET、认领费用或新增交付。原任务仍为2／4张首帧，第三镜unknown，第四镜未提交。平台同时间窗的SUCCESS记录缺少可核验的本地请求身份，不能据此填入task ID或恢复交付。

下一步需要在原账本中处理这笔结果未知的请求：优先获取精确关联证据；如果证据不可获得，应明确设计并验证保留未知风险的处置方案。旧记录不能改写为成功或零费；任何后续尝试必须形成明确的新候选，保留历史风险与累计次数。该合同的真实慢响应验收仍待安全的候选派发条件满足后进行。
