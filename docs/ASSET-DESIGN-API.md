# 素材设计与可追溯参考图 · D31

D31 在原 `ExecutionStore` 中增加可选 `asset_design` 扩展：从实际已接受的分镜和审查回执构造素材输入，显式授权一次素材文本调用，校验角色、场景和逐镜首帧 manifest，观察费用后再向独立的图片范围交接。图片范围可另行披露 `reference-derivation-v1`，把原生前镜交付转换成后镜可用的参考图。

素材调用、图片调用与原分镜文本授权各有自己的范围和同意记录。默认产品启动不装配素材调用权限；普通状态查询不能生成内容。D31 的最终源码、全量测试与真实只读核验状态见[验收文档](./ASSET-DESIGN-VALIDATION.md)。

## 素材输入与一次调用

输入来自原生 normal 任务中已接受的 v1 首审或 v2 复核，模型收到实际来源、分镜、审查报告与确切绑定。服务端另行核验原始回执及费用事实，不把供应商raw或费用导出当作素材提示词。`native_context` 从同一原执行 journal 重建证据；复制一份合法 JSON 或自行计算 hash 不能代替原生来源证明。

素材合同仍为 `product-assets-v1`：1～12 镜，每镜一张 9:16 写实影视单张首帧；角色与场景全局定义，逐镜只引用其 ID。镜头 ID、顺序、时长、原依赖和剧情不改。首镜不引用前镜；其余镜头至少引用一张已存在的前序镜头，不能引用自己、后序或虚构镜头。服务端统一编译首帧提示词，模型不提供媒体 URL，也不声称图片已生成。

当前 `AssetDesignPolicy` 固定 `gemini-3.8-flash`、`/v1/chat/completions`、`max_completion_tokens`，每 scope 仅一次调用，最多 8192 输出 token，输入 wire 最多 1MiB，输出最多 64KiB，请求时限最多 120 秒且受策略到期时间约束。每次保留 ¥1 经验风险，`risk_basis=empirical_not_hard_billing_bound`；这不是供应商收费上限，也不是本轮实测单价。没有自动修复调用。

## 服务端调用顺序

`core/execution/asset_design.py` 提供：

```python
AssetDesignExecution(
    runtime,
    *,
    policy=None,
    budget_anchor=None,
    authority_guard=None,
    fee_source=None,
    receipt_capture=None,
    credential_loader=None,
)
```

默认缺少策略、预算锚点、独立 authority guard 和费用来源时关闭。guard 必须独立验证实际父授权、分配和原生接受版本，返回严格 `True` 且不得改动输入；测试中的合成放行函数不构成真实授权。

| 顺序／方法 | 实际行为 |
| --- | --- |
| `quote(task_id, key=...)` | 冻结来源、接受版本、完整素材输入身份、策略、父预算、费用来源与风险披露；保守保留整个 ¥1／1 次 scope，不发 HTTP |
| `grant(task_id, quote_id, disclosure_hash=..., consent=True, key=...)` | 记录对确切披露的独立同意，不自动开始 |
| `reserve(task_id, grant_id, key=...)` | 在原 journal 先保存唯一收费意图、exact wire／context、descriptor 和 permit；不发 HTTP |
| `start(task_id, operation_id)` | 再次检查授权、截止与全账户门禁，先持久化 capture 开始标记，再由原 detached collector 发一次请求 |
| `recover(task_id, operation_id)` | 只采纳原 collector 中同 binding 的已有回执；不发模型请求 |
| `observe_fee(task_id, operation_id, export_bytes, observed_at=...)` | 重建原始导出，费用 candidate 与全局 claim 在同一事务落盘 |
| `verified_manifest(task_id, operation_id)` | 仅当 manifest、回执、费用及 claim 均可重建且未超风险时返回 manifest 和 provenance proof |
| `revoke(task_id, grant_id)`／`get(task_id)` | 撤销后续派发／只读安全投影，不释放已保留风险 |

有效结构输出在缺费时为 `awaiting_fee`，不能交给图片范围。核费通过后才为 `manifest_ready`；超风险为 `blocked`。坏 JSON／结构错误为 `invalid`，截断为 `truncated`，未知回执或超时为 `unknown`，均不自动修复。相同意图的 `start` 重放、重启、换 key 或换 grant 不重发这次调用；未知后的恢复只找原回执。

## 显式本地操作员入口

在后端目录使用 `python -m core.execution.asset_operator`。不传 `--control` 只有 `status` 可用，返回 `mode=off`、`dispatch_available=false`、`automatic_followup_calls=0`；不会读取旧文本控制环境变量来启用素材调用。

控制文档必须通过 `asset_design_control_v1` 的严格校验：私有且互不重叠的 creator／planner／execution 根目录，确切任务及原生接受版本，冻结 policy、budget anchor、scope envelope、费用来源，实际父授权／账本／allocation begin 原字节 hash，以及完整受控代码 manifest。当前配置合同对应原 ¥100 总范围，继承至少 ¥75.37／47 次冻结父范围，并为素材设计单独保留 ¥1／1 次；已有图片或素材范围必须计入新的 opening，不按实际已花金额回收差额。不能拿自封控制文件或旧文本 grant 替代这些依据。

