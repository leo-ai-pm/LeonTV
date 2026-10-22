# Planner 私有回执采集与只读 inspect · D14 实现合同

状态：实现、后端合成故障验证及统筹独立验收已完成，源码/测试冻结；生产未启用，历史未知操作未恢复。基线 `fae86a0`。对应 R00–R09 验收草案，本切片不增加真实试验、执行或生产授权。当前结论与独立复跑见[验证记录](./PLANNER-RECEIPT-RECOVERY-VALIDATION.md)。

## 1. 接口与实际接缝

- 新增服务端配置 `ExecutionRuntime(..., receipt_capture=None)`。None 精确保留旧默认 ControlledTransport 路径；显式 ReceiptCaptureSettings 才接入独立采集传输，配置不进入 API 输入或 policy/wire/permit 摘要。生产 bootstrap 仍 disabled。
- 新路径在 **原 quote/grant/hold/intent/permit 已持久化后**、原 ControlledTransport preflight 校验通过后调用独立 collector。覆盖实际 FastAPI→runtime→传输接缝，不仅新增孤立模块；不改 normalizer、工具判据、D13 默认反馈或既有状态机权限。
- 新增 `GET /api/planner/tasks/{task_id}/execution/operations/{operation_id}/receipts/inspect`，无 query/body 参数。返回独立 `inspection` 安全摘要，枚举 missing/incomplete/corrupt/binding_mismatch/ambiguous/capture_error/candidate_found；只含固定状态、计数、绑定摘要、正文摘要与有限时间/隔离标志。无原文、上游任意字符串、凭证、路径或 traceback。
- 原 receipt_status/received_at、allowed_actions、reconcile 门禁及旧 API 投影不变。候选证据不代表接受/计费/执行恢复；inspect 始终 recovery_performed=false、isolated=true，不返回正文。
- 同时提供仅 inspect 的本地 CLI（显式私有execution根与task/op，输出同一安全摘要）；重开CLI不派发。新增纯只读 ExecutionStore 快照读取及独立 inspect 依赖。**不用 transaction/get/events/recover/base/process_receipt**；无 mkdir、lock 创建、journal 写入、迁移、工具/模型/查询调用。已知 op 无证据为200/missing；错 task/op 404，损坏存储503，所有分支均不创建文件/目录。

## 2. 独立 worker 与一次派发

已新增 `core/execution/receipts.py`、`receipt_worker.py`、`receipt_transport.py`，并完成 runtime/store/API 最小接入。

- 使用 `subprocess.Popen` 新解释器执行 worker（start_new_session、close_fds），不在 API 工作线程直接 fork Python 状态，不继承 execution 文件锁。worker 不属于父进程线程池/daemon thread；父观察进程退出后仍可采集。
- 已持久 intent→校验原 permit/bounder→私有原子 claim→启动 worker，一次派发 claim 必须先于任何 POST。worker 再独占一次 started marker；claim/started 已存在时不重启、不再 POST。spawn/IPC/崩溃不确定时保留 unknown/hold，不补发。
- 原 operation/policy/wire 与凭证仅经有界 **内存 stdin IPC** 传递；凭证不进入 worker spec、argv、环境或日志。持久 claim 只有绑定/时间/IPC 内容摘要与随机能力的摘要，没有 Key、原文或完整 wire。worker 验证 claim 与冻结 IPC 摘要后只发送这一个原 wire；不生成许可、不假造 bounder。
- worker 启动即根据持久 claim 建立 watchdog，stdin IPC 截断/阻塞也不使其无限存活。父侧有界IPC写入不阻塞观察截止；没有完整冻结payload就不开始POST。默认 worker 使用同一 HTTPX 一次请求语义与原 headers/wire，无重试、redirect、模型或协议 fallback。validation_only 必须使用服务端注入的合成 MockTransport factory；factory/config 只供可信构造与故障测试，不接受客户端字段。所有测试新解释器同样受 socket guard 约束。
- worker 不读取配置/当前试验，不碰执行 journal、账本或工具。stdout/stderr 不承载响应或异常文本。资源 watchdog 在固定 cutoff 后终止 worker；不依赖父进程清理。

