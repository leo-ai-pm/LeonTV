# 分镜图片只读面板 · D48 前端验证

2026-11-01 收尾。前端实现、同源码自动检查、实际 ASGI 解码及浏览器有限验收已闭合；刷新焦点与错误一致性修复的最终独立复测通过。本文件不宣称真实图片生成、视觉质量、视频或完整 Harness 已验收。

## 实现范围

沿用 A 放映室，在 `BriefPlannerPanel` 当前普通规划任务的执行区后挂接“分镜图片”；离线规划测试不挂正式媒体。新增独立类型、严格 decoder、只读 API、controller、状态文案与面板，不改规划授权、pending 请求恢复或旧费用状态。

API 只访问现有 `GET /api/planner/tasks/{task_id}/execution/media`。task_id 和新增 source_snapshot_hash 必须精确匹配选中任务；schema、来源、枚举、布尔值、计数、op/query 和成功组合严格校验。新 schema 缺失不使用旧 delivered 拼出成功。额外字段按显式投影丢弃，未知错误文本不显示。正常 404 只有精确 `NOT_FOUND / Execution record not found` 才显示“尚无执行记录”；其他 404、503、非 JSON 和绑定错误都是读取失败。

真实回执与合成验证按原 evidence_kind 区分。legacy 登记保留为历史事实，当前文件未核验；managed 登记也不能证明当前文件仍在。只有准确 op/query/completed、文件当前可用、保存记录成立、正式登记和非待处理时才显示“已保存”。文件已发布但原生 saved 未提交属于合法 partial，decoder 接受而 UI 显示待确认；保存未登记、文件缺失/损坏、unknown 分开表达。权限到期不抹除完整文件事实，也不产生执行入口。

刷新开始立即撤下当前成功标签；失败保留同任务旧数据时，整个区块、主状态和详情均标记上次读取/当前未核验。task/快照切换清空旧内容；请求序号和卸载失效检查覆盖异步 JSON 解码后的迟到结果。刷新去重，无自动轮询、POST、下载、恢复、重试执行或授权开关。

## 请求与状态矩阵

| 条件 | 展示 | 副作用 |
| --- | --- | --- |
| 未选普通任务 / offline_test | 不挂正式图片面板 | 不请求媒体 |
| 正在读取 | 加载提示，不显示空数据或旧任务卡片 | 当前任务本地 GET |
| 精确执行 NOT_FOUND404 | 尚无执行记录 | 不创建任务 |
| 无正式 scope | 尚未配置正式图片范围、未取得制作授权 | 不增加授权 |
| 已预留 / 提交中 / accepted pending | 等待生成 / 生成中，依据不同 | 不提交模型 |
| completed，未确认保存 | 已生成待保存，说明未完成环节 | 不下载或补写 |
| available，但原生 saved 未提交 | 结果待确认，文件可用与登记分列 | 不本地恢复 |
| 已保存但未登记 | 结果待确认，明确登记尚未完成 | 不登记 |
| managed 当前完整保存且正式登记 | 已保存，质量未审核、人验待进行 | 只展示 |
| legacy delivered | 旧版已登记，当前文件未核验 | 不升级旧权限 |
| resource404 / 接收明确失败 | 保存失败；404 原因尚未确认 | 不推断 URL 过期，不重试 |
| unknown / 文件缺失或损坏 | 结果待确认，历史事实保留 | 不清 unknown 或释放风险 |
| 刷新中 / 失败 | 撤下已核验成功；旧数据显著标记 | 仅 GET，无自动重试 |
| task/快照切换、迟到或卸载 | 旧响应不进入当前视图 | 中断等待并忽略失效响应 |

主卡片使用普通用户文案；技术引用、HTTP 状态、历史保存/登记、当前文件、权限和费用放入原生折叠详情。金额直接显示原安全投影，未确认费用保持未确认，保留风险标注“不是消费”。不提供媒体 URL 或预览/下载能力。

## 自动验证结果

