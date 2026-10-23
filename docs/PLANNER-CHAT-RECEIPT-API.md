# 实验 Chat 回执解码 · D16

2026-10-23，基线 `6dad96c`。本接口服务于明确经验风险的文本试跑；不修改正式执行 normalizer、许可或计费保护。实现与验证状态见[验证记录](./PLANNER-CHAT-RECEIPT-VALIDATION.md)。

## 为什么需要单独解码

D15实际采到了完整HTTP200，但该响应没有`x-request-id`，usage还包含推理token子项。旧实验调用器无法接受这两种形状，trial停止；实际响应仍私有保存。不能生成请求ID，不能把正文ID或平台log ID填成HTTP请求ID，也不能倒改已停止trial的规则。

本接口是纯函数。调用者先通过[D15采集器](./PLANNER-EMPIRICAL-CAPTURE-API.md)验证实际意图、冻结wire、风险引用、原窗口、claim、持久提交及通知，再解码及时原始回执。解码器不读取文件、配置、Key、账本或网络，不派发请求，不执行工具、核费或恢复任务。

```python
from experiments.planner_live.chat_receipt import decode_chat_receipt

decoded = decode_chat_receipt(
    raw,
    expected_model="gemini-3.8-flash",
    max_output_tokens=2048,
    scope="empirical_planner_text_once",
)
```

## 返回与严格边界

成功返回精确八键：

| 字段 | 意义 |
| --- | --- |
| `parsed` | 原body严格解析后的完整对象，不重写原文件 |
| `content` | 原assistant文本，不修剪、补包装、修JSON或执行工具 |
| `provider_request_id` | 真实header／body request_id，允许两者皆缺时为None |
| `provider_response_id` | 必须来自真实body.id，不能由其他ID代替 |
| `usage` | 三基础计数：prompt_tokens、completion_tokens、total_tokens |
| `reasoning_tokens` | 可选输出子计数；缺失为None，明确0保留0 |
| `output_limit_observed` | 此次completion是否未超过请求值，仅为观察 |
| `finish_reason` | 必须为stop |

raw需含原`http_status/headers/body_base64/received_at`；可选`body_sha256`如存在必须核对实际正文。要求HTTP200、严格base64／UTF-8／外层JSON、无重复键或非有限数、正文≤1MiB、单个assistant choice、原文本非空且≤65,536字节。received_at只验证有时区格式；实际截止时间仍由调用者核对。

header只支持原采集白名单`x-request-id`。它和body.request_id存在时都须合法，两者冲突拒绝；正文ID另行必需。中性`provider_model`可保留为原始元数据，不覆盖model精确匹配，也不证明供应商实际底层型号。外层、choice、message均使用明确字段白名单；未知收费、工具或协议维度拒绝。

usage只支持三基础整数计数及可选`completion_tokens_details:{reasoning_tokens}`；拒绝bool、负数、总数不等、未知子项和超出completion的reasoning。推理token已经包含在completion中，不能再加到total或计算第二笔输出费用。超过requested仍返回`output_limit_observed=False`，不伪称计费硬界已成立；正式normalizer的原拒绝规则不变。

content内的坏JSON、Markdown或错误工具参数留给原`execute_tool`实际拒绝，真实固定反馈再进入受限修正上下文。完整HTTP回执、工具协议有效、15秒结构有效、内容审阅及平台核费分别记录，不能把一次解码成功当作内容或费用接受。

失败抛`ChatReceiptDecodeError`，只有固定`EMPIRICAL_CHAT_*` code，不附正文、凭证、URL或原异常片段。不返回部分成功，不授权自动重试或切换模型。

## 接入后的控制责任

私有控制器仍逐次核对原body SHA、capture／intent／source／rules／context绑定、原截止时间、parsed原样与解码字段。实际工具反馈和计划版本单独持久化；未核费停在`awaiting_charge_confirmation`，新unknown停止整个trial。

费用关联保留实际body ID、可缺HTTP ID、平台log ID及原body SHA；只接受唯一token/model/group/usage/time行，平台行跨操作不可复用。重新导出不能覆盖旧确认，HTTP ID的None不当作跨调用重复ID；body ID和平台log ID仍须唯一。平台实际导出是费用依据，本地按token估算不是账单。

若以新版本独立意图继续，D15已用1次仍计入累计4次上限，只剩3次新Planner调用，修正累计最多2次，沿用原100元授权和父账本；三条既有unknown及其风险不清除。实验解码接口本身不签发任何额度、Reviewer许可或媒体调用。
