# 已受理诊断任务观察续行验证 · D46

2026-10-31。对照[PRD CO01–CO12](./IMAGE-COMPARISON-OBSERVATION-PRD.md)与[API](./IMAGE-COMPARISON-OBSERVATION-API.md)。状态：实现及有限离线验证已交付，统筹本地提交`dd6cde1`；随后真实quote／activation／精确费用导入各一次、同任务GET一次completed。平台费用观察0.040000元，单次下载HTTP404后停止，未取得图片字节、artifact0；404原因未确定，只读收尾完成。ENTRY→claim151.613480秒未通过120秒入口目标，ISSUE→claim108.830374秒在派发期限内。保留CO05费用碰撞及CO07边界组合未全交叉的缺口，不宣称全覆盖。

## 入口保护

从`c911985`的388份源码开始，统筹D46 entry-inventory的2326份保护文件逐份核对一致，native85／parent47不变。加入统筹原始浏览器导出三文件及精确费用bundle／candidate／check与只读核费脚本四文件，最终保护共2333份。最后一轮完整原生读检时已核对2332份，随后补核第2333份固定脚本及全清单仍无变化；统筹另独立核对原2326及新增7份。旧D45 evidence、旧测试、前端、配置与依赖不改；本窗口无Git命令或共享服务重启。

原 A accepted、request-id missing。统筹原始任务页的 SUCCESS／quota20000／URL 与纯候选0.040000元单列为平台观察，不视为原生 GET、图片取得或费用导入。原三unknown、正式2/4、质量未审及人验pending保持。

## 测量与最小性能改动

原85帧带 cProfile 的一次只读为26.615秒，Key／网络／事务尝试0，保护文件全不变。主要成本是反复 JSON 序列化／复制、descriptor重建和原上下文验证。根据剖析与统筹只读评审，只做两项纯计算简化：

- 同一次 native read 的完整、严格 descriptor 输入，取消“先json_copy、再第二次序列化”的重复工作；始终检查真实 JSON 类型、finite、UTF8及原默认空白字节上限，用完整输入而非调用者 seal 作纯证明，返回防御性拷贝。同读结束即清空，不保存权限／文件／时间结果，不合并三个 Key 边界。
- `media_ledger` 对整个media的json_copy返回值本来丢弃；保留原严格类型／finite／UTF8／48MiB计量，只取消无用parse。未改变旧数据／hash／descriptor。

第一项精确热路径红测在原实现出现16次重复json_copy，0.30秒失败；修改后通过。第二项红测在原路径仍出现一次无用copy，0.19秒失败。原始日志保留，不把fixture setup错误当作语义红。带插桩的后续25.293秒与并发负载时30.179秒存在波动，**不据此宣称稳定总延迟改善或真实派发SLA通过**。真正GET路径另外减少新profile签发后的reserved事务，三次新鲜边界保留；其版本及验证单列。

## 最终离线证据与源码边界

- 纯合同／描述符／性能组一次37项通过，15.466秒，公开源码隔离副本，继承外部socket拒绝，尝试0。之后补充了严格JSON计量／入口拒绝用例，须以最终组复验为准。
- 原生合成历史一次性建立原五提交／三unknown及单A accepted。首次准备流程在保存测试checkpoint时把Path交给bytes hash，352.57秒后fixture输出失败；A已规范受理。更正测试输出后复用该纯合成历史，不重跑历史慢流程；不是D46产品语义红测或真实付费验证。
- 最终395源清单SHA256为`e04d9596e35943d744401b104dd95f0cd7af8701df9fc268b3bd2c3f192ed40a`，343份runtime closure。9份旧生产模块改动、3份新生产模块与4份新测试／fixture；旧测试逐份SHA不变。统筹16文件Ruff／diff通过并本地提交，无push。
- 完整命令、退出码、耗时、socket计数及各次stage源码映射在本轮私有`backend/*-result.json`。未重跑全套后端或前端，不把历史D40／D45通过数与本轮累加。

| 分组 | 结果 | 版本与限制 |
| --- | --- | --- |
| 旧合同／兼容，含D40原生路径 | 375通过，472.638秒，socket0 | 前一个窄修复快照；共享生产差异只剩`image_policy`的输入顺序修复，新D46模块的中间差异不用于声称其功能通过 |
| 最终合同／旧policy／receipt／fee／双身份相关 | 346通过，26.210秒，socket0 | 全395份精确最终快照；与375有重叠，含49项新policy／性能边界 |
| 原生观察综合 | 1项通过，378.328秒，socket0 | 精确生产代码仅`image_policy`尚为修复前排序拷贝；最终上述组覆盖恢复原输入顺序。测试差异仅新增该回归断言 |
| 最终native迟到ready | 私有独立1项通过，35.42秒，禁止网络／Key尝试0 | 最终生产源码；从综合组保存的纯合成claim前unknown分支恢复，不重建慢历史、不使用真实账户；原collector格式、权限、store和decoder保持真实 |