## 3. 两种期限与提交跨界

claim 固定原 `permit.deadline_at`、本次 `observer_until = min(启动时刻 + server observation_wait_seconds, 原 deadline)`。重开观察/inspect 不改它们，也不延长请求/授权。

- 原绝对 deadline 后不得开始 POST；已提交的一次请求可在固定、仅供证据收集的 cleanup grace 内排空（默认5秒、服务端上限30秒），cutoff=原 deadline+固定 grace。该窗口不延长可接受期限、金额或继续执行权限，watchdog 到 cutoff 终止采集；inspect 永远不重启它。
- 完整正文到达后先进行容量/凭证回显检查，再私有原子持久化回执并 fsync 文件和目录；完成不可变记录与 commit seal 的 durable 提交后，才产生独立 ready 通知。通知不得先于完整回执提交；提交成功但未通知的候选仍可由只读 inspect 检出。
- 时序字段区分 response_received_at、commit_started_at 与提交后产生的 durable_confirmation 时间；不把提交前写入的时间冒称 fsync 完成时刻。任何通知自身跨界也按不利结果处理。
- **仅仍在原观察期间的 perform** 在独立读取且验证持久回执/ready 后，并再次确认当前时刻严格早于 observer_until 和原 deadline，才可返回 raw 走原严格处理链。响应先到但 durable 提交/通知读取已跨任一期限，一律隔离；不事后选较早时间放行。
- 观察先结束：runtime 保留 RESPONSE_UNCONFIRMED、unknown、原 hold/次数。观察结束不冒称绝对 deadline 已到：新增私有 observation-ended 异常分支不伪造 D12 timeout 分类；实际 D12 transport error 仍按原固定枚举处理。
- 之后的完整/错误证据由 worker 独立保存，**不写回 op.raw，不覆盖已固定 D12 transport_diagnostic**，不自动调用 process_receipt/工具/结算/恢复或后续请求。inspect 找到及时或迟到证据也只给隔离候选摘要。

## 4. 私有存储、绑定与完整性

根目录固定为 execution 私有根下 `receipts/`；0700目录、0600文件，不从客户端接受路径。继承静态挂载父/同/子目录拒绝及 symlink/逃逸检查；只读 inspect 连缺失分支也不创建存储。

原绑定包含 operation/task、snapshot/context/wire/policy/permit 摘要、grant/quote/account 身份及 **原 deadline 字符串**。绑定以原 journal 快照为准，字段缺失/错配不猜补。

- 原 wire_hash **严格沿用 digest(wire 字符串)**，即对原合同排序/ensure_ascii=False 的 JSON 字符串序列化求 SHA256；不是裸 wire bytes SHA256，绝不重解释旧值。
- 新 body_sha256 仅对完整保存的响应 body bytes 求 SHA256；新 envelope_hash 用原 digest 对白名单 envelope 字段求摘要。二者名称与算法分开。
- 私有不可变回执记录包含上述绑定、完整 body(base64)、有限原 headers/status、接收及提交阶段时间和完整性摘要。不可变 commit seal 引用 envelope_hash，完整记录+seal 为提交证据；独立 ready 仅为之后的通知。无 seal/半写/坏 hash 不认定完整。只有 ready 缺失时，inspect 仍可返回隔离候选且 notification_present=false；活动 perform 不因无通知候选自动执行。
- 临时文件 fsync→不覆盖的原子提交→目录 fsync；不覆盖既有证据。重复相同绑定/正文/HTTP状态/白名单请求标识只保留同一份；状态/标识或正文冲突均保守报告 ambiguous，不最后写入胜出。同正文却状态/标识冲突不以“相同正文”掩盖矛盾。
- 保持原1MiB HTTP响应body采集上限（HTTPX iter_bytes实际交给既有normalizer的字节）；私有记录文件≤2MiB，单op最多8个完整候选及有界固定错误记录，全库64MiB/128op。每个已分配op预留2KiB给固定拒绝/观察结束记录；新增op先在quota锁内检查再建目录，已有目标先幂等返回再计新增空间。超限拒存完整候选、保留固定拒绝标记，不覆盖/删除旧证据、不作成功或免费判断。具体容量常量在源码与测试绑定。
- 收齐前仅内存缓冲，不把截断/超大内容写为完整候选。检测到存储字段/正文直接或 JSON 解码字符串包含当前凭证，保守拒存原内容，仅写固定 CREDENTIAL_ECHO 拒绝记录；异常文本不落盘。凭证不作为正文被保存，也不写入异常/输出。
- 摘要校验提供本地完整性与原操作绑定，不声称供应商签名真实性。任何无效候选都阻止确认；保留原文件供另行核查，inspect 不修复它。

