# 完成图片本地持久化验证 · D47

2026-10-31。对照[API](./MEDIA-PERSISTENCE-API.md)及[PRD MP01–MP14／P01](./MEDIA-PERSISTENCE-PRD.md)。404份冻结版已完成本轮有限实现验证：相关466项、原生综合1项、关键私有综合1项通过，P01配对测量完成；本页不会将fixture准备、缓存调试或文本审阅计作最终功能通过。真实媒体R均not_run。

## 入口与版本

入口source `dd6cde1`／docs `562629b`，395份源码、2440保护文件逐份SHA核对零漂移。原native90／parent47、正式2/4、unknown3、原100元授权／风险83.37元／包络55不改，风险不是消费。旧tests／旧证据／运行状态／配置／依赖／前端不写，后端专项不操作Git；统筹负责本地集成提交，不push或重启共享服务。

功能验证快照public404份，runtime348份；5份旧生产模块改动、5份新生产模块、4份新tests／fixture，旧tests逐份不变。功能验证source manifest SHA256 `dfc47b1caec9ad47521e88005df2adb38c0347cfef4eb3d24db7cd8cbcec9178`，runtime manifest文件SHA256 `a839a6f1083d924ced0de65e7e4ef51f1d40d4c9ba6b182f0c40c3cd80478613`。实际版本映射以私有本轮backend/FREEZE.json及每组result.json为准；最终若修复将明确新freeze，不混称原版本通过。

## 已有测量与调试事实

- 最小快照红测：事务＋两次控制读实际执行3次完整validate_state，1失败／2通过，0.14秒；修后同3项通过0.12秒。返回原已提交状态的防御副本，不复用可变payload。
- 2000空载完整native帧的同候选proof-disabled测3次验证，seconds 0.064154／0.030009／0.025767；proof-enabled为1次，0.034751／0.009194／0.009014。仅机制对比，非媒体长历史或真实SLA；该早期测量已由下述最终75帧媒体历史配对计量补充。
- 统筹最终独立4例通过0.10秒（总0.268秒），验证inode替换、线程、嵌套root、KeyboardInterrupt清理、同inode新hardlink等；见coordinator-snapshot-final-result，源码匹配freeze。另独立worker0.248秒自行TIMEOUT、无根kill／网络；Ruff14文件通过；原真实90帧只读兼容23.24秒、payload／原文件一致、禁止写／联网尝试0。不能代替接收链验证。
- 早期IO／snapshot38项、42项、44项通过，递增且重叠不加算。最新durable组46项通过7.31秒，外部socket/DNS尝试0、本地连接10；新增完整proof JSON在fsync阻塞时worker自行TIMEOUT、无耐久确认。只计该组实际快照。
- 原生fixture准备260.40秒与completed准备92.21秒仅准备。首接收调试74.73秒失败，暴露通用ledger未把新receive状态交给自己的转换器；修复精确kind后，缓存调试综合1项通过620.01秒，外部0／本地4。缓存authority使用早期synthetic public-backend，不作为最终匹配源码端到端验收。
- D46 ENTRY151.613480秒未达120、ISSUE108.830374秒在dispatch内、慢尾未测仍是历史事实，本轮无真实平台调用来重测它。

## 最终验证方法

`backend/run_isolated.py`只复制baseline public白名单与本轮新source/tests，strip凭据环境，子进程继承外部DNS/socket拒绝。显式本地传输seam把唯一synthetic公共域名的DNS／固定地址连接／TLS流映射到loopback假HTTP；URL／地址规则、HTTP读取、父子进程及独立alarm、原生权限／store／receipt、完整解码及文件逻辑均是生产实现。loopback plain stream不是真实TLS证书／握手验收；独立connector测试检查固定地址、单次连接和TLS hostname／CERT_REQUIRED配置。

最终原生从零生成合成四镜头／原五提交／三unknown及新比较授权，不复制真实账户。fixture在任何授权hash生成前复制冻结runtime348份的真实字节，authority backend与实际执行stage逐份SHA相等，并保存matching-authority-source.json。不能改旧授权manifest或mock validator来补配。正常首次query完成由现有产品入口直接调用真实生产子进程，自动持久化至原artifact；另建明确独立的synthetic journal fork验证中断／404／重放，不是产品允许重置机会。

完整保存原生各阶段输出与旧记录／parent保护，正常链分列completed、意图提交、worker启动、首请求、proof耐久、私有发布fsync、native saved、artifact及返回耗时。影像质量、人验、视频、普通制作delivery不在本轮技术通过范围。

## 最终有限验证映射

