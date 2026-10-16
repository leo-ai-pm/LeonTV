import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from api.schemas.sandbox import (
    SandboxI2IRequest,
    SandboxJobRequest,
    SandboxLLMRequest,
    SandboxT2IRequest,
    SandboxVideoRequest,
    SandboxVLMRequest,
)
from config import settings
from media_references import (
    MediaReferenceError,
    collect_media_references,
    local_media_paths,
)
from path_security import resolve_media_reference
from prompts.loader import PROMPTS_DIR, load_prompt

router = APIRouter(tags=["Sandbox"])
logger = logging.getLogger(__name__)


SANDBOX_DIR = os.path.join(settings.CODE_DIR, "result", "sandbox")
SANDBOX_HISTORY_FILE = os.path.join(SANDBOX_DIR, "history.json")
SANDBOX_ACTIVE_TASKS: dict[str, dict] = {}
SANDBOX_LOCK = threading.RLock()
SANDBOX_JOBS_DIR = Path(SANDBOX_DIR) / "jobs"
SANDBOX_JOB_TASKS: dict[str, asyncio.Task] = {}

# 确保目录存在
os.makedirs(SANDBOX_DIR, exist_ok=True)
SANDBOX_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _write_job(job: dict) -> None:
    path = SANDBOX_JOBS_DIR / f"{job['id']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


async def _run_job(request: SandboxJobRequest, resume_receipt: str | None = None) -> None:
    try:
        if request.tool == "video":
            result = await _generate_sandbox_video(SandboxVideoRequest(**request.model_dump()),
                                                  job_id=request.job_id, resume_receipt=resume_receipt)
        elif request.tool == "i2i":
            result = await sandbox_i2i(SandboxI2IRequest(**request.model_dump()))
        else:
            result = await sandbox_t2i(SandboxT2IRequest(**request.model_dump()))
    except Exception as exc:
        logger.exception("Sandbox async job failed: %s", request.job_id)
        result = {"success": False, "error": str(getattr(exc, "detail", exc))}
    with SANDBOX_LOCK:
        path = SANDBOX_JOBS_DIR / f"{request.job_id}.json"
        job = json.loads(path.read_text(encoding="utf-8"))
        job.update(status="completed" if result.get("success") else
                   "interrupted" if result.get("query_pending") else "failed",
                   output=result, completed_at=datetime.now().isoformat())
        _write_job(job)
        SANDBOX_JOB_TASKS.pop(request.job_id, None)


@router.post("/api/sandbox/jobs", status_code=202)
async def sandbox_start_job(request: SandboxJobRequest):
    """Return before slow provider work so browser proxies never wait on generation."""
    if request.tool == "i2i" and not request.image:
        raise HTTPException(400, "图生图需要参考图")
    if request.image:
        _validated_media_reference(request.image)
    with SANDBOX_LOCK:
        path = SANDBOX_JOBS_DIR / f"{request.job_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if SandboxJobRequest(**existing["input"]).model_dump() != request.model_dump():
                raise HTTPException(409, "这个任务 ID 已用于另一条请求")
            return {"id": existing["id"], "status": existing["status"]}
        job = {"id": request.job_id, "status": "running", "input": request.model_dump(),
               "created_at": datetime.now().isoformat(), "output": None}
        _write_job(job)
        SANDBOX_JOB_TASKS[request.job_id] = asyncio.create_task(_run_job(request))
    return {"id": request.job_id, "status": "running"}


@router.get("/api/sandbox/jobs/{job_id}")
async def sandbox_get_job(job_id: str):
    if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
        raise HTTPException(400, "Invalid job ID")
    with SANDBOX_LOCK:
        path = SANDBOX_JOBS_DIR / f"{job_id}.json"
        if not path.is_file():
            raise HTTPException(404, "生成任务不存在")
        job = json.loads(path.read_text(encoding="utf-8"))
        if job["status"] == "running" and job_id not in SANDBOX_JOB_TASKS:
            job.update(status="interrupted", output={"success": False, "query_pending": True,
                       "provider_task": job.get("provider_task"),
                       "error": "后端重启中断了查询，上游状态尚未确认。请查询原任务，避免重复提交。"})
            _write_job(job)
        return {key: job.get(key) for key in ("id", "status", "output", "created_at", "completed_at")}