## 5. inspect 与兼容边界

inspect 只读现有 journal 快照和不可变证据，验证原绑定、record/commit seal/body hash、容量与格式；ready存在时额外校验其完整性，ready缺失不否定已提交的隔离候选；不写任何历史、claim、诊断、授权或账务。只有一个可核对候选也只标 candidate_found/isolated，不运行 strict normalizer 或工具。只有平台行/账单/未经核实查询接口不构成候选正文，不由缺账单推免费，出站始终0。

旧无 receipts/claim 的 journal 不回填。旧 fixture/来源/原预留/次数逐字节核对；原API无字段覆盖。已有 D12 diagnostic 与 raw 不共存的校验不改变，新证据始终独立存储。老路径默认仍兼容；本轮实际采集能力仅由明确服务端配置启用，生产不自动开启。

## 6. 合成故障验证与保留的初始红回归

已建立原实际 FastAPI→runtime→ControlledTransport 的基线红测试：1）观察/绝对期限结束后，合成完整 HTTP 到达但无隔离持久证据；2）已知 unknown op 的安全只读 inspect 路由不存在。两者先断言原状态/hold/次数/D12记录字节不变，再断言目标缺口。

已在同一受控runtime接缝显式启用新配置，把新增测试转绿；默认旧路径仍兼容。合成用例覆盖 R00–R09，尤其：

- 独立**进程**测试先确认worker已发起唯一请求且被release文件门闩阻塞、尚无回执记录；SIGKILL观察进程并wait确认退出，之后才创建release文件。独立worker PID收齐完整响应，接收时间不早于release且早于原deadline；重启/inspect新增 POST=0。此证明未用父进程内线程或固定延迟代替。
- 提交前失败/硬退出不发 ready；提交后、通知前硬退出，重启可只读检出完整持久候选（明确提交证据与通知是否存在，不因重启重发）。
- 观察先结束/原绝对期限先到、接收与 durable 提交/通知跨界、watchdog cutoff；都不自动恢复/结算、不得覆盖D12诊断。
- 逐个绑定错误/损坏/半写/冲突/重复/容量/私有路径/静态读取/凭证回显；HTTP/CLI重复inspect及所有错误/缺失路径前后目录清单、字节与账本不变。

所有真实HTTP/付费/外呼=0，公开源码隔离副本+合成策略/凭证/来源/MockTransport，parent与subprocess socket guard；逐轮保存日志/JUnit/清单；新解释器的网络guard使用统一的合成attempt记录，防止worker stderr隔离掩盖尝试。最终同源定向/全量、Ruff与保护检查后冻结SHA。证据目录 `docs/evidence/LTV-PLANNER-RECEIPTS-20261022/` 继承私有 gitignore，不纳入提交。

保留的初始有效基线红结果：**2 failed / 2.86s**，分别为迟到完整回执无私有证据、inspect HTTP404；原unknown/hold/次数/journal字节先行断言通过。网络guard outbound_attempts=0、real_model_http_calls=0、paid_calls=0。

## 审阅确认的提交与拒绝边界

