# D48 正式图片持久化后端验证

设计日期2026-10-31，收尾2026-11-01（Asia/Shanghai）。本文件只报告后端受限工程切片；前端decoder／浏览器与PRD验收由对应窗口及统筹报告。没有真实模型／provider／TLS、内容质量、视频或最终人验通过结论。

## 已实现

原MediaExecution新增出生前显式quote／activation绑定，新operation默认关闭。准确原grant、镜头v1、job、下一累计submission index、task/source、typed control、源码与lease绑定同一原native store，原account／父资金／fee／unknown门禁保持。普通query首次completed、同key重放与recover采用completed统一进入共享D47一次接收器；完整及时字节／proof／post-fsync确认经复核后登记原delivery。诊断artifact不当作正式交付。

GET `/api/planner/tasks/{task_id}/execution/media`保留旧media，新增production_images。沿用private_inspection：无observer、执行会话、Key、外部调用、下载、发布或补写。当前文件事实与历史saved／delivery及当前权限分别表达；返回严格安全标量。准确DTO及部分状态语义见API文档。

## 冻结源码与运行入口

入口HEAD由统筹提供ad343618eda1cc0315f310a18c64f6e0ad017eae；本窗口不操作Git。最终清单在 `docs/evidence/LTV-PRODUCTION-IMAGE-PERSISTENCE-20261031/backend/final-source-manifest.json`：公开源412（原404＋4生产模块＋4测试／helper），改5旧生产模块，旧tests字节全保留。清单SHA256：`97d2620022756e1d1822d23008206f9e7d23bf2d314c1d2e3893816df525eb8a`。

public runner自行复制必要公开源码／固定测试数据，不复制真实config.yaml、.env、harness_data或私有证据；只用合成账户和凭据。新生成的104项原生fixed code manifest与实际执行源码逐项SHA一致；另376项隔离公开副本也与当前工作区逐项一致。104是本轮原生固定闭包，不是D47历史348项扩展清单；两者不混称。

从backend目录运行：

```sh
.venv/bin/python tests/run_production_image_persistence.py
```

可用 `--output /absolute/new-output-directory`保留源码清单、实际ASGI与结果。目录须不存在其public-backend子目录。默认pytest入口为 `tests/test_production_image_persistence.py`，封装同一自包含runner，无新增skip或私有D48路径依赖。直接scenario由runner显式提供，非默认收集节点。

## 首轮实测

`native-run-05/runner-result.json`：exit0，**1个复合pytest节点通过，122.20秒**，9项场景叙述不当作9个pytest通过数。真实ExecutionStore／授权文件／hash／回执解析／完整图片解码／独立生产worker执行，无mock被测授权或validator。仅supplier POST/GET响应为既有合成transport，资源接收通过明确media.example.com→loopback映射的实际本地HTTP；不代表真实公网TLS或provider可用。

- 正式query首次completed自动收到373B合成PNG，原delivery与SHA准确关联，旧原生记录逐项保留。
- 同keyquery和completed recover无第二次下载。
- blob已经发布而native saved尚未提交：GET返回available＋historical_saved=false／未登记，零写；recover只读原proof并在适用权限内本地完成。
- 完整及时part尚未发布：GET返回not_published，不发布；recover本地发布并登记。
- 已登记文件损坏／丢失：当前invalid／missing、received/persisted=false，历史saved／delivery保留；recover不补发。
- 容量锁忙的模拟OSError返回not_verified／null，不误报损坏。
- 合成时钟到期但文件完整：只读仍可报告available／历史登记，authority=expired，无写；源码字节漂移令执行权限拒绝，GET仍如实读取文件。
- 已completed重放遇资源HTTP404：保持completed、reception=failed／HTTP_NOT_200，不推断过期；后续recover无补发。

normal和404是同一新建合成账户的独立、重新验证的journal前缀分支；各资源请求1次，共2次实际本地worker连接，**外部DNS/socket尝试0**。原父文件及账户结构未改变。原生模拟分支只回放本次临时合成账户；未加载或回写真实原任务。

实际11条媒体ASGI、绑定的task／source／events／creator brief在 `native-run-05/asgi-fixtures.json`，HTTP均200。每次媒体GET前后私有文件指纹一致，敏感URL/raw/base64/Key/绝对执行路径未出响应。GET实际0.966–2.778秒，仅此40帧左右合成样本，不推断真实90帧或多镜头SLA；因此未新增跨请求缓存／放松权限核验。

## 兼容与保护

同一冻结公开副本、外网硬阻断下，`compatibility/shared.log`：D47 IO／快照、media API、asset API **57 passed，1 skipped，6.14秒，exit0**。该skip是既有D47 proof-fsync独立隔离入口，未算通过。D47 native特殊入口本轮未调用，仍保持原默认skip；没有重复历史17分钟长测。

