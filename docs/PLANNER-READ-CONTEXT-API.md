# 规划上下文继承与截断分层 · D17

本接口用于受控文本试跑。它验证历史上已经成功的读源事件，为新意图生成可核对的上下文；不恢复停止的任务，不签发调用、预算或生产许可。

## HTTP 完整与输出完整

`experiments.planner_live.chat_receipt.inspect_chat_receipt` 使用与原解码器相同的严格外层、模型、ID、usage及字段检查，返回精确三键：

```python
{
    "classification": "complete",  # 或 truncated
    "metadata": {
        "provider_request_id": None,
        "provider_response_id": "original-body-id",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "reasoning_tokens": None,
        "output_limit_observed": True,
        "finish_reason": "stop",
    },
    "decoded": ...,  # 原八字段结果；truncated 时为 None
}
```

`stop` 是完整输出；`length` 是完整 HTTP 中的截断输出。后者只返回安全元数据，不返回正文片段，也不能进入工具执行、自动重试或风险释放。其他结束原因仍拒绝。原 `decode_chat_receipt` 保持八字段、只接受 `stop` 的行为。

允许可选 `message.reasoning_content` 为 None 或不超过 65,536 UTF-8 字节的字符串。它仅留在私有原文和私有 parsed 归档中，不能拼入工具正文、下一轮上下文、Reviewer 输入或公开日志。reasoning 计数属于 completion 子项，不能重复计费。

## 已读事件的继承证明

```python
from experiments.planner_live.read_context import verify_read_context

verified = verify_read_context(proof, expected)
messages = verified.messages()  # 每次返回新的副本
```

`proof` 精确包含九个原始对象：`source`、`initial_context`、`descriptor`、`raw`、`intent`、`summary`、`step`、`charge`、`inspection`。它们对应同一已处理成功的 `read_frozen_brief` 事件，不接受外部 `read_source=True` 代替。

`expected` 精确包含七个由可信调用者独立冻结的锚点：`proof_hash`、`parent_operation`、`parent_task_id`、`source_snapshot_hash`、`rules_hash`、`protocol_sha256`、`authorization_hash`。自己重算 proof 哈希并不能证明它可信；调用者必须先核对实际账本、采集存储、来源、原窗口与唯一平台费用记录。

纯验证器核对来源快照、实际冻结请求、原始停止回执、意图和成功工具事件的指纹、原采集检查、费用关联及原上下文。它不读取配置、文件、Key、账本或网络。原系统未记录的工具完成时间不补造。历史工具反馈的纯校验不算重新执行业务或恢复旧任务。

成功返回不可变 `VerifiedReadContext`，含 `context_json`、`context_hash`、`proof_hash`、`anchors_hash`、`source_snapshot_hash`、`read_operation_key`；`frozen()` 提供版本化描述，不能凭描述反序列化为可信权限。失败只抛 `ReadContextProofError`，固定 code 为 `EMPIRICAL_READ_CONTEXT_REJECTED`，不附私有片段。

继承消息仅包含原始 system/user、原读源 assistant 动作及实际工具反馈。后续截断或 unknown 的响应不继承。每次消费都重新验证原证明和可信锚点；新任务另行冻结最终上下文、wire、意图、调用计数、截止时间及预算引用。

## 本轮接入范围

D17 固定新键 F01，继承之前已用 3 次，最多新增 1 次、累计 4 次；请求使用已有 `max_tokens=8192`，不猜测关闭推理参数。原 100 元授权、四条既有 unknown 和风险保留。平台费用另行唯一核对，未核费不能交付计划；结构接受、独立 Reviewer 与人工终验分别记录。正式生产及默认采集保持禁用。