| 条目 | 实际证据 | 结论与限制 |
| --- | --- | --- |
| MP01–MP03 | native-final：出生前opt-in／严格同意，旧operation补挂拒绝，重封policy扩权拒绝，完整规范query与固定URL绑定；相关旧描述符／费用攻击回归 | 所列场景通过；未穷尽所有身份字段组合 |
| MP04／MP06 | native-final：404一次后重启不重发；意图后网络前中断／孤儿残片不补发；critical-final：同operation两个实际接收器竞争仅一个receive和一次资源请求 | 所列场景通过，失败／unknown与completed分开；多进程组合未穷尽 |
| MP05／MP07／MP08 | 最终相关466含46项新IO／snapshot检查：实际本地流超限、截断、MIME／编码、超时、路径／链接／覆盖、容量与锁；worker deadline独立终止；真实全解码及不覆盖发布 | 有限通过；本地TLS流seam不验真实证书链；未切电或实际耗尽物理磁盘 |
| MP09／MP10 | native-final：part/proof／blob后中断，仅本地恢复；完整proof缺确认或迟确认拒绝；原artifact已提交而receive指针未写时正确对账；critical-final：saved后损坏blob，真实artifact gate拒绝并报告不可用，同原字节恢复网络0 | 所列场景通过；物理故障／所有崩溃指令点未穷尽 |
| MP11 | native-final：part+proof与blob两阶段遇expiry／代码漂移，文件与native字节不变、网络0；原三独立capture新鲜边界保持；根最终snapshot／独立worker检查 | 有限通过，不改原120／180／5，无真实D46 ENTRY重测 |
| MP12／MP13 | native-final保存前置／直接artifact拒绝、旧记录／parent保护、正式图集不增；登记失败partial、登记指针对账、文件损坏／过期后缺失时历史事实与当前可用分开 | 所列场景通过，quality未审／human pending |
| MP14 | 所有功能验证stage source map逐份等于原功能冻结404，authority/runtime348逐份等于冻结runtime；最终2440保护项／140份旧测试文件全一致 | 后端结果与独立根复核分别记录；真实媒体R全not_run |
| P01 | 同持锁事务可信已提交snapshot；原安全有界读、nlink／inode／thread／异常清理；最终合成75帧／2,607,749字节配对实测 | 2次完整校验→1次；约10.3秒→5.1秒，仅此同决策场景，不是实际ENTRY SLA |

所有计数按实际节点和快照列出，不累计历史重叠通过数。本轮没有重跑全后端／前端；有限合成通过不等于真实媒体、全产品或Harness验收。

## 冻结版实际结果与计量

| 最终分组 | 实际结果 | 版本／网络边界 |
| --- | --- | --- |
| related-final | 466通过，345.69秒，退出0 | 完整404源码快照；外部DNS/socket尝试0，本地10；46项新快测已包含，不再相加 |
| native-final | 原生综合1项通过，1061.14秒，退出0 | 从零生成同348真实字节授权，完整store／receipt／解码／生产子进程，外部0／本地5；独立合成journal分支，无真实账户 |
| critical-final | 私有综合1项通过，150.26秒，退出0 | 最终匹配源码的合成基线；并发接收与实际坏blob门禁；外部0／本地2；finally恢复原合成native及文件 |
| performance-final | 私有计量1项通过，36.22秒，退出0 | 相同最终75帧媒体历史，外部／本地0，原payload与文件不变 |

四个功能验证stage的404份source map都逐份匹配原功能freeze；下述仅测试入口标记后补不解释为当前404测试字节全部重跑。native-final不是此前缓存调试的620.01秒结果；fixture PREPARE_ONLY／COMPLETE_ONLY不计功能通过。真实平台未运行。本地HTTP传输仅验证本轮软件链，真实TLS证书／握手、模型质量和资源托管可靠性未验。

正常首次query由现有执行器自动调用真实生产worker，网络／解码／私有持久化至原artifact，无手工下载或登记。规范completed→资源首请求25.605318秒，→耐久proof25.929277秒，→私有发布fsync完成31.323427秒，→链返回64.021388秒；worker启动→耐久proof0.859535秒。`normal-first-completion.json`保留意图实际提交、首请求、proof fsync后确认、发布、native saved与artifact时间，`NORMAL-PATH-TIMING.json`给派生值。这些是同源码合成／本地流计量，不能推断真实URL一定及时可用。

P01最终使用冻结代码，在静止的75帧、2,607,749字节完整合成媒体历史，运行真实 `store.transaction → LoadedImageControl._verified`。同候选关闭／开启只读proof交替两轮：关闭10.275511／10.307783秒、各2次完整validate_state；开启5.166878／5.120818秒、各1次。每轮payload及native文件SHA完全不变，348源码逐份匹配。比较的是同最终候选的优化开／关，非原真实版本前后；不缓存权限、不合并三处fresh、不提升为真实SLA。

