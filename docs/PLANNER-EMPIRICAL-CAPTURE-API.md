# Planner 明确风险试跑与共用回执采集 · D15

2026-10-23（Asia/Shanghai），基线 `7319b34`。代码与合成接入验收通过；真实试跑尚未开始，结果见[验证记录](./PLANNER-LIVE-VALIDATION.md)。本合同不启用正式生产，也不签发新的用户预算或媒体权限。

## 两种授权，复用同一采集器

正式 `ExecutionRuntime → CapturedTransport` 仍要求原 policy、journal、quote、permit 和可信输入上界校验。`billing_bound_verified`、默认关闭状态、原请求摘要、normalizer 和工具接受判据保持不变。当前真实通道尚未证明输出计费硬界，不能伪造 verified、bounder 或正式许可来运行。

`experiments/planner_live/empirical_capture.py` 提供独立的服务端实验适配器。它使用明确的 `empirical_planner_text_once` kind/scope，绑定实际新意图、授权、风险预留、begin、来源、规则、上下文、原 wire 和截止时间。经验风险分配不是供应商报价或计费保证，字段中没有 formal grant／quote／permit。

两条路径共用 `capture_once`、独立 worker 和私有 `ReceiptStore`。正式分支先完成原校验，再进入中性采集层；旧 job／binding 不补新字段或改写摘要。不存在新增客户端网络执行路由，实验适配器不读取配置、默认密钥、来源文件或账本。

## 服务端调用接口

模块：`experiments.planner_live.empirical_capture`。

| 对象 | 绑定内容与职责 |
| --- | --- |
| `EmpiricalIntent` | 本地32位capture ID、实际intent/task/operation引用、授权/source/rules/context/wire摘要、端点/型号/输出参数、签发与原截止时间；固定kind/scope，支持synthetic或empirical_live环境 |
| `RiskAllocation` | 实际allocation/intent/operation引用、同一授权与intent hash、实际parent ledger和begin引用、begin时间；`risk_fen=100`、`status=submitting`、明确非硬计费界的risk_basis |
| `FrozenEmpiricalDescriptor.build(intent, allocation, context, wire)` | 严格交叉核对引用、上下文与完整payload；冻结实际context_json/wire，生成descriptor hash |
| `descriptor.frozen()`／`from_frozen(value)` | 保存和重建包含全部计算摘要的原冻结视图；普通`dataclasses.asdict`不能代替该持久格式 |
| `EmpiricalCaptureAdapter(receipt_root, settings=None)` | 接收服务端私有回执根；默认观察至原deadline，cleanup固定5秒 |
| `adapter.capture(descriptor, api_key)` | 唯一一次采集；仅在原窗口内完整提交、通知及校验后返回raw；不运行normalizer或工具 |
| `adapter.inspect(descriptor)` | 只读返回`{inspection: ...}`安全摘要；不派发、处理正文、计费、释放风险或恢复任务 |

`.intent_hash`、`.risk_allocation_hash`、`.descriptor_hash` 使用现有 `digest` 语义。操作引用允许已有 `TASK/D01` 形式，只作绑定；唯capture ID用于目录索引。实际存储根仍须处于公共静态目录之外。

## 正确的接入顺序

1. 可信控制器验证原授权、累计账本、既有unknown及调用／修正限额，冻结来源、上下文、原请求和新意图。先持久保存实际intent，不延长或重发旧请求。
2. 在原parent ledger实际预留风险并执行begin；由真实记录导出不可变head／record与begin引用，再构造`RiskAllocation`。不能用临时编造的摘要或`submitting`字符串代替实际操作。
3. 构造、持久保存`descriptor.frozen()`后调用capture。Key仅通过函数参数与有界stdin内存IPC进入worker，不进入描述符、argv、环境或日志。
4. worker检查冻结引用与claim，独占started marker后仅POST一次。完整record与commit seal先原子提交、fsync，再写独立ready；提交失败不冒充完整响应。
5. 及时raw交由调用者另行核对HTTP、ID、型号、完整usage、finish及原工具合同。工具实际接收、文字质量、平台费用和用户接受分别记录。完整回执但费用未核实，应保留风险并等待核费，不发下一次模型请求。
6. 新unknown即停止；迟到、冲突或不完整证据隔离。重开仅通过`from_frozen → inspect`核对原证据，不能重新capture消费旧候选或补发。

适配器验证的是可信服务端提供的冻结引用之间的关联；它不读取或独立证明整个parent ledger的余额、用户授权、供应商费率或计费上界。真实授权、预留及累计限额由调用它的控制器负责，本库不提供客户自报授权入口。

## 请求、时间与兼容边界

本轮实验仅支持明确的Chat文本请求。payload固定型号、所选输出参数与上限，messages由冻结context映射，`stream=false, n=1, temperature=0`；不支持SSE、hosted tools、协议／型号fallback或自动重试。完整payload严格匹配，额外字段拒绝。

context_json保留原字典键序，因为message.content本身是JSON编码字符串。语义context hash仍用`digest(context)`，精确wire hash用`digest(wire字符串)`；调用者另外记录的裸wire bytes SHA256不能混作同一算法。正式wire格式及旧hash不变。

新意图的接受期限不超过120秒，实验cleanup固定5秒，只收集已提交请求的证据。共用worker保留UTC cutoff和单调时间双界、150秒资源上限；HTTP各阶段使用原cutoff剩余时间，资源上限不扩大接受窗口。此前250秒平台关联错误不构成延期依据，也不能补造旧响应。

共用D14的完整性、容量、私有权限、凭证回显拒存与整组失败关闭检查：正文≤1MiB、record≤2MiB、IPC≤8MiB、每capture最多8候选、库64MiB／128操作。`candidate_found`仍为隔离证据，不等于工具接受或已核账。

`ReceiptStore.inspect(operation)`、`ready_raw(operation)`保留正式包装；新增`inspect_binding(capture_id, expected_binding)`及对应内部ready入口。原正式HTTP／CLI不会把实验目录冒认为正式执行operation，正式默认路径继续关闭采集。

本次控制器另限定最多4次Planner调用、2次修正，每次1元经验风险占用，并保留原100元账本中的历史风险。这是本次私有试跑范围，不是适配器向任意调用者授予的额度，也不是第三方收费硬上限。
