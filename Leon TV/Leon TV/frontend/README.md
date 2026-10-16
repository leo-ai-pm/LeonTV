# Leon TV 前端

Leon TV 是桌面 Web AI 短片创作工作台，沿用现有 Next.js App Router、React、TypeScript strict、Tailwind CSS、Lucide 和 React Flow 画布。前端接入真实后端，任务和产物以服务端持久化状态为准。

## 页面

| 地址 | 能力 |
| --- | --- |
| `/` | 创作首页、历史会话、六阶段工作流与人工确认 |
| `/studio` | 项目列表、搜索、新建、复制、回收站与演示画布 |
| `/studio/[id]` | 无限画布、节点与连线、自动保存、撤销/重做、素材版本、任务恢复与成片导出 |
| `/sandbox` | LLM、图片理解、文生图、图生图、视频工具与历史 |
| `/pipelines/standard` | 文艺短视频、字幕模版、配音与任务恢复 |
| `/pipelines/action-transfer` | 动作迁移 |
| `/pipelines/digital-human` | 数字人口播 |
| `/settings` | 模型、服务商与生成配置 |

## 本地运行

在本目录执行 `npm ci` 安装锁文件中的依赖，然后：

```bash
npm run dev -- --hostname 127.0.0.1 --port 3000
```

预览地址为 [http://127.0.0.1:3000/](http://127.0.0.1:3000/)。后端需另行启动，默认地址为 `http://127.0.0.1:8000`；只启动前端时页面可打开，业务 API 仍会失败。

普通 API 和媒体使用 Next.js 同源 rewrites。`BACKEND_API_URL` 指定后端地址，默认 `http://127.0.0.1:8000`。生产构建时需设置正确地址，修改后重新构建。流式连接保留原有直连方式，默认 `http://127.0.0.1:8000`；部署时通过 `NEXT_PUBLIC_API_URL` 设置浏览器可访问的后端地址，并由后端允许对应 Origin。密钥不得放入任何 `NEXT_PUBLIC_` 配置。

生产预览：

```bash
npm run build
npm run start -- --hostname 127.0.0.1 --port 3000
```

## 检查与无付费验收

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

回归测试使用 Node.js 内置测试运行器与项目已有 TypeScript 编译器，覆盖真实 API helper、流式读取、媒体路径和状态映射；测试中的 HTTP 响应是受控替身，不代表真实模型已验证，也不写入用户数据。

真实后端可用时，先检查配置、模型、项目列表、历史与已有任务恢复。可打开已有项目查看画布、用搜索筛选项目、打开生成配置与设置页。新建/复制/保存/回收站会写入项目数据，应在获准的测试数据上验收。生成、重新生成及流水线提交可能调用付费模型，不属于无付费检查。不要为页面验收清理用户缓存或修改真实密钥。

用户展示名称统一为 **Leon TV**；技术目录使用 `Leon TV`；现有本地存储键和外部模型标识保持兼容。当前产品范围为桌面 Web；手机端不作为本轮交付范围。
