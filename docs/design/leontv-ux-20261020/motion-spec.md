# LeonTV 放映室 · 动效说明

2026-10-20，LTV-UX-DESIGN-001。首版已选 A「放映室」；本文是离线设计草稿的动效实现与源码审查，不是浏览器体验已验收的声明。

## 分工与实际来源

静态 UI/UX 仍由 UI UX Pro Max 负责。动效实际读取并应用：
- animate 的 `SKILL.md`、`RECIPES.md`：先按频次与目的决定是否动，再选实现和参数。
- review-animations 的 `SKILL.md`、`STANDARDS.md`：按十项标准审查。
- 参考版本：[emilkowalski/skills @ 2d228630](https://github.com/emilkowalski/skills/tree/2d228630ba1446365f614a108cb8252f1feda202)。

## 先过频次门槛

| 元素 | 频次 / 目的 | 决策与代码 |
|---|---|---|
| 视图导航、方案 A/B/C、情境比较 | 高频阅读、键盘可操作 | 即时更新，不作滑动/缩放；保留文字与选中语义 |
| 剧本文字、预算、费用、核验结论 | 用户正在阅读或决定 | 不移动，不播放数字计数，不用动画把 unknown 变为通过 |
| 原生详情侧面板 | 偶发；空间关系 | CSS transform + opacity，240ms / 200ms，进入从右侧小距离靠拢；关闭即时，不为退出动效拖延返回 |
| 主要/次要动作的指针按压 | 偶发；反馈 | 不缩放；按下即时 opacity 反馈，松开100ms恢复；不改变布局尺寸 |
| 本地反馈条 | 偶发；状态反馈 | 复用同一个元素，以 CSS transition 在 opacity/translateY 间重定向，160ms；频繁触发替换文字，不叠出一串提醒 |
| 计划展开 | 高频可读内容 | 原生 details/summary 即时展开，不为抽屉高度制造布局动画 |
| 任务进展、局部修复、查询 | 不是计时动画 | 只有用户显式按钮/情境切换才改变示例；没有 timer/rAF 推进完成度、质量或 ETA |

没有弹跳、鼠标跟随、循环呼吸灯、全屏入场、自动播放媒体或滚动揭示。视图比较不是动效菜单。任务信息的真实性优先于视觉活跃感。

## 参数与实现

共用 tokens 位于 `index.html` 的 `:root`：

```css
--ease-out: cubic-bezier(.23, 1, .32, 1);
--ease-drawer: cubic-bezier(.32, .72, 0, 1);
```

数值取自 animate/STANDARDS 的表格，未自造 easing。侧面板使用原生 `<dialog>` 提供语义、焦点约束和 Escape，不安装 UI/动效库；用户要求的原生控件与零依赖范围优先于技能中引入组件库的通用建议。

`@starting-style` 给支持它的浏览器提供进入样式；不支持时直接出现，内容与关闭始终可用。关闭不等待 transitionend，也不依赖动画结束才能写入示例状态。反馈条只有隐藏计时器，没有生产进度计时器。原生视频有 controls，无 autoplay；离开页面、离开看片视图或离开视口后暂停。

## 可访问性与中断

- `data-input` 区分指针与键盘。键盘发起的页面动作无位移动效；侧面板与反馈条同样取消运动。
- `prefers-reduced-motion: reduce` 移除 scale/translate，保留 100ms opacity 变化；原生视频不自动播放。
- hover 反馈只在 `(hover: hover) and (pointer: fine)` 中启用；只改 opacity，不用 hover 才能发现关键功能。
- CSS transition 可从当前值重定向；再次点击或关闭面板不等待动画。业务/示例状态由 reducer 决定，动画不充当锁、审批或完成证据。
- 动效只选 transform/opacity，**这不等于真实性能已保证**。合成层、文字清晰度、窗口缩放、视频播放时帧表现与 rapid toggle 仍需真实浏览器检查。

## review-animations 源码审查

按技能要求使用 Before / After / Why 表；下列是构建过程中主动舍弃或修正的方案，不伪装为已经观察到的浏览器缺陷。

| Before（未采用） | After（当前实现） | Why |
|---|---|---|
| 每次导航都滑入，或选项切换弹簧移动 | 高频导航和比较即时切换 | 频次不应占用持续阅读与键盘操作的时间 |
| width/height 的布局动画 | 详情面板只用 transform/opacity | 避免动画每帧更改布局；实际帧表现仍待 QA |
| 400–500ms 统一抽屉/反馈动效 | 侧面板240/200ms，按压即时/松开opacity 100ms，反馈160ms | 当前工作区响应应短于300ms，不照搬慢节奏营销配方 |
| 键盘与鼠标用相同 scale 反馈 | 键盘零运动，指针只有opacity反馈 | 遵守 Emil 的键盘/高频硬门槛 |
| 本地计时器推进生产进度 | 显式预览状态切换，绝不自动跨质量门禁 | 等待的动画不能成为真实结果或授权证据 |
| reduced-motion 仍移动侧面板 | 只保留100ms opacity；无位移缩放 | 保留可理解的变化，同时减少运动刺激 |

源码层决策：**Approve（仅源码）**。未发现 `transition: all`、scale(0)、UI ease-in、布局属性动画或任务计时完成。Feel-breaking / 性能 / 焦点与窄屏的真实验收仍为 **pending**，由统筹唯一 Ego 空间执行；不把源码审查等同于视觉或体验通过。

重点 feel-check：快速反复打开/关闭详情、键盘 Enter/Escape、prefers-reduced-motion、视频播放时开面板、375px 窄屏与缩放。若手感不确定，按 STANDARDS 建议在 DevTools 中放慢2–5倍逐帧看，先删或减动效，再调参数，不增加装饰运动。

## 统筹 QA 后小修（2026-10-20）
- 封面说明 `pointer-events:none`，播放按钮 z-index 3 且44×44px；等待根复测遮挡问题。
- 已删除指针按压缩放，按下即时opacity反馈/松开100ms恢复；动态详情视频现注册同一IntersectionObserver，关闭时暂停并解除观察。
- 根已实测375px三个主视图无横向溢出，计划面板Escape返回焦点；其他详细原型结论仍以根证据为准。
