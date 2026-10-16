# Leon TV 项目开发指引

Leon TV 是本机桌面 Web AI 视频创作平台。已有六阶段工作流、项目画布、单次生成工具和专项视频流水线。产品展示统一写 `Leon TV`；目录统一为 `Leon TV`；旧存储键和启动别名为兼容保留，许可证与上游署名保留。

## 现役入口

- 仓库根目录：本文件上一级；实际应用位于 `Leon TV/`（相对本文件所在目录）。
- 前端：`Leon TV/frontend/`，Next.js / React / TypeScript / Tailwind / React Flow。
- 后端：`Leon TV/backend/`，FastAPI / Pydantic / 模型客户端 / FFmpeg。
- 产品范围与窗口分工：仓库根目录 `docs/ROADMAP.md`。
- 代码能力及验证边界：仓库根目录 `docs/ARCHITECTURE.md`。
- 目录迁移及 Agent 增量验证：`docs/ARCHITECTURE.md`；`backend/experiments/harness_poc/` 是确定性本地实验，不是生产 LLM Agent。
- 外层 `SKILL.md` 与 `references/` 是 OpenClaw 调用说明；技能文本不是产品内已实现的 Skill 加载器。
- 第三方来源与许可见仓库根 README 和 LICENSE；本地若保留 FilmAgent 历史参考副本，它独立于当前 Web 应用入口，不随现役应用发布。

## 启动与验证

以下命令从本文件所在的技能目录 `Leon TV/` 开始，分别在新终端执行：

```bash
cd "Leon TV/backend"
uv run python api_server.py
```

```bash
cd "Leon TV/frontend"
npm run dev
```

默认后端 `http://127.0.0.1:8000`、前端 `http://localhost:3000`；其他预览端口以实际启动参数为准。启动前核对进程归属，避免停止其他项目。使用 `/api/health` 和真实页面检查就绪，固定等待秒数不能代表成功。

后端检查使用 `uv run --extra dev python -m pytest -q`；前端按 `package.json` 执行代码检查、类型检查、相关测试和生产构建。验证命令失败时保留原错误，不通过删功能或降标准掩盖。

## 核心调用与数据

- `core/orchestrator.py` 驱动固定六阶段：剧本 → 角色/场景 → 分镜 → 参考图 → 视频 → 后期。
- `core/agents/` 是阶段处理器；不等同于独立规划和通信的子 Agent 团队。
- `api/routers/workflow.py` 负责阶段调用；`studio.py` 负责画布；`sandbox.py` 负责单次工具；`pipelines.py` 负责短流程任务。
- `models/` 封装模型能力；固定工作流优先沿用状态机，新增动态工具执行须有明确需求。
- 后端 `code/data/` 保存会话、画布、任务等元数据；`code/result/` 保存生成媒体。不得删除用户数据或修改其内容来通过测试。
- 模型与服务商配置只经受保护的后端接口使用。不要在输出、截图或日志中打印 `config.yaml`、密钥和完整用户文档。

## 修改约定

- 先检查既有未提交修改；按统筹分工编辑，保留其他窗口的工作，不擅自切分支、重置或提交。
- 桌面 Web 是当前产品范围；手册默认技术方案按现有工程适配，不能借此扩展终端或整体迁移。
- 阶段确认、取消和恢复行为以接口状态及当前产品说明为准，保留人工确认点，不因刷新或断线重新付费生成。
- 测试区分离线模拟、真实接口、真实模型调用；未运行的项目写明待验证，不继承历史验收结论。