375与346的实际测试节点并集426项，另有综合1项和私有迟到1项；这是分组、混合快照的覆盖说明，**不是最终全套一次428通过**。collect-only仅用于节点去重，不计为新的测试通过。逐份差异在私有RESULT中列明。

综合组实际验证：当前source漂移使旧批准失效、新观察quote／activation严格同意；费用待核时不能GET、错任务／账户拒绝；同exports／新调用时间幂等重放；普通和诊断费用同身份不能跨owner认领；source collector新增文件拒绝；post-Key代码变化无HTTP，claim前中断无HTTP并保守unknown，换key不清除未决；独立合成分支复用同一基线后，单次GET规范completed、same-key／recover不重发、三个实际capture guard调用、独立artifact与重放、到期拒绝四类写动作、完整历史／parent／风险／旧三unknown／正式2/4保护。

合成GET测得ENTRY→claim41.440516秒、ISSUE→claim27.934840秒，三个独立新鲜边界；受合成数据、插桩及当时并发负载影响，不是原任务实际SLA。私有迟到分支实际构造完整正确、及时到达的completed body，ready在原response截止后1秒；原ReceiptStore读出候选，但新runtime保持unknown／raw None，重复恢复不增帧、原collector字节不变。最终原85未插桩只读17.359304秒，control加载17.760256秒，原payload与文件SHA保持；Key／网络／原生写入尝试0，不能代替88帧后的真实GET预算测量。

## CO验收映射

| 条目 | 实现／证据位置 | 当前验证边界 |
| --- | --- | --- |
| CO01 | bindings／source_collector／_fresh_quote | 原 A 独一绑定；policy攻击及原生collector漂移 |
| CO02 | quote／activate／history checkpoint／verified_current | 显式新lease、新source、严格同意、CAS；旧批准不改 |
| CO03 | LIMITS／GET-only permit／禁止入口／原scope_budget | POST0／资金0／Bfalse，不放行其他制作 |
| CO04 | 原candidate_for_task／同原件重放 | 精确账户及task证据；首份observed_at不可变 |
| CO05 | candidate_gate／共享fee owner／累计query | 费用先于GET、typed双身份及跨namespace归属拒绝已测；所有原生碰撞组合未全交叉，保留缺口 |
| CO06 | 一次持久submitting意图／原_key／recover | 重启及换key不再发送，未决意图保守停止 |
| CO07 | _capture_guard三次fresh／claim2／响应合同 | 三次fresh及post-Key源码变化已测；各边界全部mutation组合未全交叉。120／180／5不延长；实际ENTRY151.613480秒未过目标，ISSUE108.830374秒在派发期限内，慢响应尾部未实测 |
| CO08 | 期限检查／本地recover／迟到ready拒绝 | 到期仅只读；新unknown不重发、不自动续期 |
| CO09 | verify_delivery／独立artifact | 合成字节链通过；真实单次下载HTTP404，未取得字节、未登记artifact，不宣称真实交付通过 |
| CO10 | 原生隔离kind／原reference_sources | 不新增正式delivery／参考，旧三unknown不变 |
| CO11 | status_summary | accepted／completed／received／artifact／费用与质量、人验分开 |
| CO12 | 每次stage manifest／最终交付清单 | 源快照与覆盖分组说明，不把合成当真实通过 |

## 冻结计划的执行门槛

本节保留本次派发前审查的合同与限制；实际已执行范围和停止点以末节为准，不构成再次执行授权。

最终源码、测试及具体计划经统筹审查后，才可显式批准观察quote／activation，再单独导入原精确费用。GET前先测当前原生历史只读耗时和剩余lease，结合三次fresh及事务／descriptor成本评估120秒派发预算，超出预算先停止。查询入场仍须大于600秒裕量，原生写入大于120秒；任何源／父／collector变化、未决、冲突或overrun停。每步有独占入口marker，工具中断只检查原native／collector，不重入。

私有`EXECUTION-PLAN.json`与`live_observe_a.py`已准备，逐步派发quote→activation→exact fee→只读预算→GET1→下载1→artifact；每个有副作用动作具独立O_EXCL marker、精确前序native SHA及计划／driver／395源码hash，工具中断后只核原native／collector，不重入。本次明确批准的新lease为2026-10-31 15:30UTC（北京时间23:30），旧截止未改；lease本身不授权重复或新增动作。post-fee两次真实fresh测量需满足`3×最慢样本＋25秒 ≤105秒`，且测量5分钟内、同native／activation／source，仍保留原120秒派发与独立新鲜检查。

