# D50 retained-unknown 正式图片持久化验证

2026-11-01，Asia/Shanghai。入口721348025a21f40fd554c29c428068d3259d1e6e；本窗口未操作Git。结果为**合成原生接缝通过，真实执行仅准备**。COMP01继续开放，没有因本轮绿测而定因或修复。

## 一条实际链

复用公共two_unknown_case及现有ExecutionStore、资金／费用记录、RuntimeContinuedImageControl、MediaExecution和D47接收worker。新建合成上下文包含4个历史image operation：2条unknown、2个已登记delivery、已保留的费用和4＋1累计位置。它不是原真实账户的90帧或3条unknown重放。

在同一合成原scope／grant／账户中，现有retained-unknown quote／activation披露2条旧unknown和最后一个已有资金位置。准确typed session创建未来S03 replacement：出生前persistence quote／activation显式opt-in，submission_index=5，前镜绑定准确原operation／query／delivery，不引用unknown。一次模型POST accepted，准确费用观察，一次规范同任务GET completed，然后共享实际子进程worker单次本地HTTP接收373B合成PNG，完整解码／SHA／耐久proof，登记原delivery。没有用diagnostic artifact替代交付，没有新账户或另一套工作流。

typed control、原validator／原生历史重建／回执／费用／引用检查均实际执行。fixture生成只将公开源码字节冻结到新合成授权；历史unknown沿用既有合法native转换和missing-supplier fixture，不冒充真实provider观测、零费用或零HTTP证明。旧runtime permit的截止和清理窗口真实经过，没有修改时钟或伪造更早确认来跳过门禁。

## 运行与实测

从backend目录运行公开自包含入口：

```sh
.venv/bin/python tests/run_retained_image_persistence.py --output /absolute/new-persistent-output
```

目录须新建，失败现场不自动删除。新scenario由runner显式选择，无新增skip或私有evidence依赖；不将长复合场景混入默认历史重跑。仅复制公开代码／固定测试数据，剔除真实config.yaml、.env、harness_data及密钥环境；外部DNS/socket硬阻断，仅允许明确合成本地HTTP server端口。

`backend/native-01/runner-result.json`及pytest.log：**1复合节点passed，541.05秒，exit0，外部尝试0，本地worker接收1次**；没有运行全部历史suite或D49长前缀。541秒包括合成历史、权限／资金校验、旧permit等待和全链重放，不是生成或45秒下载worker的SLA。

主要断言已通过：

- 旧原始LoadedImageControl不能越过现有unknown门禁；deferred S04不能消费位置，原文件不变。
- 新operation在出生时准确opt-in／runtime activation／job／原grant绑定，前镜只取准确已登记原delivery。
- accepted但本次费用尚未观察时，新接收权限拒绝；观察准确费用后才进入规范query／接收。
- completed自动登记原delivery；同keyquery、recover、submit重放不增加模型HTTP或资源接收，私有执行目录指纹不变。
- 合成旧unknown2条、旧fee／delivery／其他namespace、原frame前缀和父ledger字节不变；原合成风险81370000 units（81.37元）／cap53不变，image operation4→5、delivery2→3。风险非消费；这些数字不冒充真实83.37／55。
- 第六次reserve被拒，没有暗加位置、清unknown、释放费用风险或补写原记录。新增diagnostic artifact为0。

模型计数见model-counter-audit.json：合成setup和目标合计text mock3、asset mock1、image mock6；image6为3个accepted POST＋3个GET，历史missing fixture不能由此推出真实unknown未执行。目标增量仅image POST1＋task GET1，另资源worker GET1。native-result中的all_model_http字段指image counter6，不是所有text／asset之和；分类审计明确总mock supplier10。所有真实provider调用0。

## 实际只读ASGI

`backend/native-01/asgi-fixtures.json`含2次实际媒体GET，以及同源的独立Planner task／source／events／creator brief响应。新S03为saved、received/persisted=true、原delivery_registered=true、file=available、quality=not_reviewed、human_acceptance=pending；旧legacy delivery只保留历史登记，不自动升级为新私有文件已核验。证据类别synthetic明确保留。

每次媒体GET前后及所有重放后，私有**执行目录**完整文件SHA指纹一致；同源task/source/brief GET导出亦未改变该目录。没有返回URL/raw/base64/Key或绝对执行路径，不调用observer、接收、恢复或发布。实际两次媒体GET为14.553／14.204秒，接近已有前端15秒超时线；这不是宽裕的性能证明，未读取真实90帧作基准，也未保证真实账户可在同一时限读回。样本交统筹通过现有frontend decoder核对，后端不改前端，不以静态样本代替生产性能或真实生成。

## 最终源码与测试边界

最终后端419项＝入口417＋2个新tests／runner，生产模块及全部旧tests字节未改。新合成授权固定闭包104项与实际执行运行时代码及最终工作区逐项SHA一致。实际隔离公开副本383项；完整最终源／保护见backend/FINAL-SOURCE-CHECK.json和final-source-manifest.json，清单SHA256为dbfa887e16f9700f5c461bc1099aac5360df55f9c2a2d3a2db3b150419053aea。

原生长测启动后，最终新test/helper有2处明确差异：测试中禁止读Key且从未被调用的lambda回调改为同体def以通过Ruff；测试runner把同样的本地端口限制也加到connect_ex。运行时104项未改。最终guard短检查证明connect／connect_ex仅允许该合成端口，另2次故意错误端口被阻断且没有外部发网；这2次是独立人工guard负例，不是native试验的额外下载／模型调用。未为无运行时变化的测试整理重复541秒长测；diff保留在native01-to-final-test-tools.diff，不冒称全部测试源逐字相同。最终2文件Ruff通过，初次Ruff格式问题不算功能失败或通过。

原417、前端116、保护2532及COMP01原失败现场30逐项SHA保持；本轮只做保护指纹，不加载真实Runtime／config／Key，不写原native90／parent47。没有重跑D49无信号前缀／全部历史测试，没有依赖／前端／PRD／AGENTS修改、共享服务重启、Git或push。

## 真实一镜仍未就绪

未来准备包在backend/FUTURE-ONE-IMAGE-PREPARATION.md：精确待绑定原task／source／plan／grant、3条真实unknown及其证据、最新源码／lease、现有资金位置、出生前opt-in、一次动作和停止条件均单列。公开摘要的最后D46 GET-only观察lease已于北京时间2026-10-31 23:30结束，本就不授权未来正式POST；旧4＋1位置已消耗，raw-present第三条unknown不能借合成2条的通过默默准入。没有创建新task／grant／model ID，没有签发源码／lease续权或新增资金。

原真实native90／parent47／2/4／unknown3、原100元／保留风险83.37／cap55不变。此准备不是执行授权，不自动重试旧D46 A，不提升费用／图片质量／人验结论，更不等于视频或完整Harness。后续仅由统筹审核合成接缝与计划，真实门禁未满足时保持停止。