`observe_only` 仅允许状态读取，拒绝所有写命令；`enabled` 仍需每次派发通过 authority、代码、源版本、预算及截止核验。构造 session 不加载供应商 Key、不启动文本执行或费用线程；只有真实且验证通过的 `start` 才调用 credential loader。合成 worker 与真实 transport 互斥。

以下命令展示实际参数；私有路径及 ID 都是占位符，控制文件需由独立授权流程提供：

```sh
python -m core.execution.asset_operator status
python -m core.execution.asset_operator --control "/private/path/asset-control.json" status
python -m core.execution.asset_operator --control "/private/path/asset-control.json" quote --key asset-quote-001
python -m core.execution.asset_operator --control "/private/path/asset-control.json" grant --quote "<QUOTE_ID>" --disclosure-hash "<DISCLOSURE_HASH>" --consent --key asset-grant-001
python -m core.execution.asset_operator --control "/private/path/asset-control.json" reserve --grant "<GRANT_ID>" --key asset-reserve-001
python -m core.execution.asset_operator --control "/private/path/asset-control.json" start --operation "<OPERATION_ID>"
python -m core.execution.asset_operator --control "/private/path/asset-control.json" recover --operation "<OPERATION_ID>"
python -m core.execution.asset_operator --control "/private/path/asset-control.json" fees-import --operation "<OPERATION_ID>" "/private/path/original-export.json"
python -m core.execution.asset_operator --control "/private/path/asset-control.json" status --operation "<OPERATION_ID>"
python -m core.execution.asset_operator --control "/private/path/asset-control.json" revoke --grant "<GRANT_ID>"
```

`--control` 也可放在子命令后；所有子命令可选 `--task`，值必须与控制文档准入任务相同。`--key` 仅为 quote／grant／reserve 必填，费用文件是 `fees-import` 的位置参数。没有 `--model`、`--provider`、`--manifest` 或任意提示词覆盖参数。

如旧文本费用尚未建立 canonical request claim，可用同一已验证控制的 `legacy-fees-import --operation "<TEXT_OPERATION_ID>" "/private/path/original-export.json"`。仅允许同一父账户内的真实旧文本意图，不扩大素材准入任务；原费用与旧别名不改写。缺少可重建的原始导出时保持关闭。

CLI 输出为安全摘要，不输出原剧本、prompt、raw、完整 manifest、导出字节、Key 或私有路径。代码漂移／策略到期阻止新 quote、grant、reserve、start；在有效的 enabled 本地控制下仍可读取历史、采纳已有回执和核费，不借此重新派发。status 的 `control_authority_valid` 与 `dispatch_available` 分开，控制有效不代表还有可调用次数。

## 原生 manifest 到独立图片范围

`core/execution/asset_media_handoff.py` 的 `manifest_origin(payload, task_id, operation_id)` 重新核验实际素材回执、费用、claim 和 manifest，返回 `manifest` 与 `native_asset_manifest_v1` origin。`media_from_asset_design(runtime, task_id, operation_id, **settings)` 用它构造 `MediaExecution`，禁止调用者另外覆盖 `manifest` 或 `manifest_origin`。

这一步不发 HTTP，也不授予图片权限。调用者仍须提供独立图片 policy／budget anchor／authority guard／fee source，再执行[媒体范围](./MEDIA-EXECUTION-API.md)的 quote → grant → reserve_submit → submit → observe_fee／query → record_delivery。新图片 quote 披露 `manifest_origin`，绑定素材 operation、proof、输入和 manifest；验证会重建原生事实，不能只替换 hash 来换一份清单。

素材文本 grant 不能用于图片，原文本 grant 也不能升级为这两个范围。图片 POST 的 accepted task、GET、实际交付和平台费用仍按 [D29](./CONTROLLED-IMAGE-TOOLS-API.md)／D30 合同分别验证。

## 参考图转换必须另行披露

`MediaExecution` 新增可选 `reference_profile=None` 和 `manifest_origin=None`。二者均缺省时保留 D30 scope／quote／operation 的原字段和 hash 行为；不会给旧 scope 回填新字段。原有未声明转换的范围仍只直接使用 PNG／JPEG 参考图，受原容量限制，不能静默转换 WEBP。

转换须在新范围显式传入 `ReferenceProfile(allow_lossy_jpeg=False).frozen()`；如确需有损压缩，使用 `True` 并通过新 quote 披露和独立同意。`reference-derivation-v1` 冻结转换策略、禁止操作、固定 JPEG 阶梯，以及 Pillow／JPEG／WEBP／zlib encoder 版本；profile hash 参与 scope 身份和披露。旧 quote／grant 不能用于新 profile。