原生规范completed后才能确定该query receipt／URL hash，另行批准下载1次，2MiB、stream检查45秒／绝对alarm60秒、网络分项超时10秒、无跳转／Key／自动重试；只存本次私有目录。成功后再以精确下载proof／字节SHA登记隔离artifact。脚本在冻结交付时仅AST／Ruff和真实只读inspect验证；随后单独派发的实际GET与下载结果在下节列出，下载未通过。原task平台成功、纯核费或新批准都不自动执行下一步。以下单列冻结后真实进度，完整制作、视觉及用户验收未完成。

## 真实分阶段进度与停止结果

统筹逐步审查精确source／driver／计划及每步前序native SHA后，分别派发以下动作。每个有副作用入口只执行一次，marker保留；只有下载进程以退出2记录失败，其他实际阶段原进程退出0。工具中断不重入，原D45提交脚本从未重跑。

| 阶段 | 实际结果 | 原生状态 |
| --- | --- | --- |
| 观察quote | 1次，精确原A及显式lease | 85→86 |
| activation | 1次，精确quote／disclosure，严格同意 | 86→87 |
| 精确费用导入 | 1次，candidate逐字段等于统筹原任务证据，0.040000元、overrun=false | 87→88；仅A的fee指针及新隔离fee记录 |
| 只读预算测量 | fresh两次22.863435／22.526755秒；3×最慢＋25＝93.590304秒≤105；在5分钟内审查派发GET | 88不变；Key／HTTP／原生写入0 |
| 同任务GET | 1个意图、1次HTTP；原claim2／完整raw／commit／ready，精确原任务，completed | 88→89 submitting→90 completed |
| 单次资源下载 | 精确该GET的receipt／URL hash；HTTP404后停止，无重试／跳转／URL替换 | 90不变，未取得图片字节 |
| artifact | 未派发、写入0 | 90不变，无隔离或正式交付 |

原A accepted、request-id missing、GET completed与精确平台费用0.040000元均保留。费用仍是平台观察，final bill未确认、风险未释放。原始浏览器task导出与规范GET回执的图片URL哈希均为`a4caf2a6913224f9420949e4cb61417c1ef556826ec24e2c9ec43d03e731eb0e`；单次下载在2026-10-31 11:53:19UTC（北京时间19:53:19）返回404。原因未确定，不能据此证明URL过期、删除、鉴权或路径问题；不切换URL，不重试，不发更多GET或POST，不用旧图替代。

查询计时以`ComparisonObservationExecution.query`调用前为ENTRY，claim为规范派发边界，prepare方法入口没有单独插桩：

| 边界 | UTC时间／耗时 | 结论 |
| --- | --- | --- |
| ENTRY | 11:45:21.417912 | 入口起点 |
| ISSUE | 11:46:04.201018 | permit起点 |
| claim | 11:47:53.031392 | ENTRY→claim151.613480秒，120秒入口目标未通过；ISSUE→claim108.830374秒，派发期限内 |
| raw／ready | 11:47:59.085143／11:47:59.097245 | 均早于11:48:04.201018派发截止 |
| response／cleanup截止 | 11:51:04.201018／11:51:09.201018 | 原180秒响应及5秒清理未延长；超过派发截止的慢响应尾部仍未实测 |

原生最终90帧，SHA256 `829fbcaf5f6ac3a90dcc625aa063977c1e5cbf46a694f9a988e5360d3b8748e8`。完整原85前缀与原记录保留，仅A的fee指针更新；A原raw／receipt／descriptor／accepted事实不变。parent47，SHA256 `421a28c025f81547f7d3e69e848027b3243a86aac7dc2142060c675d3281a79e`，无变化。源码395／runtime closure343冻结不变；2333原保护项仅预期native变化，其他0。新query collector7文件逐份记录大小及SHA，单candidate完整绑定，raw等于原生记录。

后端最后一次只读收尾22.709432秒，审计网络／Key／原生写入尝试0；统筹独立核对与后端终态一致，不重复慢测。原unknown3、新unknown0、正式2/4、质量not_reviewed、人验pending、原100元授权与83.37元／55保留风险包络不变；风险不是消费。本次闭环只证实原任务受理、完成回执和平台费用，未证实图片可交付、视觉质量、视频、AB或完整Harness验收，也不解释历史403。

完整阶段原件在私有本轮`backend/D46-{QUOTE,ACTIVATE,IMPORT-FEE,QUERY}-{RESULT,AUDIT}.json`、`D46-MEASURE-1793446965176428000.json`、`D46-DOWNLOAD-FAILURE.json`及`D46-LIVE-EVENTS.jsonl`；最终结果与2333保护／7份新collector交接分别为`backend/LIVE-RESULT.json`、`backend/LIVE-HANDOFF.json`。统筹复核为同轮`coordinator-live-closeout.json`。终态已停止在资源404，不再派发artifact或任何HTTP；保留全部已进入marker、计划及driver。下一范围由统筹／PRD据此审查缺失资源处置、完成后本地保存设计及入口性能工作，本交接不给予新的执行权限。
