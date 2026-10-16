# 本地 Harness 接线验证

日期：2026-10-15。入口：`experiments/harness_poc/`，独立 CLI，未接入生产 API、状态机或前端。

## 实际证明的范围

planner、worker、reviewer 是**确定性适配器**：固定目标、规则分派和结构化消息，不调用 LLM，不是自主 Agent 团队，也不是标准 A2A 实现。真实执行部分是现有 `pipelines.utils.create_static_image_clip`、`concat_videos` 和 FFmpeg 媒体审核。

目标为两个合成色块片段：红色 1 秒 → 蓝色 1 秒，1920×1080、30fps、静音、总计 2 秒。它不是 AI 模型生成的视频，也不验证创作质量或真实模型短片质量。

默认流程：

1. planner 发出固定目标与工具依赖。
2. worker 用现有函数生成红、蓝素材。
3. 第一次拼接故意引用不存在的第二个文件，真实触发现有函数的 `FileNotFoundError`。
4. reviewer 发现产物不存在，输出失败；修正消息引用审核证据。
5. worker 使用两个已验证素材重新拼接。
6. reviewer 对真实文件完整解码，并检查帧数、帧率、画幅、音轨与第 0/30 帧颜色。
7. 通过后停在 `waiting_human_review`，`human_review: null`。没有自动发布步骤。

`--fault omit_clip` 会先生成有效的 1 秒视频，真实审核拒绝其时长和颜色顺序，再修正为 2 秒。`--fault none` 不注入初始故障。

## 执行方式

在应用的 `backend/` 执行，使用已有虚拟环境，无须安装：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m experiments.harness_poc run \
  --run-dir experiments/harness_poc/runs/example --pause-after-tools 2
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m experiments.harness_poc resume \
  --run-dir experiments/harness_poc/runs/example
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m experiments.harness_poc status \
  --run-dir experiments/harness_poc/runs/example
```

CLI 输出完整 JSON。退出码：暂停、待人工审查或明确人工决定为 0；执行失败、预算耗尽、拒绝恢复为 2。新建已存在的 run 会拒绝执行，须显式 `resume`。恢复使用持久化预算，CLI 默认参数不会覆盖它。

真实人工审查之后才可操作以下命令；本轮演示没有执行批准：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m experiments.harness_poc approve \
  --run-dir experiments/harness_poc/runs/example \
  --artifact-sha '<state.json 中的 artifact.sha256>' --reviewer '<审查人>'
```

`reject` 使用相同参数。必须处于人工审查状态、填写审查人并匹配当前文件哈希。这里是本地人工操作记录，不提供生产身份认证，也没有发布功能。

## 持久化与执行边界

每个 run 包含原子替换的 `state.json`、`calls/<task>/request.json`、`receipt.json`、worker 日志和 `artifacts/`。状态包含角色消息、任务依赖、生产及实验代码指纹、目标、预算和审核事实。请求、回执、输出都有 SHA-256 校验；它们是本地一致性校验，不是防篡改签名。

- 调用前持久化任务和预算，再派发独立 worker；失败调用也计数。
- 已完成任务恢复时校验回执和输出，不重新生成。终态恢复不派发工具。
- worker 已写回执而父进程未保存结果时，恢复从回执对账。
- 只有执行意图、没有回执时，转 `interrupted`，要求人工检查，不自动重跑。
- 改变目标/代码、丢失回执、改变输入或产物时拒绝继续；新建 run 才能重新执行。
- 同一 run 用 `fcntl.flock` 排他，worker 继承锁描述符，避免父进程意外退出后并发执行。
- 写路径只允许实验 `runs/`、`.test-runs/` 及指定的本轮 evidence 目录；工具参数使用现有 `contained_storage_path`。工具白名单只有创建色块视频、拼接、审核，没有通用 shell、网络、模型或发布工具。

默认上限：6 次逻辑工具调用、1 次修正、2 次审核尝试、单工具 30 秒、累计工具执行 120 秒。`--max-calls`、`--max-repairs`、`--tool-timeout`、`--total-seconds` 只接受限定范围；失败或耗尽不会伪装成完成。累计时间统计已派发 worker 的墙钟执行时间，不计暂停/人工等待；若恢复时任务无回执，保守计入该任务完整超时额度。超时杀死本次创建的进程组并回收 worker；从不根据磁盘上保存的旧 PID 发送信号。

仅在实验 worker 环境设置 `IMAGEIO_FFMPEG_NO_PREVENT_SIGINT=1`，使 imageio 解码子进程不另建进程组。针对测试在真实 imageio 解码器启动后暂停读取，确认其存活且 PGID 等于 worker PID，再触发审核阶段超时，验证 worker 已回收、解码器不存在或已终止等待系统回收；没有修改全局环境或依赖包。

该 PoC 使用 `fcntl`、`pass_fds` 和 POSIX 进程组，仅在本机 macOS 验证；不宣称 Windows 通过。白名单和路径检查不是操作系统沙箱，信任仓库内 Python/FFmpeg 实现；没有搭建全局文件或网络拦截机制。原有生产函数仍会读取本机配置，但该流程没有模型调用路径，不输出配置或密钥。产物都是本地合成素材；生产代码及用户数据不在本次写范围内。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_harness_poc.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache --select F,E701,E722,I001 \
  experiments/harness_poc tests/test_harness_poc.py
```

集成用例真实执行 FFmpeg，涵盖缺片修正、可播放短片被拒绝、正常恢复复用、终态重复恢复、预算耗尽、真实 worker 超时以及人工门禁。父进程检查点落后、无回执、篡改、持续错误修正等是明确标注的受控故障注入。人工 approve/reject 仅在测试临时副本模拟，不批准实际演示。测试目录位于实验目录下并自动清理。

上述检查使用合成素材和测试临时副本；历史运行日志属于本地资料，不随源码发布。请在自己的环境重新运行，不把此文档视为当前测试通过凭证。
