# Leon TV API 文档

## 概述

Leon TV 是一个 AI 视频生成系统，提供 REST API 供外部调用。API 与前端共享后端管理的会话与产物；当前持久化载体为结构化文件。

**基础 URL**: `http://localhost:8000`

---

## 认证

当前版本没有账号认证，面向本机单人使用；服务应绑定 `127.0.0.1`。这不表示已具备公网或多用户隔离能力。

---

## 可用阶段

| 阶段 ID | 名称 | 说明 |
|---------|------|------|
| `script_generation` | 剧本生成 | 将灵感转化为结构化剧本 |
| `character_design` | 角色/场景设计 | 生成角色设计图和场景背景 |
| `storyboard` | 分镜设计 | 设计镜头语言和分镜脚本 |
| `reference_generation` | 参考图生成 | 生成高精度参考图 |
| `video_generation` | 视频生成 | 将参考图/分镜图生成视频 |
| `post_production` | 后期剪辑 | 拼接视频片段为最终成片 |

---

## API 接口列表

### 1. 创建项目

创建一个新的视频生成项目。

**接口**: `POST /api/project/start`

**请求体**:
```json
{
  "idea": "故事线描述",
  "style": "anime",
  "episodes": 4,
  "llm_model": "deepseek-v3.2",
  "vlm_model": "qwen3.5-plus",
  "image_t2i_model": "doubao-seedream-5-0-260128",
  "image_it2i_model": "doubao-seedream-5-0-260128",
  "video_model": "wan2.7-i2v",
  "enable_concurrency": true
}
```

**参数说明**:
| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| idea | string | 是 | 故事线描述 | - |
| style | string | 否 | 视频风格 | 服务当前配置 |
| episodes | int | 否 | 生成的剧集数量 | 4 |
| llm_model | string | 是 | LLM 模型；前端从 `backend/config.yaml` 读取默认值并传递 | - |
| vlm_model | string | 是 | VLM 评估模型；前端从 `backend/config.yaml` 读取默认值并传递 | - |
| image_t2i_model | string | 是 | 文生图模型；前端从 `backend/config.yaml` 读取默认值并传递 | - |
| image_it2i_model | string | 是 | 图生图模型；前端从 `backend/config.yaml` 读取默认值并传递 | - |
| video_model | string | 否 | 兼容视频模型字段；也可使用分模式字段或服务默认配置 | - |
| enable_concurrency | bool | 否 | 开启并发生成（可同时生成多张图片/视频） | true |

**响应示例**:
```json
{
  "session_id": "1773208355389",
  "status": {"script_generation": "completed", "character_design": "pending"},
  "params": {
    "idea": "故事线描述",
    "style": "anime",
    "llm_model": "deepseek-v3.2",
    "vlm_model": "qwen3.5-plus",
    "episodes": 4
  }
}
```

---

### 2. 执行阶段

执行指定的生成阶段。

**接口**: `POST /api/project/{session_id}/execute/{stage}`

**路径参数**:
- `session_id`: 项目会话 ID
- `stage`: 阶段 ID（见上表）

**请求体**:
```json
{
  "style": "anime"
}
```

> 请求体参数与创建项目相同（可选），会覆盖项目中已有的对应参数。

**响应**: HTTP 流式返回，内容为逐行 JSON（保持现有客户端协议），包含以下事件类型：
- `progress`: 进度更新
- `stage_complete`: 阶段完成
- `error`: 执行错误

**示例 - 进度事件**:
```json
{
  "type": "progress",
  "message": "剧本生成: 正在生成...",
  "phase": "剧本生成",
  "step_desc": "正在生成...",
  "percent": 50
}
```

**示例 - 阶段完成事件**:
```json
{
  "type": "stage_complete",
  "stage": "script_generation",
  "status": {"script_generation": "completed", "character_design": "pending"},
  "requires_intervention": false
}
```

---

### 3. 获取项目状态

获取项目的当前状态。

**接口**: `GET /api/project/{session_id}/status`

**响应示例**:
```json
{
  "session_id": "1773208355389",
  "current_stage": "script_generation",
  "status": {"script_generation": "running", "character_design": "pending"},
  "error": null,
  "stage_progress": {},
  "artifacts": {},
  "meta": {
    "idea": "故事线描述",
    "style": "anime"
  },
  "updated_at": "2026-10-15T20:00:00"
}
```