@router.post("/api/sandbox/jobs/{job_id}/resume", status_code=202)
async def sandbox_resume_job(job_id: str):
    """Resume a persisted Tudou video task without submitting another generation."""
    await sandbox_get_job(job_id)
    with SANDBOX_LOCK:
        job = json.loads((SANDBOX_JOBS_DIR / f"{job_id}.json").read_text(encoding="utf-8"))
        if job_id in SANDBOX_JOB_TASKS:
            return {"id": job_id, "status": "running"}
        if job["status"] != "interrupted":
            raise HTTPException(409, "只有查询中断的任务可以继续查询；上游明确失败的任务不会重新提交。")
        receipt_path = job.get("provider_receipt_path")
        if not receipt_path:
            raise HTTPException(409, "原任务缺少可恢复的提供商回执，请先核查提供商任务记录。")
        try:
            resolved = resolve_media_reference(receipt_path, [SANDBOX_DIR], allow_remote_urls=False)
        except ValueError as exc:
            raise HTTPException(409, "原任务回执不存在或路径无效。") from exc
        receipt = json.loads(Path(resolved).read_text(encoding="utf-8"))
        original = SandboxJobRequest(**job["input"])
        from models.video_client import VideoClient
        expected = {"model": original.model, "prompt": original.prompt,
                    "duration": original.duration, "aspect_ratio": original.ratio,
                    "resolution": VideoClient._normalize_seedance_resolution(original.resolution)}
        if any((receipt.get("request") or {}).get(key) != value for key, value in expected.items()):
            raise HTTPException(409, "原任务回执与已保存的生成参数不匹配，请先核查任务记录。")
        known_task = (job.get("output") or {}).get("provider_task") or job.get("provider_task") or {}
        # The provider may have accepted the task just before an atomic receipt write failed.
        # Recover only an ID already recorded by this job, never one sent by a caller.
        if (not receipt.get("task_id") and known_task.get("provider") == "tudou"
                and known_task.get("task_id") and known_task.get("error_code") == "receipt_failed"):
            receipt.update(task_id=known_task["task_id"], status="unknown")
        if receipt.get("provider") != "tudou" or not receipt.get("task_id"):
            raise HTTPException(409, "原提交是否被受理尚未确认，请先核查土豆任务记录，避免重复扣费。")
        save_path = str(Path(receipt["save_path"]).resolve())
        if not Path(save_path).is_relative_to((Path(SANDBOX_DIR) / "videos").resolve()):
            raise HTTPException(409, "原任务输出路径无效。")
        if Path(resolved) != Path(f"{save_path}.tudou-task.json"):
            raise HTTPException(409, "原任务回执与输出路径不匹配。")
        from models.tudou_client import TudouClient
        TudouClient()._save_receipt(Path(resolved), receipt)
        job.update(status="running", completed_at=None)
        _write_job(job)
        SANDBOX_JOB_TASKS[job_id] = asyncio.create_task(
            _run_job(original, resume_receipt=resolved))
    return {"id": job_id, "status": "running"}


def _persist_video_task(job_id: str | None, receipt_path: str, context: dict | None = None) -> None:
    if not job_id:
        return
    with SANDBOX_LOCK:
        path = SANDBOX_JOBS_DIR / f"{job_id}.json"
        job = json.loads(path.read_text(encoding="utf-8"))
        job["provider_receipt_path"] = receipt_path
        if context:
            job["provider_task"] = {key: context.get(key) for key in
                                    ("provider", "task_id", "status", "error_code")}
        _write_job(job)


