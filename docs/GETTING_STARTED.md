# 安装与运行

以下目录均以 Git 仓库根为起点。应用保留双层 `Leon TV/Leon TV/` 目录，执行命令时请保留引号。

## 环境要求

- Python 3.10+，推荐3.12。
- Node.js 20.9+、npm。
- 推荐使用 uv 管理后端依赖。
- 后端媒体合成需要可运行的 FFmpeg。程序优先使用系统版本，并可回退到依赖 `imageio-ffmpeg` 提供的版本；安装器的系统检查提示不等于最终运行时探测结果。

## 一键安装

```bash
git clone https://github.com/leo-ai-pm/LeonTV.git
cd LeonTV
cd "Leon TV/Leon TV"
./install.sh
```

Windows 使用同目录 `install.bat`。安装器会检查环境、安装依赖，并在配置不存在时创建 `backend/config.yaml`，不会覆盖已有配置。

macOS/Linux 若只安装依赖、稍后构建前端：

```bash
LEON_TV_SKIP_FRONTEND_BUILD=1 ./install.sh
```

该变量只适用于上述 shell 安装器。

## 手动安装

在仓库根启动终端一：

```bash
cd "Leon TV/Leon TV/backend"
uv sync --python 3.12
test -f config.yaml || cp config.yaml.example config.yaml
```

在仓库根启动终端二：

```bash
cd "Leon TV/Leon TV/frontend"
npm ci
```

没有 uv 时，可在后端目录创建虚拟环境并安装 `requirements.txt`：

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python api_server.py
```

Windows 对应解释器为 `venv\Scripts\python.exe`。

## 配置模型

编辑后端本地 `config.yaml`，或在 Web「设置」页面填写密钥并选择模型。

- `api_providers`：各服务商凭据、地址与代理设置。
- `models`：文本、视觉评估、图像和视频的默认型号。
- `generation`：风格、画幅、分辨率和视频生成方式。

示例配置没有真实密钥。默认型号分属不同服务商，必须为实际选用的组合配置相应凭据；只填一个服务商的密钥不会自动满足全部默认环节。视频的时长、比例、音频与参考图方式应按具体型号确认。

配置里的 `print_model_input` 默认关闭。不要把完整配置、用户剧本、会话、供应商回执或签名链接贴到公开 Issue。

## 启动与检查

后端目录：

```bash
uv run python api_server.py
```

前端目录，新终端：

```bash
npm run dev
```

- 前端：http://localhost:3000
- 后端健康：http://127.0.0.1:8000/api/health
- 后端接口文档：http://127.0.0.1:8000/docs

没有密钥也可查看界面、创建项目和编辑画布；模型生成需要真实服务商配置和对应额度。服务启动成功不代表某个模型已验证可用。

## 端口与前端构建

后端 `server.host` 与 `server.port` 在本地配置中修改。若后端端口变化，前端通过 `BACKEND_API_URL` 指向新的地址：

```bash
BACKEND_API_URL=http://127.0.0.1:8100 NEXT_PUBLIC_API_URL=http://127.0.0.1:8100 npm run dev -- --port 3000
```

本地构建运行前端：

```bash
npm run build
npm start
```

`BACKEND_API_URL` 用于同源接口转发，`NEXT_PUBLIC_API_URL` 用于浏览器直连的流式请求，二者应与后端地址一致。`BACKEND_API_URL` 参与 Next.js rewrite 配置；改变该值后请重启开发服务，生产模式需用对应环境重新构建。保持后端默认本地监听，公开网络部署需要额外的多用户安全与运维设计。

## 本地数据

- `backend/code/data/`：会话、画布与异步任务元数据。
- `backend/code/result/`：生成图片、片段与合成结果。
- `backend/harness_data/`：Harness 产品运行与实验私有状态、回执和账本。

这些目录被 Git 忽略。备份时同时保存配置与所需项目/媒体；不要删除数据来解决测试或界面状态问题。

## 常见排查

| 现象 | 检查 |
| --- | --- |
| 前端能打开，接口请求失败 | 先确认后端健康地址，再核对前端 `BACKEND_API_URL` 与端口 |
| 模型报缺少密钥或不可用 | 核对所选型号对应服务商、凭据、接口地址和额度 |
| 生成请求超时或刷新断开 | 先查询原任务状态，避免重复提交产生额外费用 |
| 媒体合成失败 | 检查 FFmpeg 可执行文件、输入媒体与后端错误信息 |
| 画布节点没有自动全部运行 | 当前需要手动运行节点，整图自动编排尚未实现 |