正式旧流程兼容组 `backend/compatibility/formal.{json,log}`：**50 passed／1 failed，504.02秒，exit1**。失败为unknown替换grant重启旧节点，但实际在第一次reserve退出校验时失败，未进入后半链。相同冻结源码下，该节点追踪复跑1pass6.81s，再3个冷进程分别1pass4.73／4.32／4.27s，外部0，未改运行时或旧测试。复跑与原组重叠，不加作新独立用例，更不把原组覆盖成全通过；根因尚未确定，未断言lease、未声称修复。详见backend/FAILED-COMPATIBILITY-TRIAGE.md。Ruff对本轮13个Python文件通过。未运行全历史suite；原组失败保留为已知未定因项，有限切片交统筹审阅，不宣称整个阶段验收。

追加有界定位只在首次失败合成现场副本中进行：原30文件SHA不变；无新opt-in记录／字段，新contract trace调用0。原scope／grant重建reserve成功；同一候选保序导出后，在D48与逐项匹配entry ad34361的源码下均通过。负对照在两版本捕获同一旧时间次序断言，证明trace可见包装前错误，但该反例不代表原根因。有效诊断为backend/bounded-candidate-diagnosis-02/RESULT.json，不加pytest计数。原未提交候选的准确随机ID与时间、原进程状态不可恢复，仍不能排除相关时序／性能影响或宣称全部D48回归已排除；原失败不漂白，具体已排除与未排除范围详见TRIAGE。

`backend/FINAL-PROTECTION-CHECK.json`逐项SHA核对2532项保护文件，零漂移；原404没有删除，旧测试零漂移，native-run-05已测源码与当前零漂移。原native90／parent47／正式2/4／unknown3、原100元／保留风险83.37／累计55据入口清单保留，风险不是消费。本轮只做保护文件SHA核验，不重新执行真实runtime重建。

统筹最终核对见 `coordinator-final-source-check.json` 和 `coordinator-integrity.json`：后端412、前端116均与各自冻结清单一致，changed=[]；2532项保护文件无变化，旧测试和依赖未变。后端清单SHA仍为97d2620022756e1d1822d23008206f9e7d23bf2d314c1d2e3893816df525eb8a；前端清单SHA为7bfa4025953bb96ded18aba9143715f3ece11ffb457e71bb4b462c35cb6b8eca。此处引用统筹审计，不加作后端测试数。

## 统筹跨端与浏览器有限验证

`coordinator-api-decoder.json`最新记录：11条实际ASGI媒体响应通过真实前端decoder，7项负例被拒，分别为错task、错source、缺schema、成功缺query、retry=true、available同时声称FILE_INVALID、available同时声称FILE_MISSING。配套task/source/brief绑定保留；7项取代先前5项表述，不与后端pytest重复累加。

`coordinator-browser-validation.json`记录有限浏览器验证通过：使用正式production build、本轮实际ASGI响应及配对task/brief，在390／1440宽度核对已保存、文件已发布但记录未完成、文件丢失、404失败及无执行记录空态；刷新失败清除当前核验外观。刷新中保持键盘焦点，快速Enter只产生1次媒体GET，Tab可移至明细，完成后不抢回焦点，无关键溢出。共25个API请求（其中11个媒体GET），全部GET，写操作0，fixture字节未变，仅访问隔离本地origin。

这属于实际ASGI回放的UI接缝验证；列表wrapper和禁用的execution外壳是合成QA支架，空态404与读取失败503为明确合同模拟，没有连真实provider或原账户。统筹QA清理已完成：TaskSpace100结束，39148／39149端口关闭，3个临时构建目录移除；后端原失败现场与诊断证据继续保留供复核。不能将这份有限浏览器结论提升为真实生成、完整产品或最终人验。

## 实测范围与后续

本轮新native compound证明ordinary formal profile。RuntimeContinuedImageControl及原candidate gate接线已实现，但本轮没有再创建复杂retained-unknown完整native分支；也未新增formal并发、显式网络超时、saved→delivery及delivery→receive pointer每个间隙的独立分支。共享D47既有边界沿用，不能把沿用或源码检查写成本轮新通过。只读diagnostic隔离由固定ordinary selectors实现，legacy由旧兼容组验证，不伪造真实diagnostic生产验收。

真实Key／provider／付费／旧任务重试／原journal写入／新增预算／source续权／依赖更新／共享重启／push均0。新链及只读跨端接缝按上述已测范围有限可交付；旧兼容原批次50pass／1fail继续开放，时序／进程／性能影响未排除，不称原组已修复。源码继续冻结，后续限定验收与Git由统筹负责；本窗口不追加测试或实现，不开放浏览器直取图片或UI重试，不代替最终人验，也不会自动执行新的真实验证。
