# 图片响应诊断与单次校验内的解码复用 · D44

2026-10-28。[验收记录](./IMAGE-FAILURE-DIAGNOSIS-VALIDATION.md) · [未知风险保留计划](./IMAGE-RETAINED-UNKNOWN-API.md) · [响应期限合同](./IMAGE-RESPONSE-LIFECYCLE-API.md)。

D44补充图片操作端的只读响应摘要，并减少同一次账本重建中重复解码相同图像的开销。响应诊断帮助区分“没有保存原始响应”和“已保存HTTP响应，但未确认受理”；解码复用只保留成功的图像字节校验结果。两者都不产生新的执行权限，也不改变原请求、回执、未知状态或预算。

## 响应摘要

普通图片操作、runtime续接与仅GET观察共用`image_operator._resource`。针对经过原账本校验的operation／query，新增`response_diagnostic`，字段固定如下：

| 字段 | 含义 | 不能据此推断 |
| --- | --- | --- |
| `raw_captured` | 原生记录中存在已保存的raw响应 | collector文件此刻完整、服务端是否完成生产、费用是否确定 |
| `http_status` | 原有规范回执中的HTTP状态；无回执时为null | 200即业务成功，或403必然是Key错误 |
| `receipt_classification` | 原decoder给出的分类；无规范回执时为null | 展示摘要可以覆盖原分类或把unknown改为failed |
| `accepted_task_present` | 原规范回执包含已受理任务身份 | 已核费、图像已交付、质量合格或人工接受 |
| `retry_authorized` | 固定为false，诊断本身不授予重试权限 | 重新查看状态可以启动、续期或重发请求 |

对query，`accepted_task_present`来自它绑定的父POST任务；即使GET为pending、failed或unconfirmed，该值仍可能为true。查询结果必须看该GET自己的`receipt_classification`，实际查询是否允许仍由原`query_available`及权限校验决定。

例如，收到HTTP403但未确认受理的摘要可以是：

```json
{
  "raw_captured": true,
  "http_status": 403,
  "receipt_classification": "unconfirmed",
  "accepted_task_present": false,
  "retry_authorized": false
}
```

没有保存raw的unknown则是`raw_captured=false`，HTTP状态与回执分类均为null。这个摘要不把“没有回执”命名为超时，也不证明HTTP没有发出或费用为零。两种情形都保留原unknown。

展示层只读取已验证record中的标量，不再次解析响应正文或重建描述符。新增摘要不输出供应商message、原始body、响应头、任务ID、签名媒体URL或凭据，也不执行新的collector扫描。原有`failure_diagnostic`仍用于说明异常发生时的本地观察阶段，与响应摘要表达不同事实。通用Key鉴权、具体模型路由和精确费用证据须另行核对，不能从单个HTTP状态自动推导。

## 现有只读入口

从后端环境使用原`image_operator status`查看具体操作。以下均为占位符示例，必须替换成已有的私有控制文件和原记录标识；不是对真实任务的可重跑命令。

```text
python -m core.execution.image_operator status \
  --control "<PRIVATE_CONTROL_FILE>" \
  --operation "<OPERATION_ID>"
```

查看已存在runtime续接对应的历史操作：

```text
python -m core.execution.image_operator status \
  --control "<PRIVATE_CONTROL_FILE>" \
  --runtime-continuation "<RUNTIME_ACTIVATION_ID>" \
  --operation "<OPERATION_ID>"
```

仅GET观察的历史操作使用`--observation "<OBSERVATION_ACTIVATION_ID>"`代替`--runtime-continuation`，不能同时指定。输出中的`operation.response_diagnostic`和`queries[].response_diagnostic`采用同一结构。`image_runtime_operator status`仍是批准元数据入口，不替代上述具体操作诊断。

读取状态不建立执行会话，不加载Key，不发送生成或查询请求，不认领费用或恢复原记录。源码变化后，原批准仍可作为历史事实重建，但不能直接授权新源码执行；诊断可展示历史响应，执行门禁仍报告批准失效或其他阻塞原因。

## 解码复用的边界

原`verify_delivery`每次仍先验证精确query描述符、父POST与任务身份、原响应、分类及响应期限，并要求查询已完成。D44只将后面的完整图像字节解码拆为纯校验：验证PNG／JPEG／WEBP、单帧、非动画、尺寸及9:16比例，运行`verify()`后重新打开并`load()`，保持原检查与返回格式。

在一次同步账本重建的`_descriptor_snapshot`内，`_delivery_snapshot`允许复用成功的解码结果：

- 每次调用先要求严格的`bytes`类型及当前字节长度上限。复用键包含**完整不可变图像字节**、模块的尺寸／像素上限、Pillow像素上限及`LOAD_TRUNCATED_IMAGES`策略；不相信调用者传入的摘要、图片ID或回执hash。
- 只保存完整解码成功后的`(width, height, format)`，失败和未完成结果不入表。表最多32项，计入表的图像字节总量最多16MiB；超过复用容量仍执行完整解码，不因此放宽或拒绝原有有效输入。
- 复用仅在创建它的线程且上下文仍active时有效。嵌套重建使用自己的表；退出时通过`finally`标为失效、清空并恢复外层上下文，异常也执行清理。复制出去的上下文不能在原读取结束后继续借用该证明。
- 相同图片字节可以复用解码结果，但每个调用仍独立检查自己的请求／任务／回执／期限，重新生成该调用的完整交付证明。原descriptor、receipt和delivery的字段、hash算法及质量`not_reviewed`含义不变。

这不是跨请求的权限缓存。新鲜的来源、代码、grant、父资金和独立Key前／后及最终collector检查继续执行；原账本、风险、累计次数与unknown也不因性能优化而释放。

## 验收边界

本轮接口改动不能证明403的根因已解决，也不能替代新的真实生成或从入口到collector的120秒性能验收。只读历史基准、合成测试、实际模型结果和人工终验分别记录在[验收记录](./IMAGE-FAILURE-DIAGNOSIS-VALIDATION.md)。任何后续生成仍需适用的新版本合同及运行批准，并沿用原账户保留全部未知请求和累计消耗。