最新 116 文件源码清单在所有检查期间逐份不变；清单 SHA256 `7bfa4025953bb96ded18aba9143715f3ece11ffb457e71bb4b462c35cb6b8eca`。证据在本轮 `frontend/focus-fix/final-checks.json`、`source-manifest.json` 及各 final 日志。初版 114 文件/201 测试证据保留在 frontend 根目录，不与最新结果累加。

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| `npm test` | 204 通过，0 失败，0 skip，退出 0 | 含 27 项 D48 测试（原 24 + 本次 3），其余 177 为现有测试；不重复累计 |
| `npm run typecheck` | 退出 0 | strict 类型检查 |
| `npm run lint` | 0 错误、10 条既有 img 警告，退出 0 | 新增文件无警告；既有警告位于 Sandbox、PipelinePage、CharacterStage、ImageLightbox、ReferenceStage、StudioCanvas |
| `npm run build -- --webpack` | 隔离生产构建通过，退出 0 | 不复制 .env，不改共享 .next；最终构建日志与源码指纹保留，QA 临时目录已清理 |
| 7 个后端 HTTP200 手写契约例 | 均通过真实 decoder | 独立 no-scope / legacy / success / 无图 / 404 / 未登记 / 丢失到期；不是实际 ASGI 证据 |
| decoder CLI 自检 | 退出 0 | 一个手写成功例；不是 ASGI 或浏览器结果 |

新测试直接加载生产 TS decoder/API/controller，通过合成 fetch JSON 覆盖严格类型与身份、安全投影、来源、历史/当前区分、partial、404/unknown、未知费用、GET-only、首次失败/刷新失败、A→B、同 task 换快照、解码迟到、旧刷新、重复查询及卸载。没有 mock 掉被测解码器。切换/刷新不改变既有规划写 ACK 语义。

本次另有 1 项真实 Panel 的 React 服务端渲染检查：忙碌态按钮保持原生 button、文字名称、aria-disabled，不含 native disabled、负 tabindex 或 autofocus。组件内部同步 reading guard，controller 原有去重继续有效，无响应完成后的强制 focus。CSS 为暂不可用状态提供样式，focus-visible 保持完整可见。渲染检查证明语义与属性，不冒充浏览器真实焦点测试。

另 2 项生产 decoder/view 检查覆盖矛盾错误：完整成功组合不能带非 null error，FILE_MISSING/FILE_INVALID 必须与文件状态相符；view 也不把带错误的组合标“已保存”。合法发布间隙、已保存未登记、准确文件缺失/损坏和权限到期但文件仍可用均保留。旧测试字节未修改。

首次最终复核曾暴露 partial 文案分支和验证 runner 对普通测试错误注入构建代理地址，两点已修复；失败日志保留 first-final-*，不计为通过。最终重新运行上述四项，结果以 final-checks.json 为准；旧测试未修改。

## 独立接缝与浏览器实证

纯 decoder 导出：`frontend/lib/plannerMediaApi.ts` 的 `readPlannerMedia(body, {task_id, source_snapshot_hash})`。输入为实际 HTTP200 JSON 原 envelope，绑定取自选中的 PlannerDetail，不从响应反推。输出仅安全投影；失败抛出静态安全错误。

统筹用最新生产 decoder 重跑实际隔离 ASGI 导出：11 个样本及 7 个负例通过。负例为错 task、错 source、缺 schema、成功缺 query、宣称重试权限，以及 available 却带 FILE_INVALID / FILE_MISSING 两种矛盾错误；绑定独立取自 Planner task，非从响应反推。正例包括正常保存、发布/part 间隙与本地恢复、坏文件、丢失、锁忙、到期可用、源码漂移及资源 404。实证见 `coordinator-api-decoder.json/log`，fixture SHA256 为 `d86f38aa6297b56bbf6fd6c67f4b7b2d5aa7d0955ea038a3f7c696e8bdd9652b`。结果所记四份 frontend_source SHA 与当前最终源码完全匹配；只报告该次 11+7，不累计前次 11+5。