后镜只从同 scope、前序镜头已登记的原生 query／delivery 读取原始字节，按 manifest 的引用顺序派生。新 profile 不接受 Agent 自传参考图、路径或 URL。原交付和 `first_frame_version=1` 不改；派生 representation 另记 version 1，并在同一事务与目标镜头的 reserved intent 一起出生。

| 情况 | 固定处理 |
| --- | --- |
| 合规 PNG／JPEG，单图不超过 1MiB | 优先保持原始字节；仍检查包含全部参考图、base64、prompt 和 JSON 的实际 wire 总量 |
| WEBP，或原图不能直接作为参考图 | 先用 decoded RGBA 像素生成 PNG，压缩级别 6；尺寸和像素不变，不 resize／crop／EXIF rotate |
| 无损方案放不进原 wire 上限，未允许有损 | 明确停止，事务不留下派生记录、幂等 key 或收费意图 |
| 明确允许有损且无损方案超限 | 对不透明源图按 JPEG 85 → 75 → 65、subsampling 2 依次尝试；每次从原 decoded RGB 编码，检查所有引用的总 wire，首个可用方案即止 |
| 有透明像素 | 保留无损表示，不把透明层铺底或压成不透明 JPEG；仍超限则停止 |

源交付仍最多 2MiB，实际单帧 PNG／JPEG／WEBP，9:16、每边不超过 4096，像素数不超过 32Mi；坏图片、非 identity EXIF 方向、ICC／gamma 元数据拒绝。派生参考图仍单图不超过 1MiB，完整请求仍不超过原策略上限且至多 1MiB；原 descriptor 和 worker IPC 的 8MiB 上限、journal 的 64MiB 上限不扩大。

`reference_derivation` 记录精确来源 operation／query／delivery、原 bytes SHA／大小／decoded pixel hash、目标 job／shot／operation、profile、输出字节／SHA／大小／尺寸／MIME、encoder、质量和每次本地尝试。输出为 `visual_quality=not_reviewed`；像素相等不代表角色、构图或艺术质量已验收。

新事务出生时严格重新计算当前 encoder 的派生结果；历史重建保留已记录表示，不覆盖或悄悄重编码。同 encoder 可重复计算加强验证；encoder 变化时核对原始归属、记录字节、像素／尺寸及完整绑定，旧 profile 不能继续生成新派生，需显式新 profile／quote。D29 descriptor 不增加转换字段，实际参考字节仍进入原 exact wire；转换 proof 在媒体 namespace 中关联该意图。

## 统一预算、费用与读取边界

原文本、图片和素材文本复用同一 journal、锁与账户门禁。`scope_budget.scope_envelope` 计算原账户及两个新 namespace 的完整冻结额度和次数，保留未用 scope，不拿实际费用替换风险分配；同一父 allocation 不得跨范围复用。费用 claim 按平台导出 request ID 的 canonical key 跨三类意图去重，不能因 operation UUID 相同就视为同一工具归属。

素材文本核费当前沿用文本路径：按固定 token／group／model、回执输入输出 token 和规定时间窗口筛出唯一平台消费行，再以该行 request ID 建立 claim；没有匹配或多行匹配则保持待核费。**这不等于已经证明 raw 响应直接携带并匹配该平台 request ID。** 图片费用继续要求原生成请求 ID 的直接关联；task ID 和 GET 请求 ID 不能代替 POST 请求 ID。费用导出的原始字节 hash 和首次 `observed_at` 都冻结，精确重放不刷新观察时间。

任一范围未知或待核费会阻止同账户新的收费请求；任一费用或本地观测超风险会保留完整暴露，并阻止同账户新文本、素材文本和图片派发。零费用、撤销、到期、核费完成均不释放风险。状态中的 `paid_call_count` 统计已保留的收费意图，不能当成供应商已成功生成或已收款次数。费用观察保留 `risk_released=false`、`provider_bill_confirmed=false`，不实施退款或最终结算。

只读产品接口为 `GET /api/planner/tasks/{task_id}/execution/asset-design`，返回 `{"asset_design": ...}` 安全投影：scope／意图计数、保留及额外暴露、费用是否观察、状态和可用 manifest hash；`allowed_actions=[]`、`quality_status=not_reviewed`。它不返回完整 manifest、原文、prompt、raw、图片字节、signed URL、费用导出或私有路径。

该 GET 禁止 body 与未知 query，校验 32 位任务 ID、静态目录重叠与存储完整性，缺少原执行记录返回 404；不创建任务／namespace，不启动 observer、恢复或收费调用，响应为 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。没有新增素材写入 HTTP API、前端生成按钮、图片本地操作员 CLI 或自动 Agent 调度。

当前实现为后续受控实测提供代码接缝；是否完成真实检查以验收文档为准。视觉模型审阅、人物一致性验收、局部返工、视频／音频生产、自动费用拉取、退款及最终账单结清仍未由本合同完成。
