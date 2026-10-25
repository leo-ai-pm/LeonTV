# 图片历史校验与故障阶段验收 · D35

后续进展：[D36续接预审](./IMAGE-CONTINUATION-PREFLIGHT-VALIDATION.md)已完成；下文保留D35当轮结果，原有效期和历史未知未改。

2026-10-25，代码`74cb4c7`。[接口与边界](./RUNTIME-VALIDATION-PERF-API.md) · [上一轮真实结果](./TASK-FEE-CONTINUATION-VALIDATION.md)。

本轮减少了重复历史读取及图片请求的重复纯重建，补充本次采集失败时的固定阶段观察。原第二镜仍为unknown，原policy和permit已到期，未续期、重发或新增交付。修复经过历史数据只读回放和模拟采集验证；有效授权下的真实reserve→prepare耗时仍待验收。

## 复现与修复

三个反馈环均在改生产实现前出现预期失败：

| 调用点 | 原结果 | 修复后 |
| --- | --- | --- |
| 真实continuation类的单次verify/guard | 每次完整历史读取2遍，2项断言失败 | 每次读取1个新鲜snapshot；不同决策仍各自完整读取 |
| 原生journal中的同一POST描述符 | 一次validate_state重复纯重建6遍，1项断言失败 | 同次重建1遍；下次读取重新验证 |
| 原生采集authority抛异常 | unknown/rawNone已持久化，但阶段证据缺失，1项断言失败 | 新失败封存固定阶段观察；unknown与原调用消耗保留 |

continuation的根因是`_eligible`已经验证原权限，之后用于收集实际文件的`current_code_manifest`再次调用同一权限入口。内部纯采集函数移除了这次重复调用；独立公开入口仍完整校验，Key加载前后仍各做独立门禁。

请求描述符只在同一次同步journal重建中复用成功的完整输入证明：先严格JSON检查，按整个序列化内容匹配，每次返回防御拷贝；最多32项/8MiB，满额退回完整验证，退出和异常均清除当次集合。该集合不存权限、时间、撤销或文件状态，也不跨独立读取复用。全量frame、媒体、素材、费用owner和continuation历史规则继续执行。

## 真实历史的只读对照

对同一份52帧原journal，分别使用上一轮已提交的324文件源码及本轮329文件源码。探针禁止socket、事务、任务锁、执行器/Planner构造、session、Key和capture，配置模块未加载；不是恢复或重新派发原请求。

| 指标 | 原实现 | 本轮 |
| --- | ---: | ---: |
| 一次读取中完整描述符重建次数 | 665（545 POST/120 GET） | 4（2 POST/2 GET） |
| 不同完整描述符 | 4 | 4 |
| 逐帧媒体验证次数 | 29 | 29 |
| 两次guarded read_only样本 | 6.855/6.956秒 | 3.824/3.878秒 |
| 直接validate_state样本 | 7.341秒 | 4.155秒 |

返回payload指纹完全一致。绝对耗时受机器负载影响，样本不构成SLA；历史重建仍为秒级，不能把665→4等同整个运行时提速166倍。剩余素材manifest、native_context、JSON拷贝和逐帧业务检查继续保留，本轮没有进一步缓存这些累积状态判定。

当前到期授权仍按原时钟拒绝。没有通过修改时钟、原policy、permit或activation制造真实派发成功，也没有以静态路径估算替代有效窗口内的端到端验收。

## 故障证据的实际含义

新sidecar只保存**最后进入的固定观察阶段**、完整原请求绑定、时间和seal；它不是确切异常原因或某阶段已完成的证明。HTTP与费用结果始终为unknown，retry_authorized固定false。不保存异常文本、脚本、Key、URL或请求体。

首次文件不可覆盖，私有路径、权限、单链接、完整binding和严格JSON校验均保留；写诊断失败仍保存原unknown。原journal、descriptor、permit、raw和receipt的schema未改，预算或次数不释放。只有当次submitting记录可写诊断，历史unknown/accepted/reserved不能回填。

原操作端status可读取验证后的安全投影。对原第二镜的真实只读验证确认：状态仍unknown，failure_diagnostic为missing，dispatch/query均不可用，automatic_followup_calls为0；显式尝试给历史unknown补阶段被拒绝，未创建sidecar目录。此探针未构造执行session、加载Key、写事务或发网络请求。

## 检查范围

公开后端324→329文件，修改7个旧执行模块，新增1个阶段证据模块和4个测试文件；317个旧文件SHA相同。新增50个测试项（纯快照3、continuation性能3、独立攻击10、故障阶段34）。

同一329文件冻结源码的完整回归分4个不重叠桶执行，覆盖全部87个测试文件和2501个收集项：

| 范围 | 通过 |
| --- | ---: |
| 新合同、continuation与快照审计 | 262 |
| 图片操作、回执与客户端 | 472，含36子测试 |
| 媒体、素材账本与continuation性能 | 269 |
| 其余后端 | 1498，含37子测试 |
| 合计 | **2501项、73子测试** |

50项新增检查已包含在全量内。12个变动文件Ruff及diff检查通过。两个既有multiprocessing fork警告保留，没有失败、跳过或放宽断言。隔离副本仅含公开代码、虚构控制和模拟HTTP；父进程与detached worker的启动路径均加入临时socket封禁，网络尝试日志为0。该临时辅助文件不属于329文件清单，也未加入实际backend或Git。前端未改，未重复UI/build或重启共享服务。

针对性及独立复核均与全量重叠，不相加：图片policy/receipt/快照151项、continuation相关34项、故障阶段及原capture/runtime72项；独立10项快照攻击与34项故障阶段复核通过。攻击包括完整seal重封、bool/float、非JSON容器、GET父证据篡改、失败不缓存、返回值修改、嵌套异常、线程隔离、后续重封frame、私有路径和首写不可覆盖。单独导入新增依赖链通过，文本capture无trace时hook不创建状态。

## 原数据与当前交付

本轮开始时的1996个受保护文件字节全部保持一致，包括原52帧journal、回执、费用、控制文件、父预算和图片。原scope/grant仍各1个，POST意图2个（首镜accepted、第二镜unknown），原GET2次、已交付1张，首镜平台任务quota观察仍为¥0.040000，最终账单未确认。

原100元授权、保留风险¥81.37/保守53次及父45条记录均不变；风险不是消费。后两镜未预留，原未知不清除。没有真实新生成、查询、费用认领、续期或代码activation，没有新视频/音频或视觉/人工验收。quality仍not_reviewed。

## 下一项

处理原未知请求与已到期范围的版本化续接：保留原记录、消耗和风险，显式披露后续有效范围；性能修复和阶段观察均不自动授权重试。接通后再在实际历史体量和有效许可内验收reserve→collector.prepare，随后完成图片、产品视觉Reviewer和条件局部返工，再推进视频与人工终验。

前端、共享服务、模型配置、Key和平台分组均未改；本轮不需用户重新登录或补Key。浏览器未启用，代码本地保存，未推送GitHub。
