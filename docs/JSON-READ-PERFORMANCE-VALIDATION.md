# D54 JSON读取性能：有界字符串编码复用

2026-11-04。入口 `a64b7e34ae3e29579b0f7ffa2d7fc5d8f992d7aa`。既有后端负责实现，统筹负责集成／Git，PRD独立验收。

本轮保留一组有限的JSON编码改进，**原90帧5秒目标仍未达到**。同源码入口两次实际ASGI为 **11.435229／11.445932秒**；最终431源码三次为 **10.565187／10.643690／10.755348秒**。每次独立进程、非profile、实际媒体GET200／no-store，完整90帧验证一次，完整payload与独立折叠相等，DTO与冻结参考相等，输入字节不变，外部网络尝试0。前端15秒保持；只据本轮本机对照判断有限收益，不宣称生产SLA、普遍提速或真实5秒完成。

## 源码与范围

入口429份，最终431份。旧生产模块只改 `core/creator/store.py`、`core/planner/review.py`、`core/execution/image_policy.py`；新增纯模块 `core/execution/json_read_codec.py` 和 `tests/test_json_read_codec.py`。其余426份入口文件、旧tests／依赖SHA不变。无前端、AGENTS、PRD、统筹文件或Git改动。

最终清单 `evidence/LTV-JSON-READ-PERFORMANCE-20261104/backend/final-source-manifest.json`，文件SHA256 `9a157e14c37949fd08fb86978bdd8a88daf9546e22954647ca928d6b9677c1ea`；规范映射树SHA256 `0520c9394bdbf5a8d122c3fb43aa7f89b6d74716d466893b2f30e09c02faea04`。stage395／authority107以及实际fixture权限源码均逐项匹配，新模块确在两者中。

## 诊断与是否保留的判断

先冻结入口并建立真实红信号：本轮 `read_baseline.py` 使用入口源码、原90只读副本和冻结DTO，第一次11.435229秒，5秒断言退出1；数据／字节检查通过。精确命令和阶段记录见 `backend/PROGRESS.md`。

在探测前列出三条可证伪假设：相同长字符串重复转义；digest与json_copy可以共享这一纯标量结果但必须保留解析；编码器设置／检查／回调开销可能抵消收益。没有重做D51无净收益的整对象digest缓存，也不跳过容器、key、数字、循环或历史检查。

实际调用点红回归让同一长Unicode／转义字符串经过digest和json_copy的4次完整序列化：标准库字符串转义12次，期望1次，0.12秒失败；改动后0.13秒通过，并验证下一次读取重新构建、拷贝输出的可变别名仍分离。候选正常原90 GET10.178134秒，候选源码／结果单独留在 `candidate-01/`；该早期数字不冒称最终版本数字。

最终profile暴露约1061万次Python转义回调。为避免仅凭一次入口较慢就接受复杂度，再做允许范围内第二次入口实际GET，得到11.445932秒，与第一次相近。两次入口均慢于最终三次10.565–10.755秒，因此保留本组本机有限收益，同时明确额外回调成本；不再调容量或扩热点。

## 精确语义与边界

新模块在既有单次同步描述符／原生验证边界内建立一个有界标量池：只保存**exact `str`全文对应的标准库字符串转义结果**。不保存JSON整文、对象摘要、校验结果、来源、权限、时间、文件或网络结果。

- JSONEncoder和原C编码器仍遍历本次完整实际对象、排序原keys、处理数字、默认分隔符及循环。仅长度至少4096字符的exact str可命中全文键；不对str子类调用新增hash／equality。相同内容的不同字符串对象可复用，改动后缀必须重编码。
- 64项／16MiB字符存储预算，按字符长度与固定开销计费，不是进程RSS上限。完整输入和结果仍按原路径处理；容量满时正常编码，不放宽schema或限额。
- 编码模式及实际编码函数必须匹配。读取退出清空池和函数引用；嵌套、异常、复制出去的context、不同线程及退出后继续持有的编码器均不能延长复用。编码过程中scope退出也不能重新填入已清空的池。
- 使用CPython现有C encoder接口，保留原默认构造选项；C encoder不可用时走标准库原迭代器，接口不匹配时回退原标准库实现。该私有接口和新增Python回调是本组维护限制，不能据此宣称所有Python版本和所有负载均提速。