---

### 4. 获取阶段产物

获取指定阶段的产物数据。

**接口**: `GET /api/project/{session_id}/artifact/{stage}`

**响应示例** (剧本生成阶段):
```json
{
  "stage": "script_generation",
  "artifact": {
    "title": "剧本名称",
    "logline": "...",
    "characters": [...],
    "settings": [...],
    "episodes": [...]
  }
}
```

---

### 5. 更新阶段产物

更新指定阶段的产物数据（如用户修改提示词、选择版本）。

**接口**: `PATCH /api/project/{session_id}/artifact/{stage}`

请求体格式**因阶段而异**：

#### storyboard — 修改分集/片段/镜头
```json
{
  "episodes": [
    {
      "episode_number": 1,
      "segments": [
        {
          "segment_id": "seg_01_01",
          "total_duration": 10,
          "visual_prompt": "新视觉提示词",
          "video_prompt": "新视频提示词",
          "shots": []
        }
      ]
    }
  ]
}
```

#### reference_generation — 修改视觉提示词
```json
{
  "segments": [
    {"segment_id": "seg_01_01", "visual_prompt": "新提示词"}
  ]
}
```

#### reference_generation — 选择参考图版本
```json
{
  "seg_01_01": "code/result/image/xxx/seg_01_01_v2.jpg"
}
```

#### video_generation — 修改片段描述/时长
```json
{
  "seg_01_01": {"description": "新描述", "duration": 5}
}
```

#### video_generation — 选择视频版本
```json
{
  "seg_01_01": "code/result/video/xxx/seg_01_01_v2.mp4"
}
```

**响应**: `{"status": "ok"}`

---

### 6. 干预阶段

对已完成的阶段进行修改并重新执行（重新生成部分产物）。

**接口**: `POST /api/project/{session_id}/intervene`

**请求体**:
```json
{
  "stage": "reference_generation",
  "modifications": {
    "regenerate_scenes": ["shot_001_01", "shot_001_02"]
  }
}
```

- `stage`：要干预的阶段
- `modifications`：修改内容，目前支持 `regenerate_scenes`（要重新生成的镜头 ID 列表）

**响应**: HTTP 流式返回，内容为逐行 JSON（保持现有客户端协议），包含以下事件类型：
- `progress`: 进度更新
- `stage_complete`: 阶段完成
- `error`: 执行错误

---

### 7. 确认并继续

确认当前阶段的修改，进入下一阶段。

**接口**: `POST /api/project/{session_id}/continue`

**响应示例**:
```json
{
  "status": "ready",
  "next_stage": "character_design"
}
```

---

### 8. 停止执行

停止当前正在执行的阶段。

**接口**: `POST /api/project/{session_id}/stop`

**响应示例**:
```json
{
  "status": "stopped"
}
```

---

### 9. 获取会话列表

获取所有历史项目列表。

**接口**: `GET /api/sessions`

**响应示例**:
```json
{
  "sessions": [
    {
      "id": "1773208355389",
      "idea": "故事线",
      "style": "anime",
      "date": 1792065600,
      "current_stage": "character_design",
      "status": {"script_generation": "completed", "character_design": "waiting"}
    }
  ]
}
```

---

### 10. 获取阶段列表

获取所有可用阶段列表。

**接口**: `GET /api/stages`

**响应示例**:
```json
{
  "stages": [
    {"id": "script_generation", "name": "剧本生成", "order": 1, "description": "将灵感转化为结构化剧本"},
    {"id": "character_design", "name": "角色/场景设计", "order": 2},
    {"id": "storyboard", "name": "分镜设计", "order": 3},
    {"id": "reference_generation", "name": "参考图生成", "order": 4},
    {"id": "video_generation", "name": "视频生成", "order": 5},
    {"id": "post_production", "name": "后期剪辑", "order": 6}
  ]
}
```

---

## 调用示例

### 完整流程示例