实际接缝检查直接调用生产 decoder，未复制校验逻辑。HTTP404 由 adapter 测试与浏览器显式契约模拟验证，未作为 HTTP200 decoder 输入。

隔离 QA 使用最终 `uto75il5` 构建，后端 fixture 端口 39148、前端 39149；代码副本不含 .env，node_modules 仅链接现有依赖。统筹现已关闭两个端口、finish TaskSpace100，并删除三个自有临时构建目录。最终构建日志、源码清单、截图及实际 API/浏览器证据保留；临时构建目录已清理。`frontend/focus-fix/QA-SERVICE.json` 已标记关闭。

验收在 `/creator` 配对的 normal task 下完成。媒体使用已保存的实际隔离 ASGI 响应与配对 task/brief；列表包装与禁用的执行区为合成 QA 支架，执行404空态和503失败为显式契约模拟。验收未连接真实账户或 provider。

统筹已将 4 个实际 ASGI 状态（资源404、发布未saved、当前文件missing、managed成功）送入浏览器，主状态、说明与计数正确；文档约定的执行404空态也通过，见 `coordinator-browser-states.json`。390px/1440px 无横向溢出，Tab 到 summary、Space 展开通过。仅这些实际宽度/操作有证据，不扩大为全部设备验收。

修复前，Enter 刷新进入 native disabled，焦点落到 BODY，见 `coordinator-refresh-failure.json.after.focus`。修复后，`coordinator-refresh-focus-final.json` 在最终 `uto75il5` 构建、390px、reduced-motion 开启下实测通过：读取中焦点仍为“刷新图片记录”BUTTON，native disabled=false、aria-disabled=true；快速两次 Enter 只产生 1 次媒体 GET；失败后仍聚焦原 BUTTON。下一次刷新中主动 Tab 到 SUMMARY，响应完成仍保持 SUMMARY，不抢焦点；无横向溢出。刷新中/失败同时撤下已保存外观，保留明确的未核验/旧记录说明。

可访问性还实现语义标题/列表、文字按钮、原生 details、上下文 status/alert、aria-busy、44px 目标、长标识断行和 reduced-motion 继承，不新增动效。`coordinator-browser-validation.json` 汇总实际 25 次 API 请求，全部 GET，其中媒体 GET 11 次、写请求 0，fixture 字节不变；快速两次 Enter 的单次媒体 GET 另有焦点复测记录。该证据范围为本轮隔离验收，不扩为全站性能或真实 provider 验收。

## 闸门与剩余

| 闸门 | 状态 |
| --- | --- |
| 代码、类型、测试、Lint 与隔离生产构建 | 是；Lint 既有 10 条警告单列 |
| 旧测试/依赖/lockfile 保持 | 是；对 D48 入口仅两份旧前端文件改动，新增八份；本次只改四份前次实现并加两份 tests |
| 实际 ASGI→生产 decoder | 最新源码11实际样本+7负例通过，来源SHA匹配 |
| 390px/1440px、summary Tab/Space 与4实际状态 | 统筹有限实测通过；不是全设备/全流程声明 |
| 新构建键盘刷新焦点与重复激活GET去重 | 390px/reduced-motion实测通过，失败留焦点、Tab后完成不抢焦点，快速两次Enter仅1媒体GET |
| QA 资源收尾 | 39148/39149 已关闭，TaskSpace100 已 finish，三个统筹临时构建目录已删除；最终构建证据保留 |
| 真实 provider/CDN 与内容/人验 | 本轮未执行，不属于前端自动检查通过范围 |

本窗口不修改后端、PRD、AGENTS、旧测试、依赖、锁文件或原私有状态；不启动共享服务、不执行付费/旧任务重试或 Git 操作。原生 store 与全体保护项的跨端审计仍归统筹，本窗口仅核对前端源清单。

本前端切片没有剩余实现或补验项，统筹 QA 资源收尾已完成。2026-11-01 最后仅更新报告/RESULT，复核 116 文件仍与最终清单一致；未改源码、未重复跑已通过测试。真实媒体/质量/人验仍按其独立范围验收。