`creator.store.digest` 的原 `json.dumps(sort_keys=True, ensure_ascii=False)` 默认空格和默认allow_nan语义保留；没有全局收紧宽输入。读取边界外直接调用原 `json.dumps`，该fallback调用AST与入口相同。

`planner.review.json_copy` 仍按原顺序执行 exact-type `json_value`、allow_nan=False、UTF8字节limit和原 `parse_json`。原require／json_value／parse调用及原json.dumps fallback AST相同。没有用deepcopy取代序列化／解析，因此相同可变对象出现在两处时仍被JSON解析打散为独立对象。标量来自最终失败的文档也不会成为整个文档通过的证明，后续整体错误仍重新抛出。

所有旧帧hash、transition、记录／跨记录、完整来源、最终与特殊历史规则保持；当前权限／时钟／源码／文件仍由原边界读取。新增标量池不授权生成、续权、下载或费用动作。

## 验证与profile限制

有限回归 **109 passed／6.77秒**（19新、90相邻旧），Ruff通过。覆盖默认空格、Unicode／转义／ASCII模式、float负零／nonfinite、digest宽输入、严格JSON拷贝、keys／tuple／subclass、超大整数、循环／深输入、UTF8边界与别名、整对象变化及失败、容量、嵌套／异常／复制context／线程、编码器变化和C encoder不可用。禁用pytest插件自动加载时保留既有 `asyncio_mode` 配置警告。旧tests未改，未跑COMP01原失败batch／prefix或全历史suite。

另列profile总18.081822秒，含探针和大量Python回调事件，**不能当正常GET或总成本下降证据**。观察到缓存命中6283、miss241、保留23项，预算计费16,770,916字节；转义回调10,614,999次。原C内部工作部分计入新模块 `iterencode` 和 `escaped`，不能仅看标准库 `encoder.iterencode` 行下降就宣称消除成本，父子累计也不相加。额外回调可能抵消部分收益，短字符串负载不据此作普遍提速结论。保留决定依据上述两次入口与三次正常GET。

最终原90 SHA `829fbcaf5f6ac3a90dcc625aa063977c1e5cbf46a694f9a988e5360d3b8748e8`，三个5秒断言仍失败，所有数据／字节检查通过。剩余完整JSON处理、复制、摘要与复杂关系成本继续保持；本轮在此封版。

## 最终当前权限样本与交接

冻结后仅一次本轮新合成compound **1 passed／131.34秒**，内部场景130.282675秒，不相加。stage395／authority107完整匹配。正常有效权限ASGI **1.677551／1.667500／1.678386秒**；旧GET编排与同最终校验器的3→1完整验证对照另记，不能替代本轮入口源码对照。

新scope出生UTC2026-11-04T05:31:13.702920，初始4小时policy在scope前设置，初始3小时50分managed期限在quote／image operation前设置。**managed到期北京时间2026-11-04 17:21:14.643182，policy到期17:31:13.115664**。未改时钟、未续旧fixture。实际task／source／brief均200，与native source／plan绑定一致；missing／坏bytes／坏proof／源码变动／坏native503／合法70帧／撤权／unknown及恢复均通过，原69帧字节最终保持。

quote-heavy69仅用于当前权限读取接缝，不替代原90、D50 runtime-heavy69或内容生成质量。完整实际响应、出生／到期、路径与启动方法见本轮 `backend/native-01/handoff-current.json`、`backend/RESULT.json`、`backend/HANDOFF.md`。

D53受保护1647文件／5链接及两份公开报告匹配入口，规范树前后 `40e53a5b16ad605509d6b62660765233afa3928996e40d9bd7d66f152fc459c9`。更广11681文件／33链接、frontend116、原失败30的独立保护复核归统筹。后端采样已停止，未启动HTTP服务；统筹串行做真实HTTP和未改前端解码，PRD独立验收，本报告不预先宣称通过。

原native90／parent47／formal2/4／unknown3／原100元／风险83.37元／cap55保留，风险不是消费。真实5秒和COMP01开放，D46旧A不重试。无真实Key／config／execution-session／provider／生成／下载／费用／增资／续旧权／旧retry／共享重启／push。无浏览器UI、媒体质量、人验、完整Harness或Personal Agent完成声明；无需用户再次Key、登录或原预算。
