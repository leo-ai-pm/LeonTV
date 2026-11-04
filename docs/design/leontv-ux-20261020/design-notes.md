# LeonTV 创作者体验 · 设计预览

日期：2026-10-20。任务 LTV-UX-DESIGN-001；产品依据为 LeonTV 创作者体验 PRD v0.5。这是可点击设计原型，不是生产实现或真实 Harness 成片交付。

**最新已选方向：A「放映室」。** 产品负责人最新直接选择覆盖此前 B 倾向；本文件与 index.html 以 A 为默认和首版核心。B/C 仅保留同一骨架的轻量比较，不再扩展独立交互。`direction-board.html` 提供三种方向的视觉比较。

## 实际使用的方法与来源

按用户分工，静态 UI/UX 使用 [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/tree/56ea9cf174ed86786187daa9de0e6247cb0747e4)，动效仅使用 [Emil animate / review-animations](https://github.com/emilkowalski/skills/tree/2d228630ba1446365f614a108cb8252f1feda202)。本轮没有让其他默认设计技能接管静态方案。此处记录实际使用的方法；未将本机安装目录或内部验证材料纳入公共仓库。

实际读取：
- UI UX Pro Max 的 `SKILL.md`、`references/quick-reference.md`；核对 `pro-rules.md` 后按其中 scope notice 不照搬原生移动端单位。
- animate 的 `SKILL.md`、`RECIPES.md`。
- review-animations 的 `SKILL.md`、`STANDARDS.md`。

实际运行（均为本地公开数据检索，不发送项目原文）：

```sh
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'video editor creator workspace' --design-system -p 'LeonTV Creator UX' -f markdown
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'creative writing productivity' --design-system -p 'LeonTV Script Desk' -f markdown
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'AI creative production dashboard' --design-system -p 'LeonTV Production Grid' -f markdown
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'minimal dark media workspace' --domain style -n 3
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'creative editorial workspace' --domain style -n 3
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'bento grid' --domain style -n 1
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'error summary validation' --domain ux -n 2
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'focus not obscured' --domain ux -n 2
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'forms keyboard responsive' --stack html-tailwind -n 3
python3 <ui-ux-pro-max-skill-dir>/scripts/search.py 'forms loading accessibility' --stack nextjs -n 3
```

匹配核对：视频工作区返回 Dark Mode (OLED)，写作返回 Flat Design，创作控制台返回 Minimalism & Swiss Style；显式风格查询另得到 Editorial Grid / Magazine 与 Bento Box Grid。保留其适用的层级、排版和表面规则；生成器的 Hero/Features/Conversion 段落属于营销页，**不适用于本轮应用主流程，未照搬**。pink/teal 默认配色也不机械套用。Next.js 的 Server Actions 建议不取代已冻结 FastAPI/D06；本轮交付原生离线 HTML，无运行时框架、字体 CDN 或新增依赖。未用 persist/force 写入另一套全局设计系统。

## 三个方向与参考锁

| 方向 | 主参考 | 保留的特征 | 有意不同 | 建议 |
|---|---|---|---|---|
| A 放映室 | 数据库 `Dark Mode (OLED)`；当前 LeonTV 以媒体承载结果的 Studio | 暗色低反光工作区、清楚的内容层级、媒体作为结果主体 | 暖白文字、薄边框，少量浅蓝用于主动作；不是发光霓虹或满屏渐变 | **已选**：从输入到看片的连续性强，适合长时间制作与审片 |
| B 编剧桌 | `Minimalism & Swiss Style` 为主，`Editorial Grid / Magazine` 仅补文字分栏 | 白底、强网格、清楚的标题与正文尺度、窄阅读行宽 | 小圆角、墨色文字与朱红动作；入口优先剧本文字，不用封面堆满首屏 | 适合以阅读与改稿为主的创作者 |
| C 制作台 | `Bento Box Grid` 为主，现有 LeonTV 导航/橙色动作作为连续性参考 | 模块卡片、较松的间距、信息分组与渐进披露 | 顶部导航替代常驻宽侧栏，浅蓝灰工作区、橙色主动作，预算与目标分卡 | 适合偏项目管理、快速扫视的团队创作 |

三个方向是布局与视觉密度的差别，共享同一任务、输入内容、异常语义和验收门禁，避免用户因功能差异而误选视觉。中文与拉丁文均使用本机系统字体，不下载商业字体或远端字库；图标为本页内联的统一线性 SVG。

## UX 取舍

| 决策 | 依据 | 处理 |
|---|---|---|
| 主入口是剧本、要求和预算 | 用户目标、PRD v0.5 | 一个主动作“预览制作流程”；真实生产明确待开发，不假装已能扣费出片 |
| 正常路径不逐步点继续 | PRD 正常路径 | 计划可看、进展可查，但不是闸门；用户只处理少数明确异常和最终看片 |
| 先回答是否需要用户 | PRD 信息优先级 | 工作区主状态与异常说明在首屏；技术事件、依赖、哈希下沉详情 |
| 画布与六阶段仍可找到 | 保留旧入口约束 | “高级创作”原生披露，展示已有入口及其适用用途；不把它们设为新旅程必经步骤 |
| 不把等待演成进度 | D06 无生产百分比、Emil 频次门槛 | 没有自动百分比/ETA、假加载时间或计时完成；所有示例变化必须显式操作 |
| 质量未知不可接受 | PRD/D06 | 未知、未通过与执行完成分开；旧版/证据不齐时禁用接受并写明原因 |
| 费用不是一个余额数字 | PRD | 上限、已发生、预留、待核分开；示例金额醒目标注，不沿用已结束的100元试制授权 |
| 错误定位且保留输入 | UX 搜索 Focusable Error Summary | 校验摘要可聚焦、关联字段；退回必须写问题，错误靠近输入，不清空文字 |
| 窄屏先保核心任务 | quick-reference 可读性/不横向溢出 | 卡片折为单列、导航换行、播放器按真实媒体比例、长标识换行，保留键盘焦点 |

## 预览身份与媒体

- 页面常驻“设计预览 · 示例数据 · 真实制作待开发”；不调用任何正式 API，不写项目、不持久保存剧本/预算，不外发输入。
- 《末班灯》为本轮原创合成示例，与任何私有小说无关。
- 封面与播放器只引用已经公开的 `docs/assets/demos/minimax-h3-*`、`seedance-2.5-*`。封面内联在 HTML；视频按相对路径读取，始终标“历史技术样片，与本示例剧本无关”，不是 D06 TEST 产物、不是新生成成片。
- “演示接受/退回”仅修改页面示例状态，不提交测试终审，也不代表用户对真实作品作决定。
- 原型控制条用于比较方向/视图/异常；未来产品不保留此设计比较工具栏。没有“真实模式”开关。

## 本轮交付与检查范围

`index.html` 的 HTML/CSS/JS 自包含；可以直接离线打开，无外部字体、脚本、API 或跟踪请求。历史视频是仓库中的本地可选媒体，离开仓库单独移动 HTML 时文字/封面/全部交互仍可用，播放器会给出文件缺失提示。

浏览器由统筹唯一空间验收，本窗口只做源码/脚本/状态与对比度检查。动效决策、精确时长和审查表见 `motion-spec.md`；最后检查结果见本轮 `frontend-progress.md`。选定方向前没有改写生产前端。
