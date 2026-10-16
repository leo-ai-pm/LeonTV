# 参与 Leon TV 开发

本项目以本地AI视频创作为当前场景，以Agent Harness为演进架构。[README](./README.md)说明产品入口，[架构](./docs/ARCHITECTURE.md)区分实现与计划，[路线图](./docs/ROADMAP.md)说明当前优先级。

## 本地开发

按[安装指南](./docs/GETTING_STARTED.md)准备依赖。应用位于仓库根的 `Leon TV/Leon TV/`，前后端分别启动。请保留默认本地监听，不把个人开发服务当作已具备多用户隔离的公共服务。

## 检查

各代码块都从仓库根目录开始：

```bash
cd "Leon TV/Leon TV/backend"
uv run --extra dev python -m pytest -q
```

```bash
cd "Leon TV/Leon TV/frontend"
npm ci
npm test
npm run typecheck
npm run lint
npm run build
```

安装器检查：

```bash
python3 -m unittest discover -s tests/installers
```

离线测试、真实服务商调用与完整视听验收分开报告。不要为了得到通过结果修改用户资料、跳过错误或把未验证质量标记为接受。

## 提交问题与代码

1. 提交Issue时提供复现步骤、预期与实际行为、操作系统及运行时版本。涉及模型时注明精确型号与服务商，并提供脱敏错误。
2. 从main创建功能分支，保持改动范围清晰；新增行为同步测试和公共文档。
3. Pull Request说明问题、用户可见变化、验证结果与限制。
4. 修改上游代码时保留许可证与版权；变更模型映射时不能静默替换用户选择的型号。

## 发布资料边界

不要提交真实 `config.yaml`、`.env`、密钥、本地依赖、构建缓存、`backend/code/`、`backend/harness_data/`、私人剧本或原始供应商回执。公共演示仅放经选择的媒体及脱敏说明，避免把相邻运行目录一起上传。

实验执行器可能依赖本地授权文件。测试应使用合成fixture，不依赖维护者的私有配置或真实额度。执行真实收费任务前，需要明确的任务授权、费用依据、请求前预算保护与未知受理恢复策略。

当前UI有既有的图片元素lint警告；它们应按性能与产品需要处理，不能与类型错误或测试失败混为一谈。本项目使用[MIT License](./LICENSE)。
