# 项目初始化

首次下载项目后，需要配置环境并启动前后端服务。

## 使用场景

当用户给出以下指令时使用：
- "初始化项目"
- "配置项目"
- "部署项目"
- "安装项目"
- "开始项目"
- "setup project"
- "deploy"

## 前置检查

```bash
# 检查 Python 版本（需要 3.10+，推荐 3.12）
python3 --version

# 检查 Node.js 版本（需要 20.9+）
node --version

# 检查 npm 版本
npm --version
```

## 一键初始化（推荐）

从仓库根目录进入 `Leon TV/Leon TV` 后执行：

```bash
# macOS / Linux
cd "Leon TV/Leon TV"
chmod +x install.sh
./install.sh
```

Windows 执行：

```bat
cd "Leon TV\Leon TV"
install.bat
```

安装脚本会完成后端依赖、`config.yaml` 初始化、前端依赖安装和前端 build。安装完成后，提醒用户编辑 `Leon TV/backend/config.yaml` 填入 API Key，或启动前端后通过侧边栏底部「设置」页面配置。

## 手动初始化步骤

### 步骤1：初始化后端

参考 [init_backend.md](init_backend.md)

### 步骤2：初始化前端

参考 [init_frontend.md](init_frontend.md)

### 步骤3：验证服务

```bash
# 检查后端运行
curl http://localhost:8000/api/health

# 检查前端运行
curl http://localhost:3000
```

## 常见问题

| 问题 | 解决方法 |
|------|----------|
| 后端启动失败 | 检查 Python 3.10+、依赖与配置；错误信息脱敏后排查 |
| 前端 build 失败 | 先查看构建报错并核对 Node.js 20.9+ 与锁文件，不直接删除依赖 |
| 端口被占用 | 先核对占用进程是否属于本项目，再协调停止或选择其他端口；不要按端口批量杀进程 |
| 依赖安装慢 | 使用国内镜像源 |