def _validated_media_reference(value: str) -> str:
    try:
        return resolve_media_reference(
            value,
            [settings.TEMP_DIR, settings.CODE_DIR],
            allow_remote_urls=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load_history() -> List[dict]:
    """加载历史记录"""
    with SANDBOX_LOCK:
        if os.path.exists(SANDBOX_HISTORY_FILE):
            try:
                with open(SANDBOX_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                logger.warning("Failed to load sandbox history: %s", SANDBOX_HISTORY_FILE, exc_info=True)
                return []
        return []


def _save_history(history: List[dict]):
    """保存历史记录"""
    with SANDBOX_LOCK:
        tmp_path = f"{SANDBOX_HISTORY_FILE}.{uuid.uuid4().hex}.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, SANDBOX_HISTORY_FILE)


def _normalize_path(path: str) -> str:
    """将绝对路径转换为相对路径格式 result/..."""
    if not path:
        return path
    if path.startswith(("http://", "https://")):
        return path
    # 如果已经是相对路径，直接返回
    if not path.startswith('/'):
        # 确保以 result/ 开头
        if not path.startswith('result/'):
            return f"result/{path}"
        return path
    # 绝对路径，提取相对于 CODE_DIR 的部分
    code_dir = settings.CODE_DIR
    if path.startswith(code_dir):
        relative = path[len(code_dir):].lstrip('/')
        # 直接返回 result/... 格式，因为 /code/ 会映射到 CODE_DIR
        return relative
    # 其他绝对路径，尝试提取文件名
    return path.split('/')[-1]


def _convert_output_paths(output_data: dict) -> dict:
    """转换 output 中的路径为相对路径格式"""
    if not output_data:
        return output_data
    converted = output_data.copy()
    # 转换 images
    if 'images' in converted and isinstance(converted['images'], list):
        converted['images'] = [_normalize_path(img) for img in converted['images']]
    # 转换 video_path
    if 'video_path' in converted and converted['video_path']:
        converted['video_path'] = _normalize_path(converted['video_path'])
    # 转换 input 中的 reference_image
    if 'reference_image' in converted.get('input', {}):
        input_copy = converted['input'].copy()
        input_copy['reference_image'] = _normalize_path(input_copy['reference_image'])
        converted['input'] = input_copy
    return converted


def _converted_result_list(paths: List[str] | None) -> List[str]:
    """Return generated image paths in the same format used by sandbox history."""
    converted = _convert_output_paths({"images": paths or []})
    return converted.get("images", [])


def _converted_video_path(path: str | None) -> str:
    """Return generated video path in the same format used by sandbox history."""
    converted = _convert_output_paths({"video_path": path or ""})
    return converted.get("video_path", "")


def _add_record(
    tool: str,
    model: str,
    input_data: dict,
    output_data: dict,
    files: List[str] = None,
    record_id: str | None = None,
) -> str:
    """添加历史记录"""
    with SANDBOX_LOCK:
        record_id = record_id or str(uuid.uuid4().hex[:8])
        # 转换路径为相对路径格式
        output_data = _convert_output_paths(output_data)
        record = {
            "id": record_id,
            "tool": tool,
            "model": model,
            "input": input_data,
            "output": output_data,
            "files": files or [],
            "created_at": datetime.now().isoformat(),
        }
        history = _load_history()
        history.insert(0, record)  # 最新记录放在最前面
        _save_history(history)
        return record_id


def _start_active_task(tool: str, model: str, input_data: dict) -> str:
    with SANDBOX_LOCK:
        task_id = str(uuid.uuid4().hex[:8])
        SANDBOX_ACTIVE_TASKS[task_id] = {
            "id": task_id,
            "tool": tool,
            "model": model,
            "input": input_data,
            "status": "running",
            "progress": 1,
            "created_at": datetime.now().isoformat(),
        }
        return task_id


def _finish_active_task(task_id: str) -> None:
    with SANDBOX_LOCK:
        SANDBOX_ACTIVE_TASKS.pop(task_id, None)


def _delete_record_files(files: List[str], references=None):
    """删除记录关联的文件"""
    if references is None:
        references = collect_media_references(settings.CODE_DIR, settings.TEMP_DIR)
    preserved, skipped = [], []
    for f in files:
        candidates = [path for path in local_media_paths(f, settings.CODE_DIR, settings.TEMP_DIR)
                      if path.is_file()]
        if len(candidates) != 1:
            skipped.append(f)
            continue
        target = candidates[0]
        if references.contains(target):
            preserved.append(f)
            continue
        if target.exists():
            try:
                target.unlink()
                if target.suffix.lower() == ".png":
                    sidecar = Path(f"{target}.tudou-source.json")
                    if sidecar.is_file() and not references.contains(sidecar):
                        sidecar.unlink()
            except Exception:
                logger.warning("Failed to delete sandbox artifact: %s", f, exc_info=True)
                skipped.append(f)
    return {"preserved": preserved, "skipped": skipped}


# 请求模型
@router.get("/api/sandbox/history")
async def sandbox_get_history():
    """获取历史记录列表"""
    history = _load_history()
    # 返回完整信息（包括 output）
    return {
        "success": True,
        "records": [
            {
                "id": r["id"],
                "tool": r["tool"],
                "model": r["model"],
                "input": r["input"],
                "output": r.get("output"),
                "created_at": r["created_at"],
            }
            for r in history
        ]
    }


@router.get("/api/sandbox/tasks")
async def sandbox_get_active_tasks():
    """获取临时工作台正在执行的任务"""
    with SANDBOX_LOCK:
        tasks = list(SANDBOX_ACTIVE_TASKS.values())
    return {"success": True, "tasks": tasks}


@router.get("/api/sandbox/history/{record_id}")
async def sandbox_get_record(record_id: str):
    """获取单条历史记录详情"""
    history = _load_history()
    for r in history:
        if r["id"] == record_id:
            return {"success": True, "record": r}
    return {"success": False, "error": "记录不存在"}


@router.delete("/api/sandbox/history/{record_id}")
async def sandbox_delete_record(record_id: str):
    """删除历史记录"""
    with SANDBOX_LOCK:
        history = _load_history()
        record_to_delete = None
        new_history = []
        for r in history:
            if r["id"] == record_id:
                record_to_delete = r
            else:
                new_history.append(r)

        if record_to_delete is None:
            return {"success": False, "error": "记录不存在"}

        try:
            references = collect_media_references(
                settings.CODE_DIR, settings.TEMP_DIR,
                metadata_roots=[SANDBOX_DIR, SANDBOX_HISTORY_FILE, SANDBOX_JOBS_DIR],
                excluded_files=[SANDBOX_HISTORY_FILE],
                documents=[new_history, list(SANDBOX_ACTIVE_TASKS.values())],
            )
        except MediaReferenceError as exc:
            raise HTTPException(409, str(exc)) from exc
        _save_history(new_history)

    # 删除关联文件不需要占用历史锁。
    deletion = _delete_record_files(record_to_delete.get("files", []), references)
    logger.info("Sandbox history deleted: record_id=%s", record_id)
    return {"success": True, **deletion}


@router.post("/api/sandbox/llm")
async def sandbox_llm(req: SandboxLLMRequest):
    """临时工作台 - LLM 文字生成"""
    from models.llm_client import LLM
    client = LLM()
    input_data = {"prompt": req.prompt, "web_search": req.web_search, "temperature": req.temperature}
    task_id = _start_active_task("llm", req.model, input_data)
    try:
        logger.info("Sandbox LLM started: model=%s web_search=%s", req.model, req.web_search)
        result = await run_in_threadpool(
            client.query,
            req.prompt,
            model=req.model,
            web_search=req.web_search,
            **({"temperature": req.temperature} if "temperature" in req.model_fields_set else {}),
        )
        # ��存到历史记录
        record_id = _add_record(
            tool="llm",
            model=req.model,
            input_data=input_data,
            output_data={"response": result},
            record_id=task_id,
        )
        logger.info("Sandbox LLM completed: model=%s record_id=%s", req.model, record_id)
        return {"success": True, "result": result, "record_id": record_id}
    except Exception as e:
        logger.exception("Sandbox LLM failed: model=%s", req.model)
        return {"success": False, "error": str(e)}
    finally:
        _finish_active_task(task_id)


@router.post("/api/sandbox/vlm")
async def sandbox_vlm(req: SandboxVLMRequest):
    """临时工作台 - VLM 图片理解"""
    from models.vlm_client import VLM
    client = VLM()
    images = [_validated_media_reference(image) for image in req.images]
    input_data = {"prompt": req.prompt, "images": images}
    task_id = _start_active_task("vlm", req.model, input_data)
    try:
        logger.info("Sandbox VLM started: model=%s images=%d", req.model, len(req.images or []))
        result = await run_in_threadpool(
            client.query,
            req.prompt,
            image_paths=images,
            model=req.model,
        )
        # 保存到历史记录
        record_id = _add_record(
            tool="vlm",
            model=req.model,
            input_data=input_data,
            output_data={"response": result},
            record_id=task_id,
        )
        logger.info("Sandbox VLM completed: model=%s record_id=%s", req.model, record_id)
        return {"success": True, "result": result, "record_id": record_id}
    except Exception as e:
        logger.exception("Sandbox VLM failed: model=%s", req.model)
        return {"success": False, "error": str(e)}
    finally:
        _finish_active_task(task_id)


@router.post("/api/sandbox/t2i")
async def sandbox_t2i(req: SandboxT2IRequest):
    """临时工作台 - 文生图"""
    from models.image_client import ImageClient
    client = ImageClient()
    input_data = {"prompt": req.prompt, "style": req.style, "ratio": req.ratio}
    effective_prompt = req.prompt
    if req.style:
        known_styles = {path.stem for path in (Path(PROMPTS_DIR) / "style").glob("*.txt")}
        style_prompt = load_prompt("style", req.style) if req.style in known_styles else req.style
        effective_prompt = f"{req.prompt}\n\n画面风格：{style_prompt}"
    task_id = _start_active_task("t2i", req.model, input_data)
    try:
        logger.info("Sandbox T2I started: model=%s ratio=%s", req.model, req.ratio)
        result = await run_in_threadpool(
            client.generate_image,
            effective_prompt,
            model=req.model,
            image_paths=None,
            video_ratio=req.ratio,
        )
        if not result:
            raise ValueError("图像服务未返回可用结果")
        # result 是图片路径列表
        # 保存到历史记录
        record_id = _add_record(
            tool="t2i",
            model=req.model,
            input_data=input_data,
            output_data={"images": result},
            files=result if isinstance(result, list) else [],
            record_id=task_id,
        )
        logger.info(
            "Sandbox T2I completed: model=%s record_id=%s images=%d",
            req.model,
            record_id,
            len(result) if isinstance(result, list) else 0,
        )
        return {
            "success": True,
            "result": _converted_result_list(result if isinstance(result, list) else []),
            "record_id": record_id,
        }
    except Exception as e:
        logger.exception("Sandbox T2I failed: model=%s", req.model)
        return {"success": False, "error": str(e)}
    finally:
        _finish_active_task(task_id)


@router.post("/api/sandbox/i2i")
async def sandbox_i2i(req: SandboxI2IRequest):
    """临时工作台 - 图生图"""
    from models.image_client import ImageClient
    client = ImageClient()
    image = _validated_media_reference(req.image)
    input_data = {"prompt": req.prompt, "reference_image": image}
    task_id = _start_active_task("i2i", req.model, input_data)
    try:
        logger.info("Sandbox I2I started: model=%s ratio=%s", req.model, req.ratio)
        result = await run_in_threadpool(
            client.generate_image,
            req.prompt,
            image_paths=[image],
            model=req.model,
            video_ratio=req.ratio,
        )
        if not result:
            raise ValueError("图像服务未返回可用结果")
        # 保存到历史记录
        record_id = _add_record(
            tool="i2i",
            model=req.model,
            input_data=input_data,
            output_data={"images": result},
            files=result if isinstance(result, list) else [],
            record_id=task_id,
        )
        logger.info(
            "Sandbox I2I completed: model=%s record_id=%s images=%d",
            req.model,
            record_id,
            len(result) if isinstance(result, list) else 0,
        )
        return {
            "success": True,
            "result": _converted_result_list(result if isinstance(result, list) else []),
            "record_id": record_id,
        }
    except Exception as e:
        logger.exception("Sandbox I2I failed: model=%s", req.model)
        return {"success": False, "error": str(e)}
    finally:
        _finish_active_task(task_id)


@router.post("/api/sandbox/video")
async def sandbox_video(req: SandboxVideoRequest):
    return await _generate_sandbox_video(req)


async def _generate_sandbox_video(req: SandboxVideoRequest, *, job_id: str | None = None,
                                  resume_receipt: str | None = None):
    """临时工作台 - 视频生成"""
    from models.video_client import VideoClient
    client = VideoClient()
    image = req.image if resume_receipt else (_validated_media_reference(req.image) if req.image else None)
    input_data = {"prompt": req.prompt, "reference_image": image, "ratio": req.ratio,
                  "duration": req.duration, "resolution": req.resolution}
    task_id = _start_active_task("video", req.model, input_data)
    try:
        # 生成唯一的保存路径
        save_dir = os.path.join(SANDBOX_DIR, "videos")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{uuid.uuid4().hex[:8]}.mp4")
        receipt_path = f"{save_path}.tudou-task.json"
        if resume_receipt:
            receipt_path = resume_receipt
            save_path = json.loads(Path(receipt_path).read_text(encoding="utf-8"))["save_path"]
        _persist_video_task(job_id, receipt_path)
        def task_callback(context):
            _persist_video_task(job_id, receipt_path, context)
        logger.info("Sandbox video started: model=%s image=%s", req.model, bool(req.image))

        if resume_receipt:
            from models.tudou_client import TudouClient
            result = await run_in_threadpool(TudouClient(task_callback=task_callback).resume_video, receipt_path)
        else:
            result = await run_in_threadpool(
                client.generate_video,
                prompt=req.prompt,
                image_path=image or "",
                save_path=save_path,
                model=req.model,
                duration=req.duration,
                video_ratio=req.ratio,
                resolution=req.resolution,
                provider_task_callback=task_callback,
                shot_type="multi",
            )
        # 保存到历史记录
        record_id = _add_record(
            tool="video",
            model=req.model,
            input_data=input_data,
            output_data={"video": result, "video_path": save_path},
            files=[save_path],
            record_id=task_id,
        )
        logger.info("Sandbox video completed: model=%s record_id=%s video=%s", req.model, record_id, save_path)
        return {
            "success": True,
            "result": result,
            "video_path": _converted_video_path(save_path),
            "record_id": record_id,
        }
    except Exception as e:
        logger.exception("Sandbox video failed: model=%s", req.model)
        output = {"success": False, "error": str(e)}
        if hasattr(e, "task_id"):
            output.update(provider_task={"provider": "tudou", "task_id": e.task_id,
                                         "status": e.status, "error_code": e.code},
                          query_pending=e.status == "unknown" or e.code == "delivery_failed")
        if "receipt_path" in locals() and Path(receipt_path).is_file():
            receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
            status = receipt.get("status")
            error_code = (receipt.get("error") or {}).get("code")
            if "provider_task" not in output:
                output.update(provider_task={"provider": "tudou", "task_id": receipt.get("task_id"),
                                             "status": status, "error_code": error_code},
                              query_pending=status in {"unknown", "submitting"} or error_code == "delivery_failed")
        return output
    finally:
        _finish_active_task(task_id)
