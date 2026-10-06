# backend/routes.py — FastAPI 装配（CORS/请求中间件/静态挂载）与全部路由
import asyncio
import logging
import os
import tempfile
import time
from datetime import datetime

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import batches
import config
import history
import logger
import yolo
from batches import url_for

LOG = logging.getLogger("crack")


def create_app() -> FastAPI:
    """装配应用：CORS -> 请求日志中间件 -> 静态挂载 -> 注册路由。"""
    app = FastAPI(title="水工建筑物智能巡检分析系统")

    # 前端以 file:// 打开，全部放行
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    # 每请求打点；未捕获异常记完整堆栈后原样抛出
    @app.middleware("http")
    async def request_logging(request, call_next):
        t0 = time.time()
        try:
            response = await call_next(request)
        except Exception:
            LOG.exception("请求处理异常：%s %s", request.method, request.url.path)
            raise
        cost_ms = round((time.time() - t0) * 1000, 1)
        LOG.info("%s %s -> %s %sms", request.method, request.url.path,
                 response.status_code, cost_ms)
        return response

    # 静态挂载前必须先建目录
    os.makedirs(config.STATIC_DIR, exist_ok=True)
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

    _register_routes(app)
    LOG.info("FastAPI 装配完成：CORS + 请求日志中间件 + /static 挂载 + 业务路由")
    return app


def _register_routes(app: FastAPI) -> None:
    # ---------- 健康检查（注意：model_path 原样返回 "version2.pt"） ----------
    @app.get("/")
    def health():
        return {"message": "服务运行中",
                "model_loaded": yolo.MODEL is not None,
                "model_path": config.MODEL_PATH,
                "static_dir_exists": os.path.isdir(config.STATIC_DIR)}

    # ---------- 创建批次并上传图片 ----------
    @app.post("/api/api/batch/init")
    async def batch_init(files: list[UploadFile] = File(default=[]),
                         conf: str = Form("0.05"), mode: str = Form("folder")):
        items = []
        for f in files:
            items.append((f.filename or "", await f.read()))
        result = batches.create_batch(items, conf, mode)
        if "status" not in result:  # 业务失败：HTTP 200 + error 体
            LOG.warning("batch/init 失败：%s（batch_id=%s）",
                        result.get("error"), result.get("batch_id"))
        return result

    # ---------- 启动后台识别（立即返回） ----------
    @app.post("/api/api/batch/run")
    async def batch_run(batch_id: str = Form(...)):
        result = batches.start_batch(batch_id)
        if "status" not in result:
            LOG.warning("batch/run 失败：%s", result.get("error"))
        return result

    # ---------- 轮询批次进度（全角冒号由 Starlette 自动解码） ----------
    @app.get("/api/api/batch/status/{batch_id}")
    def batch_status(batch_id: str):
        b = batches.BATCHES.get(batch_id)
        if b is None:
            LOG.warning("batch/status 失败：批次不存在 %s", batch_id)
            return {"error": "批次不存在：" + batch_id, "batch_id": batch_id}
        return batches.summary(b, "status")

    # ---------- 历史批次列表（新批次在前，7 字段专构） ----------
    @app.get("/api/api/batches")
    def batch_list():
        items = [{"batch_id": b["batch_id"], "mode": b["mode"], "status": b["status"],
                  "total": b["total"], "processed": b["processed"],
                  "created_at": b["created_at"], "output_dir": b["output_dir"]}
                 for b in reversed(list(batches.BATCHES.values()))]
        LOG.info("batches 列表：%d 个批次", len(items))
        return {"total": len(items), "batches": items}

    # ---------- 单张同步检测（旧接口，保留兼容；经单 worker 线程池跑推理） ----------
    @app.post("/api/api/detect")
    async def detect(file: UploadFile = File(...), conf: str = Form("0.05")):
        data = await file.read()
        name = file.filename or ""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(batches.EXECUTOR, _detect_single, name, data, conf)

    # ---------- 检测记录（SQLite 全量，返回对象不是数组） ----------
    @app.get("/api/api/records")
    def records():
        items = history.query_all()
        return {"total": len(items), "records": items}

    # ---------- 清理历史日志（跳过本次启动的活动日志） ----------
    @app.post("/api/api/logs/clear")
    def logs_clear():
        active = os.path.abspath(logger.active_log_path())
        deleted = 0
        if os.path.isdir(config.LOG_DIR):
            for fn in sorted(os.listdir(config.LOG_DIR)):
                if not fn.lower().endswith(".log"):
                    continue
                path = os.path.abspath(os.path.join(config.LOG_DIR, fn))
                if path == active:
                    LOG.info("logs/clear 跳过当前活动日志：%s", fn)
                    continue
                try:
                    os.remove(path)
                    deleted += 1
                    LOG.info("logs/clear 删除日志：%s", fn)
                except OSError as e:
                    LOG.warning("logs/clear 删除失败 %s：%s", fn, e)
        LOG.core("logs/clear 完成：删除 %d 个日志文件", deleted)
        return {"deleted": deleted, "error": ""}


def _detect_single(filename: str, data: bytes, conf: str) -> dict:
    """单张同步检测的线程池实现：结果图 static/result_<uuid8>.png，成功失败都写 SQLite。"""
    conf_v = batches.parse_conf(conf)
    record_id = f"record_{os.urandom(4).hex()}"
    detection_time = datetime.now().strftime(config.CREATED_AT_FORMAT)
    ext = os.path.splitext(filename)[1].lower()

    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
        out_name = f"result_{os.urandom(4).hex()}.png"
        out_path = os.path.join(config.STATIC_DIR, out_name)
        res = yolo.detect_one(tmp_path, out_path, conf_v)
        result_url = url_for("/".join([config.STATIC_DIR, out_name]))
        history.add_record({
            "record_id": record_id, "filename": filename, "stored_name": "",
            "batch_id": "", "input_url": "", "result_url": result_url,
            "crack_count": res["crack_count"], "boxes": res["boxes"],
            "image_size": res["image_size"], "conf_threshold": conf_v,
            "cost": res["cost"], "detection_time": detection_time,
            "status": "done", "error": "",
        })
        LOG.info("detect 完成：%s 裂缝 %d 处，结果 %s",
                 filename, res["crack_count"], result_url)
        return {"crack_count": res["crack_count"], "boxes": res["boxes"],
                "result_url": result_url, "record_id": record_id,
                "detection_time": detection_time, "error": ""}
    except Exception as e:
        err = str(e)[:200]
        history.add_record({
            "record_id": record_id, "filename": filename, "stored_name": "",
            "batch_id": "", "input_url": "", "result_url": "",
            "crack_count": None, "boxes": [], "image_size": "",
            "conf_threshold": conf_v, "cost": None,
            "detection_time": detection_time, "status": "failed", "error": err,
        })
        LOG.warning("detect 失败：%s -> %s", filename, err)
        return {"crack_count": 0, "boxes": [], "result_url": "", "record_id": "",
                "detection_time": detection_time, "error": err}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
