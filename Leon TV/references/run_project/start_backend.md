# 启动后端

启动 FastAPI 后端服务。

## 检查是否已运行

```bash
# 推荐（检查服务响应）
curl --fail --silent --show-error --max-time 5 http://localhost:8000/api/health
```

## 启动命令

```bash
cd "Leon TV/backend"
uv run python api_server.py
```

若没有 uv，使用已安装依赖的虚拟环境启动。当前项目使用 `.venv`：

```bash
.venv/bin/python api_server.py
```

通过旧版手动方式安装并创建了 `venv` 的环境，改用 `venv/bin/python api_server.py`。

## 验证

```bash
curl http://localhost:8000/api/health
```

## 启动后等待

启动时间随环境变化。以前面的健康接口返回 `status: ok` 为就绪依据，失败时检查启动输出；固定等待 3 秒不能证明服务已就绪。

```bash
curl --fail --silent --show-error --max-time 5 http://localhost:8000/api/health
```

## 常见问题

| 错误 | 原因 | 解决方法 |
|------|------|----------|
| `command not found: lsof` | lsof 命令不存在 | 使用备用检查方式 `curl http://localhost:8000/api/health` |
| `后端未运行` | 服务未启动 | 执行启动命令 |
| `Address already in use` | 端口被占用 | 先查看占用进程与工作目录，确认归属后再停止；或为本项目选择其他端口 |
| `ModuleNotFoundError` | 启动解释器未安装依赖 | 使用 uv 或对应虚拟环境的 Python，核对安装输出 |
| `Connection refused` | 服务未启动或崩溃 | 检查启动终端输出 |

## 注意事项

- 后端端口：`8000`
- 日志默认输出到启动终端；仅在显式重定向时写入相应文件
- **必须确保后端运行后才能调用 API**
