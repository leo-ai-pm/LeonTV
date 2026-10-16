# 初始化前端

配置并启动 Next.js 前端服务。

## 步骤1：进入前端目录

```bash
cd "Leon TV/frontend"
```

## 步骤2：安装依赖（首次）

```bash
npm install
```

> **注意**：如果安装慢，可使用国内镜像：
> ```bash
> npm config set registry https://registry.npmmirror.com
> npm install
> ```

## 步骤3：确认后端地址

普通 API 默认通过 Next.js rewrites 访问本机后端 `http://localhost:8000`；流式接口还要核对 `lib/workflowApi.ts` 使用的 `NEXT_PUBLIC_API_URL`。后端或前端端口改变后，一并检查代理目标、流式地址与 CORS。生产构建前设置相应环境变量。

## 步骤4：Build 并启动

```bash
# 首次 build（必须）
npm run build

# 启动服务
npm start
```

## 步骤5：验证

```bash
curl http://localhost:3000
```

返回 HTML 表示成功。

## 后续启动

首次初始化后，后续启动只需：

```bash
cd "Leon TV/frontend"
npm start
```

## 常见问题

| 错误 | 原因 | 解决方法 |
|------|------|----------|
| `npm: command not found` | Node.js 未安装 | 安装 Node.js 20.9+ 及配套 npm |
| `Error: Could not find or load config file` | .next 目录损坏 | `rm -rf .next && npm run build` |
| 白屏/空白页面 | build 缓存问题 | `rm -rf .next && npm run build` |
| `Address already in use` | 端口 3000 被占用 | 先核对进程归属，再协调停止或调整本项目前端端口 |
| 依赖安装失败 | 网络问题 | 使用镜像或科学上网 |