## 审阅问题、保护与交接

DR01三入口、DR02先意图／证明及本地恢复、DR03期限／当前写门禁、DR04资源与状态均有上述有限执行证据。IR01由critical-final实际在native saved后破坏blob、调用原writer验证拒绝和不可用摘要；IR02由native-final在原artifact提交后令receive指针写失败验证原登记事实仍True／pointer_pending；IR03由最终安全有界snapshot与相关组／根4例验证。BND01由真实worker proof-fsync阻塞时自行TIMEOUT、确认未产生，以及native完整JSON但缺失／迟确认拒绝共同覆盖。所有状态仍可说明部分失败，不把技术登记当质量通过。

后端最终只读核对：404源码无漂移、runtime348一致、2440原保护项零变化、140份旧tests／fixture文件SHA不变；原真实native90／SHA `829fbcaf5f6ac3a90dcc625aa063977c1e5cbf46a694f9a988e5360d3b8748e8`、parent47／SHA `421a28c025f81547f7d3e69e848027b3243a86aac7dc2142060c675d3281a79e`保持。根另以隔离公共代码对原90帧完整只读审计23.24秒，payload一致，写／联网尝试0。根最终4项snapshot、0.248秒独立worker期限及14文件Ruff证据与freeze匹配，分别记录，不累加成一套总测试通过。

私有本轮 `backend/RESULT.json`、`HANDOFF.json`、`FINAL-PROTECTED-REVIEW.json`、`FINAL-PERFORMANCE.json`、`PRIVATE-CRITICAL-RESULT.json`及各`*-result.json/log`保留完整命令、退出码、耗时和源SHA；根独立证据为`coordinator-{snapshot-final-result,worker-deadline-final,ruff-final,final-version-audit,actual-readonly-compatibility}.json`。

实际边界：无真实Key／外网API／付费生成／旧任务重试，无前端、配置、依赖、旧测试改动或共享重启；本窗口无Git／push。D46旧A继续accepted／completed、平台费用0.04、资源一次404且原因未知、无图／artifact；原unknown3、正式2/4、质量未审／人验pending与原100元授权／83.37元风险／cap55不变，风险不是消费。

未覆盖：未来真实provider/CDN与TLS端到端、物理断电／真实ENOSPC、全部并发进程／身份×时序组合、普通制作／视频／UI以及完整Harness。本轮后端实现和有限验证已交付，剩整合／独立有限验收归统筹及PRD，未来真实执行另按明确适用权限安排，不追授或重试D46。


## 测试入口标记后补（test-only，无生产改动）

统筹最后指出两项测试依赖隔离runner：`test_native_media_persistence_future_task_and_recovery`需要匹配源码的私有合成路径／worker传输seam；`test_worker_independent_cutoff_rejects_complete_JSON_before_proof_fsync`需要子进程继承外网阻断及fsync注入。默认pytest缺这些条件时原会在准备后失败，不能作为普通无配置测试执行。

现在仅在这两个新增测试入口加显式skipif。隔离runner设置`D47_ISOLATED_RUNNER=1`及网络guard日志环境；原生项另要求`D47_MATCHING_AUTHORITY_SOURCE=1`，按原命令提供`D47_AUTHORITY_MANIFEST`。两项测试的函数签名及主体字节完全不变；其余IO／snapshot测试不跳过。标记不是运行权限，也不代替产品授权。

在公共隔离副本、默认pytest临时路径、外网硬阻断下，实际运行新IO＋snapshot＋native文件：**45 passed／2 skipped，5.95秒，退出0**，外部DNS/socket尝试0，本地9。两个skip均显示明确隔离条件原因。显式隔离条件下仅做收集及marker值核对：47项收集，两个依赖隔离的skip条件都为False，退出0，未执行它们的主体。

当前public仍404份，只有上述两份新增测试的入口装饰器不同；新manifest为私有`backend/test-entry-final-source-manifest.json`，SHA256 `968ba1028a216831eb27a50cbf40d53029148302a206425fadc940b442d7d052`。生产runtime348及其manifest SHA `a839a6f1083d924ced0de65e7e4ef51f1d40d4c9ba6b182f0c40c3cd80478613`完全不变。既往466相关／1061.14秒原生／150.26秒关键补验对应原功能快照 `dfc47b1caec9ad47521e88005df2adb38c0347cfef4eb3d24db7cd8cbcec9178`；未重跑466、17分钟原生或fsync注入主体，不夸称新404全字节通过旧运行。主体字节hash、两次命令／退出码／收集标记及日志见`TEST-ENTRY-FINAL.json`及`test-entry-final-*.log`。统筹以单独test-only本地commit记录，不push。