```bash
# 1. 创建项目
SESSION_ID=$(curl -s -X POST http://localhost:8000/api/project/start \
  -H "Content-Type: application/json" \
  -d '{
    "idea": "失忆女刺客刺杀目标时恢复记忆,与爱人联手复仇师兄",
    "style": "anime"
  }' | jq -r '.session_id')

echo "Session ID: $SESSION_ID"

# 2. 执行第一阶段（剧本生成）- 监听 SSE
curl -X POST "http://localhost:8000/api/project/${SESSION_ID}/execute/script_generation" \
  -H "Content-Type: application/json" \
  -d '{"style": "anime"}'

# 3. 获取剧本产物
curl -s "http://localhost:8000/api/project/${SESSION_ID}/artifact/script_generation"

# 4. 确认并继续到下一阶段
curl -s -X POST "http://localhost:8000/api/project/${SESSION_ID}/continue"

# 5. 执行第二阶段（角色设计）
curl -X POST "http://localhost:8000/api/project/${SESSION_ID}/execute/character_design" \
  -H "Content-Type: application/json" \
  -d '{"style": "anime"}'

# 6. 确认并继续
curl -s -X POST "http://localhost:8000/api/project/${SESSION_ID}/continue"

# 7. 执行第三阶段（分镜设计）
curl -X POST "http://localhost:8000/api/project/${SESSION_ID}/execute/storyboard" \
  -H "Content-Type: application/json" \
  -d '{"style": "anime"}'

# 8. 确认并继续
curl -s -X POST "http://localhost:8000/api/project/${SESSION_ID}/continue"

# 9. 执行第四阶段（参考图生成）
curl -X POST "http://localhost:8000/api/project/${SESSION_ID}/execute/reference_generation" \
  -H "Content-Type: application/json" \
  -d '{"style": "anime"}'

# 10. 确认并继续
curl -s -X POST "http://localhost:8000/api/project/${SESSION_ID}/continue"

# 11. 执行第五阶段（视频生成）
curl -X POST "http://localhost:8000/api/project/${SESSION_ID}/execute/video_generation" \
  -H "Content-Type: application/json" \
  -d '{"style": "anime"}'

# 12. 确认并继续
curl -s -X POST "http://localhost:8000/api/project/${SESSION_ID}/continue"

# 13. 执行第六阶段（后期剪辑）
curl -X POST "http://localhost:8000/api/project/${SESSION_ID}/execute/post_production" \
  -H "Content-Type: application/json" \
  -d '{"style": "anime"}'
```

### 使用 jq 简化

```bash
# 创建项目并提取 session_id
SESSION_ID=$(curl -s -X POST http://localhost:8000/api/project/start \
  -H "Content-Type: application/json" \
  -d '{"idea": "故事线", "style": "anime"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")

# 查看项目状态
curl -s "http://localhost:8000/api/project/${SESSION_ID}/status" | python3 -m json.tool
```

---

## 错误处理

### HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

### 错误响应格式

```json
{
  "detail": "错误描述"
}
```

### 常见错误

| 错误 | 说明 |
|------|------|
| Session not found | 指定的 session_id 不存在 |
| Artifact for stage 'xxx' not found | 指定阶段的产物不存在 |
| 阶段执行失败 | 阶段执行过程中发生错误 |

---

## 前端与 API 共享

API 和前端共享同一个 session 存储：
- Session 文件位置: `backend/code/data/sessions/{session_id}.json`
- 产物位置: `backend/code/result/`

这意味着：
1. API 创建的项目可以在前端查看和继续
2. 前端创建的项目可以继续使用 API 操作
3. 两种方式可以随时切换


## 2026-10-15 后端审计后的接口约定

原有 `/api` 路径、包名与启动入口保留。当前服务显示名为 **Leon TV**，完整接口定义以运行服务的 `/openapi.json` 为准。

### 会话与生成任务

