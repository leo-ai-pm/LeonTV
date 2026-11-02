# D51 媒体状态只读性能独立验收

2026-11-02（Asia/Shanghai），入口 `027d3e408bafd8abac1986b33e6b092268269b21`。

**结论：七模块局部优化及只读集成接缝有限接受；真实90帧的同机≤5秒目标未达，性能目标保持开放。** 在下述源码和有限证据范围内，未发现阻碍本地集成的实现问题。真实HTTP最慢14.255秒，距现有15秒超时仅0.745秒，不能据此宣称生产负载下稳定无超时、生产可用或完整Harness验收通过。该结论不是整项性能目标完成。

本次依据冻结的统筹集成报告、后端公开validation、最终源码清单、源码映射和固定结果作一次最终审查；末尾补核了随后到齐的后端 `RESULT.json`／`HANDOFF.md`，关键结论一致。运行与测量由后端、统筹执行；PRD依据必要源码／测试文本／报告作静态核对，没有运行应用、Runtime、测试或网络请求，没有读取原90帧账本、真实剧本、Key或配置。

| 条件 | 独立判断 | 依据与边界 |
| --- | --- | --- |
| MR01 真实路径与性能口径 | 读取路径、计时来源及口径证据接受；真实5秒目标不通过 | 实际ASGI和另行真实本地HTTP均有证据；首次与后两次重复请求分别记录。有效合成69帧、原90帧及旧D50到期分支分别判断，计时见下表。真实目标未达，不由合成或到期快分支替代 |
| MR02 校验与新鲜性 | 有限范围接受 | 原完整逐帧校验仍在；同步GET内嵌套读取重新打开同一路径、核对设备／inode和完整字节，返回独立副本。纯计算复用绑定完整类型化输入或完整不可变字符串，计算消费捕获键时的同份冻结输入；权限、时间、资金门禁、当前源码和图片文件结果未缓存。有限文件／proof／源码／账本／撤销变化样本准确反映，结束／异常／线程／嵌套边界有确定性回归 |
| MR03 只读ASGI接缝 | 有限范围接受 | 统筹将16个实际ASGI正常响应送入未改前端，另核对1个原生损坏503拒绝；这些包括重复和profile样本，不是16种独立业务状态。合成样本有独立task／source／brief及native绑定；原90帧HTTP绑定从隔离副本的原生task提取，未冒称task API。另有6次真实本地HTTP经现有API／controller均ready。各次读取零写；两HTTP服务全fixture文件不变、外部尝试0，已停止并关闭端口。未做浏览器UI或共享服务部署验收 |
| MR04 版本、回归与保护 | 有限范围接受 | 最终426源、native-04公开390源和105文件权限闭包有统筹逐项映射；PRD独立核对本次7生产＋6测试／工具文件哈希，均与最终和实际测试清单一致，7生产同时匹配权限闭包。99相邻回归及最终1个复合检查按下述版本计数。旧测试／依赖、前端和历史保护未改的结论来自统筹完整审计，PRD未重复读取全部保护实物 |

**计时和可比性。** 以下均为同机隔离副本，首个数为该进程首次GET，后两次为重复请求；不等于全机器冷启动或生产负载试验。

| 输入／实际路径 | 基线三次（秒） | 最终三次（秒） | 判断 |
| --- | --- | --- | --- |
| 原90帧，无managed持久化授权，实际ASGI | 22.659／22.370／22.287 | 12.995／13.093／13.253 | 同一原生SHA，DTO一致；前后均每次完整校验1次，中位数降低约41.5%，仍超过5秒 |
| 新69帧、测量时有效Loaded权限、managed＋legacy，实际ASGI | 4.694／4.680／4.677 | 1.733／1.649／1.648 | 同一最终校验器和输入，仅切换旧GET编排／新GET；完整重建3→1，源码与文件检查仍各1次，DTO一致 |
| 上述有效合成69帧，真实HTTP→现有前端API／controller | 未测同口径HTTP基线 | 1.699488／1.651071／1.683775 | 三次ready，managed权限valid、文件available；只证明该合成样本 |
| 原90帧，真实HTTP→现有前端API／controller | 未测同口径HTTP基线 | 14.255051／13.914300／13.819330 | 三次ready，最小15秒余量0.744949秒；真实5秒目标未达 |

原90帧基线为初版三模块编排及尚未优化的校验器，最终为冻结七模块；原生输入SHA始终为 `829fbcaf5f6ac3a90dcc625aa063977c1e5cbf46a694f9a988e5360d3b8748e8`，完整payload与独立逐帧fold一致。基线使用保留在测试辅助文件中的旧GET算法，不是旧进程二进制重跑。相同帧数不代表相同工作量：新69帧以合法quote补帧，只有两个提交位置，不等同D50 retained／runtime-heavy69帧。

