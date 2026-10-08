# 参与 LeonTV 开发

欢迎提交问题与 Pull Request。当前开发重点与已知限制见 [开发路线图](./docs/DEVELOPMENT-ROADMAP-LeonTV.md)，本地安装与启动见 [README](./README.md)。

## 提交问题

请说明复现步骤、预期行为、实际结果，以及操作系统、Python 和 Node.js 版本。涉及模型调用时，补充服务商、模型名与脱敏后的错误信息。请勿附带 API Key、完整配置、个人会话或生成数据。

## 提交代码

1. Fork 仓库并从 `main` 创建分支。
2. 完成修改；新增或修改功能时更新相关文档。
3. 在仓库根目录执行与改动相关的检查：

   ```bash
   # 前端生产构建
   cd xiaoyunque/xiaoyunque/frontend
   npm ci
   npm run build
   ```

   ```bash
   # 后端现有测试（新终端，从仓库根目录开始）
   cd xiaoyunque/xiaoyunque/backend
   uv sync --python 3.12
   cp config.yaml.example config.yaml  # 首次配置；已有配置时不要覆盖
   uv run python -m unittest discover -s tests -v
   ```

4. 提交 Pull Request，说明解决的问题、用户可见的变化和验证结果。

本地 `config.yaml`、依赖、缓存和 `backend/code/` 创作数据均不提交。修改上游代码时保留原有版权声明；本项目使用 [MIT 许可证](./LICENSE)。