真正完整提交点为 record 与其 commit seal 都已原子写入并 fsync 文件/目录；ready 是此后的独立 observer 通知。R01 注入在完整提交点前，残片无效且不通知；R02 注入在完整提交点后、ready 通知前，inspect 可找到完整隔离候选，但活动观察者不因未通知而自动处理。只有 ready “缺失”可由 R02 解释；ready 已存在但损坏/半写/绑定不符则阻止候选确认。

inspect 对整组证据失败关闭：任一坏候选、缺 commit 的候选、损坏 ready、绑定/摘要冲突都不能被旁边一份好记录抵消。独立新进程 watchdog 到 cutoff 后必须退出，以进程故障用例核对无后台泄漏；退出不证明供应商请求取消或免费。

转绿用例独立读取完整记录/提交证据，核对原绑定、完整 body bytes/hash、隔离结论，并确认 journal/账本/诊断不变；未以任意json文件存在代替完整证据。旧缺口红日志保持原样。


watchdog 使用原固定 UTC cutoff 与启动时冻结的单调时间资源上限双界，任一达到即结束；不是只在启动时把 UTC 截止换算一次相对Timer。合成时钟前跳/后退仅在独立测试进程内注入，不改变系统时钟，不延长原请求或资源窗口。该检查不用于推定历史真实 unknown 的根因。


## 7. 本地 CLI 与最终后端验证

从 backend 目录使用现有Python环境运行，下例路径与32位ID需换成目标操作；命令只读，不接受Key、模型或提交参数：

```sh
python -m core.execution.receipts inspect \
  --execution-root "/absolute/private/execution" \
  --task-id "00000000000000000000000000000000" \
  --operation-id "11111111111111111111111111111111"
```

输出为与HTTP相同的独立 `inspection` 安全摘要；错误输出固定 `INSPECTION_UNAVAILABLE`，不回显路径/原异常。旧journal快照仍执行原 validate_state/totals 的纯金额完整性校验，不进行新增费用授权、结算或写账。

最终冻结源新增 **68项**回归；定向 **450 passed / 51.75s**，隔离全量 **724 passed / 71.34s**，均0 failure/error/skip。旧测试字节未改。**211文件**定向/全量/当前清单相同；Ruff、限定路径diff检查和**56项源码保护检查**通过。原strict normalizer、工具判据、policy/wire/permit摘要、D13默认反馈及既有API投影保持；202个上一已验收源文件原样。

最终运行时间（UTC；原始精度及monotonic在机器证据中）：

| 范围 | UTC开始 | UTC结束 | wall耗时 | JUnit耗时 |
| --- | --- | --- | --- | --- |
| 定向450 | 2026-10-22 12:33:37.524386 | 2026-10-22 12:34:29.641263 | 52.116877s | 51.746s |
| 全量724 | 2026-10-22 12:35:51.349104 | 2026-10-22 12:37:03.272010 | 71.922906s | 71.334s |

最终回归串行执行，以可用的进程级 `caffeinate -i` 防止空闲睡眠，未改系统设置。首次并行旧门禁失败和相邻合成记录898.82秒UTC间隔均保留；未据此断定主机原因或放宽策略期限。其后旧失败组原样5项通过，再完成当前同源回归。watchdog的时钟边界对照红绿、R03加强前后副本也分别保留，未借旧副本结果冒称最终通过。

完整机器结果、9个修改/新增源与测试SHA、211文件manifest、R00–R09用例映射、日志/JUnit、跨进程socket guard及红绿历史见 `docs/evidence/LTV-PLANNER-RECEIPTS-20261022/`。该目录私有且排除Git。

全部为公开源码隔离副本与合成状态；后端实施与测试的真实模型HTTP/付费/网络出站均 **0**，没有读取真实Key/config/源剧本/raw/当前试验状态，也未启停共享服务或安装依赖。worker退出不证明供应商取消或免费；候选完整性、执行接收、账单、内容质量和用户终验仍须分别核验。此处不是生产或真实任务验收通过声明。