D50历史有效GET的14.553／14.204秒仍保留。该样本旧权限已到期，本轮没有续权、假时钟或当前有效runtime重测。早期24.659秒cProfile来自store→control→current-runtime子路径，不能冒充当前有效完整GET。最终兼容报告只证明旧D50到期ASGI响应、managed文件可用／登记及两条旧unknown保持，不据此判正常有效路径达标。

**实现与有限回归。** 六个旧生产文件为 `store`、`media_runtime`、`media_persistence_snapshot`（说明文字）、`image_policy`、`media_ledger`、`image_code_continuation`；新增 `validation_read_proofs`。PRD检查了完整键、捕获后输入变化、bool／int／float／负零、CORE／task／historical和REQUIRED／EXTRA、容量回退、失败不入池、防御性副本及上下文清除路径。当前GET同步执行且没有await；本次证据不支持把复用范围扩展到异步共享或跨请求。原完整历史校验及当前文件准入仍保留。

后端最终相邻回归99通过／13.57秒，外部网络尝试0；禁用插件自动加载的一条`asyncio_mode`配置警告不计失败，也不作跳过或降断言依据。native-04为1个复合检查，pytest总118.40秒、场景计时117.662秒，二者不相加。实际执行公开源390份、权限闭包105份，与最终426清单绑定；清单文件SHA为 `71e79d7a3def82b7e27fbe6db849db7f8323a41e09c36da3811bb6b0dc771209`。早期13／28／44组与99重叠，不累加；较早native-03的通过不替代最终native-04。

native-04组装仅用文本／资产MockTransport、四条同步合成图片raw及离线PNG proof写入；图片capture、实际下载worker、模型输出与质量均未验证。读取阶段有限覆盖managed文件缺失、坏字节、坏proof、源码变化、原生损坏、合法head70、撤销、unknown和恢复。分支在自有合成fixture的请求之间构造；零写指各次读取本身，不将组装／构造分支也描述成零写。unknown样本是测试接缝，不证明真实unknown未发生供应商动作或费用。

native-01的229.24秒失败保留：新测试在两个位置已用完时请求第三次提交，原生产限额正确拒绝，修正限于自有合成分支。native-02的15.70秒进程计时与15分30秒墙钟间隔／期限失败保留，挂起或时钟变化只是未证实假设；未重试其unknown，未改生产限额。新合成样本在出生前设置有界初始期限，持久化权限至北京时间2026-11-02 17:32:42.825995；它只是该合成样本的初始权限，不是对原样本或真实账户续权，后续到期读取不能代表有效授权计时。

统筹保护报告显示：入口419源仅上述六旧生产文件变化，新增七文件后为426；旧测试／依赖无变化，前端116无变化，保护3269及原失败现场30无变化。PRD核对统筹集成报告列出的12个固定证据文件SHA全部一致；这是报告／本次源码核对，不冒称亲自复跑测试、HTTP或完整保护审计。

**保留事项。** 原90帧最终cProfile总18.047秒，完整验证17.804秒、媒体验证15.687秒／67次、摘要计算5.215秒／105074次，数值互有包含，不能相加，也不能当无profile时延。剩余主要成本仍是结构性历史重放、摘要及引用证明；实验性digest／解码缓存未交付，不能用其实验数充当交付性能。性能目标尚未关闭，后续结构性性能工作与真实unknown处置／未来生成准入分开继续；本次不另排测试、扩矩阵或要求全历史suite。

原真实native90／parent47、正式2/4／unknown3、原100元／保留风险83.37元／cap55保持，风险不是消费。COMP01仍开放，原50通过／1失败未被绿色新检查抹除。此次不验真实画质、视频、人验或完整Agent Harness；无真实生成、续权、增资、自动重试或付费执行。

主要依据：同目录 `evidence/LTV-MEDIA-READ-PERFORMANCE-20261101/` 下的 `coordinator-integration-review.json`、`coordinator-static-review.md`、`coordinator-source-match.json`、`coordinator-integrity.json`、`coordinator-api-decoder.json`、两个HTTP client／server结果、`backend/final-source-manifest.json`、`backend/{SOURCE-MAP.json,RESULT.json,HANDOFF.md}`、`backend/related-final/result.json`、`backend/native-04/runner-result.json`及`measurements.json`、`backend/original90-read/report.json`、`backend/original90-final/report.json`、`backend/final-compatibility.json`、`backend/d50-expired-read/report.json`，以及公开 `MEDIA-READ-PERFORMANCE-VALIDATION.md`。具体SHA和机器结论见本轮 `prd/RESULT.json`。