- 会话/任务 ID 必须为 1–128 个字母、数字、点、下划线或连字符，首字符为字母或数字；禁止路径分隔符。原有数字、UUID 和连字符 ID 保持兼容。非法 ID 返回 400，缺失会话返回 404，非法阶段或非对象 JSON 返回 400。
- 用户明确传入的 `false` 保留，不再被会话默认的 `true` 覆盖。新建会话调用 `/continue` 后返回 `ready` 和 `next_stage=script_generation`。
- 六阶段重启恢复时，无存活执行器的 `running` 在内存恢复为 `stopped`，保留产物并提示核查提供商任务；不会自动重提交模型。专项流水线的旧 `running` 查询为 `failed` 并附执行中断提示，同样不自动重跑。
- 工作流流式响应沿用现有逐行 JSON：`progress`、`stage_complete` 或 `error`。它不是标准 `data:` 格式 SSE。失败保留 `content` 字段，增加 `code`；用户停止为 `TASK_STOPPED`，其他错误为 `WORKFLOW_FAILED`，内部堆栈仅留在后端日志。后台完成会唤醒连接，不再等下一次心跳。
- `/api/tasks/{task_id}/events` 仍为标准 SSE。完成或失败任务的初始快照发出后关闭连接。删除运行中的专项任务返回 409；存储路径不匹配时返回 400。
- `/api/sandbox/jobs` 及已有土豆视频任务的 `/resume` 保留，恢复查询与重新付费生成分开。本轮没有真实模型调用，不能把离线测试当作供应商验收。

### 上传与缓存

- `/api/upload_media` 响应保持 `{filename, file_path}`；新文件持久保存在 `code/result/uploads/`，`file_path` 为绝对路径，展示时可转换为 `/code/result/uploads/<文件名>`。
- `/api/cache/temp` 保留 `deleted`、`freed_bytes`、`freed_mb`、`errors` 字段，增加 `preserved: string[]`，列出因被项目/会话/任务引用而保留的旧缓存项。界面可提示“部分已引用素材已保留”。索引损坏无法安全判断引用时返回 409，并保留缓存。
- 普通上传与阶段图片上传均校验内容；伪装图片、空文件、无视频画面的媒体返回 400，超过既有大小限制返回 413（阶段图片维持原有 400）。Word 解析实际依赖 `.docx` 结构；旧二进制 `.doc` 请先转换。

### 参数与产物

- 临时工具显式 `temperature`（0–2）传入 LLM 适配层；省略时保留各服务商原有默认。供应商是否接受某模型的该参数仍需实际验证。
- 文生图显式 `style` 使用既有风格模板或自定义风格文本；省略时不暗加风格。GPT 参考图传给既有 `image_urls` 适配参数；这只证明参数未丢失，不证明供应商图生图实际效果。
- 空图像结果返回 `success=false`，不再创建成功历史。公网结果 URL 保持 HTTP(S) 格式。
- standard 流水线显式 `split_mode` 使用原有拆分函数；省略时保留按句号拆分。灵感模式先取 `segment_count`，再兼容 `n_scenes`。`generate_audio=false` 跳过 TTS，输出视频不带音轨；`audios=[]`。默认配音流程保留。
- 后期导出缺少任一已选片段时返回失败和缺失片段 ID，不再用剩余镜头伪装完整成片。六阶段、Studio 和专项拼接共用文件名转义逻辑，支持单引号文件名。
- VLM 无法解析或缺失有效评分时，不再伪造“评估通过”；已生成角色图保留待人工检查。参考图和角色的总体质量验收仍需产品样例与真实模型验证。


### 共享媒体的删除保护（LTV-AUDIT-20261015-BE-REF）

- 删除前扫描已保存画布（含副本、版本和回收站）、其他会话、专项任务、临时工具历史及 job 元数据。绝对路径、`result/...`、`code/...` 和 `/code/...` 会归一化后比较；不访问外部媒体 URL。
- `DELETE /api/sessions/{session_id}` 和 `DELETE /api/tasks/{task_id}`：产物被其他对象引用时返回 **409**，保留该对象元数据和媒体。解除外部引用后仍可正常删除；对象自己的元数据不阻止自己的删除。
- `DELETE /api/sessions`：保持原有 `status` 和 `cleaned`，新增 `preserved: {scripts: string[], images: string[], videos: string[]}`；被引用的孤立素材保留，未引用项照常清理。
- `DELETE /api/sandbox/history/{record_id}`：删除历史记录，保留其他对象仍在引用的媒体；响应在 `success` 外增加 `preserved: string[]` 和 `skipped: string[]`。前者为因引用保留的文件，后者为缺失、范围不明或删除失败的文件。其他历史记录和独立 job 仍算有效引用。
- 索引 JSON 无法读取或不能安全遍历时返回 **409**，在修改元数据或删除媒体之前中止。缓存清理复用同一引用扫描。

该补丁基于现有本地元数据快照，没有引入数据库或资产库迁移。跨进程并发写入与删除的事务协调不属于此补丁。
