# backend/batches.py — 批次状态机、批次创建存储、后台识别任务与启动扫描重建
import asyncio
import logging
import os
import shutil
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import config
import history
import yolo

LOG = logging.getLogger("crack")

# 批次索引：batch_id -> 批次对象；_started/_out_names 是内部字段，响应由 summary 白名单过滤
BATCHES: dict = {}

# 全局唯一推理线程池（单 worker，串行访问模型，异步路由不得直接调 MODEL）
EXECUTOR = ThreadPoolExecutor(max_workers=1)


def url_for(rel_path: str) -> str:
    """相对路径 -> 完整 URL（逐段编码，覆盖全角冒号与中文文件名）。"""
    return config.BASE_URL + "/" + "/".join(
        urllib.parse.quote(seg) for seg in rel_path.split("/")
    )


def parse_conf(value) -> float:
    """conf 解析：接受 0.05~0.9 的前端传值，非法值回退默认。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        LOG.warning("conf 非法（%r），回退默认 %s", value, config.DEFAULT_CONF)
        return config.DEFAULT_CONF


def summary(b: dict, kind: str = "run") -> dict:
    """响应白名单：init 含 saved/skipped；run/status 不含；内部字段永不出现。"""
    out = {
        "batch_id": b["batch_id"],
        "status": b["status"],
        "total": b["total"],
        "processed": b["processed"],
        "current": b["current"],
        "conf": b["conf"],
        "input_dir": b["input_dir"],
        "output_dir": b["output_dir"],
        "error": b["error"],
        "files": [dict(f) for f in b["files"]],
    }
    if kind == "init":
        out["saved"] = b["saved"]
        out["skipped"] = b["skipped"]
    return out


def _deduped(stem: str, ext: str, used: set) -> str:
    """同名去重：x.png -> x.png -> x_1.png -> x_2.png（忽略大小写，防 Windows 撞名）。"""
    candidate = stem + ext
    index = 1
    while candidate.lower() in used:
        candidate = f"{stem}_{index}{ext}"
        index += 1
    used.add(candidate.lower())
    return candidate


def _new_batch_id() -> str:
    """按当前时间生成批次ID（全角冒号 + 秒），目录冲突则 +1 秒重试，最多 5 次。"""
    stamp = datetime.now()
    for _ in range(6):
        batch_id = stamp.strftime(config.TIME_FORMAT)
        if not os.path.isdir(os.path.join(config.STATIC_DIR, batch_id)):
            return batch_id
        stamp += timedelta(seconds=1)
    LOG.warning("批次目录连续 6 次冲突，仍使用 %s", batch_id)
    return batch_id


def create_batch(files, conf="0.05", mode="folder") -> dict:
    """创建批次目录并保存上传图片；成功返回 init 摘要，失败返回 {"error", "batch_id"}。"""
    if not files:
        return {"error": "没有收到任何文件", "batch_id": ""}
    if len(files) > config.MAX_BATCH_FILES:
        return {"error": f"一次最多处理 {config.MAX_BATCH_FILES} 张图片", "batch_id": ""}

    LOG.info("init 收到 %d 个文件，conf=%s，mode=%s", len(files), conf, mode)
    conf_v = parse_conf(conf)
    batch_id = _new_batch_id()
    root = os.path.join(config.STATIC_DIR, batch_id)
    input_dir = os.path.join(root, "input")
    output_dir = os.path.join(root, "output")
    os.makedirs(input_dir)
    os.makedirs(output_dir)
    LOG.info("批次目录：static/%s/（input + output）", batch_id)

    saved = 0
    skipped = 0
    used_names: set = set()
    used_stems: set = set()
    out_names: dict = {}
    files_meta: list = []
    for raw_name, data in files:
        name = os.path.basename(str(raw_name or "").replace("\\", "/"))
        stem, ext = os.path.splitext(name)
        if ext.lower() not in config.IMAGE_EXTENSIONS:
            skipped += 1
            LOG.debug("跳过非图片文件：%s", name)
            continue
        stored = _deduped(stem, ext, used_names)
        with open(os.path.join(input_dir, stored), "wb") as fp:
            fp.write(data)
        # 结果图名：stored 去扩展名 + 同 stem 去重序号 + _result.png
        out_stem = _deduped(os.path.splitext(stored)[0], "", used_stems)
        out_names[stored] = out_stem + "_result.png"
        files_meta.append({"name": name, "stored_name": stored, "status": "pending",
                           "crack_count": None, "result_url": "", "boxes": [],
                           "image_size": "", "cost": None, "error": ""})
        saved += 1
        LOG.debug("原图落盘：%s", stored)

    if saved == 0:
        shutil.rmtree(root)  # 无有效图片：不留下空目录
        LOG.info("没有有效图片，已删除空目录 static/%s/", batch_id)
        return {"error": "没有有效的图片文件（仅支持 JPG/PNG/BMP/WEBP/TIF）",
                "batch_id": batch_id}

    LOG.info("批次 %s 创建完成：有效 %d 张，跳过 %d 张（同名自动去重）",
             batch_id, saved, skipped)
    BATCHES[batch_id] = {
        "batch_id": batch_id, "mode": mode, "status": "ready",
        "total": len(files_meta), "processed": 0, "current": None, "conf": conf_v,
        "input_dir": "/".join([config.STATIC_DIR, batch_id, "input"]),
        "output_dir": "/".join([config.STATIC_DIR, batch_id, "output"]),
        "error": "", "files": files_meta, "saved": saved, "skipped": skipped,
        "created_at": datetime.now().strftime(config.CREATED_AT_FORMAT),
        "_started": False, "_out_names": out_names,
    }
    return summary(BATCHES[batch_id], "init")


def start_batch(batch_id: str) -> dict:
    """启动后台识别；事件循环内 check-and-set 天然原子，重复调用不会重复启动。"""
    b = BATCHES.get(batch_id)
    if b is None:
        return {"error": "批次不存在：" + batch_id, "batch_id": batch_id}
    if b["_started"]:
        LOG.warning("批次 %s 已启动过（status=%s），忽略重复 run", batch_id, b["status"])
        return summary(b, "run")
    b["_started"] = True
    b["status"] = "processing"
    LOG.info("批次 %s 后台识别任务启动（共 %d 张，conf=%s）", batch_id, b["total"], b["conf"])
    asyncio.get_running_loop().run_in_executor(EXECUTOR, process_batch, batch_id)
    return summary(b, "run")


def process_batch(batch_id: str) -> None:
    """工作线程执行：逐张识别 -> 落盘结果图 -> 写 SQLite（成败都写）。"""
    b = BATCHES.get(batch_id)
    if b is None:
        LOG.warning("任务启动后批次已消失：%s", batch_id)
        return
    started = time.time()
    LOG.info("批次 %s 开始处理，共 %d 张", batch_id, b["total"])
    try:
        if yolo.MODEL is None:
            LOG.error("批次 %s 中止：模型未加载", batch_id)
            b["status"] = "failed"
            b["error"] = "模型未加载"
            b["current"] = None
            for f in b["files"]:
                if f["status"] == "pending":
                    f["status"] = "failed"
                    f["error"] = "模型未加载"
                    b["processed"] += 1
            LOG.warning("批次 %s 结束：模型未加载，%d 张全部标失败", batch_id, b["processed"])
            return

        for f in b["files"]:
            b["current"] = f["name"]
            f["status"] = "processing"
            stored = f["stored_name"]
            src = os.path.join(b["input_dir"], stored)
            out_name = b["_out_names"].get(stored,
                                           os.path.splitext(stored)[0] + "_result.png")
            out_path = os.path.join(b["output_dir"], out_name)
            LOG.info("[%s] 开始识别：%s", batch_id, stored)
            try:
                res = yolo.detect_one(src, out_path, b["conf"])
                f["status"] = "done"
                f["crack_count"] = res["crack_count"]
                f["boxes"] = res["boxes"]
                f["image_size"] = res["image_size"]
                f["cost"] = res["cost"]
                f["error"] = ""
                f["result_url"] = url_for("/".join([config.STATIC_DIR, batch_id,
                                                     "output", out_name]))
                history.add_record({
                    "record_id": f"record_{os.urandom(4).hex()}",
                    "filename": f["name"], "stored_name": stored,
                    "batch_id": batch_id,
                    "input_url": url_for("/".join([config.STATIC_DIR, batch_id,
                                                   "input", stored])),
                    "result_url": f["result_url"],
                    "crack_count": res["crack_count"], "boxes": res["boxes"],
                    "image_size": res["image_size"], "conf_threshold": b["conf"],
                    "cost": res["cost"],
                    "detection_time": datetime.now().strftime(config.CREATED_AT_FORMAT),
                    "status": "done", "error": "",
                })
                LOG.info("[%s] 完成：%s 裂缝 %d 处，耗时 %.2f 秒，结果 %s",
                         batch_id, stored, res["crack_count"], res["cost"], f["result_url"])
            except Exception as e:
                err = str(e)[:200]
                f["status"] = "failed"
                f["error"] = err
                f["crack_count"] = None
                f["boxes"] = []
                f["image_size"] = ""
                f["cost"] = None
                f["result_url"] = ""
                history.add_record({
                    "record_id": f"record_{os.urandom(4).hex()}",
                    "filename": f["name"], "stored_name": stored,
                    "batch_id": batch_id,
                    "input_url": url_for("/".join([config.STATIC_DIR, batch_id,
                                                   "input", stored])),
                    "result_url": "",
                    "crack_count": None, "boxes": [], "image_size": "",
                    "conf_threshold": b["conf"], "cost": None,
                    "detection_time": datetime.now().strftime(config.CREATED_AT_FORMAT),
                    "status": "failed", "error": err,
                })
                LOG.warning("[%s] 识别失败：%s -> %s", batch_id, f["name"], err)
            finally:
                b["processed"] += 1
                b["current"] = None
    except Exception as e:
        b["status"] = "failed"
        b["error"] = str(e)[:200]
        b["current"] = None
        LOG.exception("批次 %s 处理过程发生灾难异常", batch_id)
        return

    done_n = sum(1 for f in b["files"] if f["status"] == "done")
    if done_n >= 1:
        b["status"] = "done"
        b["error"] = ""
        LOG.info("批次 %s 完成：成功 %d/%d，总耗时 %.1f 秒，输出 %s",
                 batch_id, done_n, b["total"], time.time() - started, b["output_dir"])
    else:
        b["status"] = "failed"
        b["error"] = "全部文件识别失败"
        LOG.warning("批次 %s 失败：全部 %d 张识别失败，总耗时 %.1f 秒",
                    batch_id, b["total"], time.time() - started)


def _created_at(dirname: str) -> str:
    """从目录名解析创建时间（全角冒号转半角后 strptime），失败用目录修改时间。"""
    try:
        dt = datetime.strptime(dirname.replace("：", ":"), "%Y-%m-%d_%H:%M:%S")
    except ValueError:
        LOG.warning("批次目录名无法解析时间：%s，改用目录修改时间", dirname)
        dt = datetime.fromtimestamp(os.path.getmtime(os.path.join(config.STATIC_DIR, dirname)))
    return dt.strftime(config.CREATED_AT_FORMAT)


def restore_batches() -> None:
    """启动扫描 static/*/ 重建批次索引：磁盘结果图 + SQLite 记录综合判定状态。

    批次级规则（按优先级）：
    1. 全部有结果图 -> done（全成功）
    2. 全部处理过（有结果图或有入库记录，含失败张）-> done（正常完成，不误报中断）
    3. 一张都没处理过 -> ready（从未开始，允许重新 run）
    4. 其余（部分处理）-> failed + 服务重启中断提示
    文件明细优先从 SQLite 回填（裂缝数/框/尺寸/耗时），记录缺失时保持契约缺省值。
    """
    if not os.path.isdir(config.STATIC_DIR):
        LOG.info("启动扫描：%s 不存在，无历史批次", config.STATIC_DIR)
        return
    restored = 0
    for name in sorted(os.listdir(config.STATIC_DIR)):
        if not config.BATCH_DIR_RE.match(name):
            continue
        root = os.path.join(config.STATIC_DIR, name)
        input_dir = os.path.join(root, "input")
        output_dir = os.path.join(root, "output")
        if not (os.path.isdir(input_dir) or os.path.isdir(output_dir)):
            continue

        images = []
        if os.path.isdir(input_dir):
            images = [fn for fn in sorted(os.listdir(input_dir))
                      if os.path.splitext(fn)[1].lower() in config.IMAGE_EXTENSIONS]

        # 该批次的历史记录，按存储名索引（小写兼容 Windows 大小写不敏感）
        rec_map = {}
        for r in history.query_by_batch(name):
            rec_map[(r.get("stored_name") or r.get("filename", "")).lower()] = r

        # 重建结果图名映射（与 create_batch 同去重逻辑，处理 a.png/a.jpg 同 stem 冲突）
        out_names: dict = {}
        used_stems: set = set()
        for fn in images:
            out_names[fn] = _deduped(os.path.splitext(fn)[0], "", used_stems) + "_result.png"

        files = []
        result_n = 0   # 有结果图张数
        handled_n = 0  # 处理过张数（有结果图 或 有记录）
        conf = None
        for fn in images:
            rec = rec_map.get(fn.lower())
            out_name = out_names[fn]
            has_result = os.path.isfile(os.path.join(output_dir, out_name))
            if rec and conf is None and rec.get("conf_threshold"):
                conf = rec["conf_threshold"]
            if has_result:
                result_n += 1
                handled_n += 1
                files.append({
                    "name": fn, "stored_name": fn, "status": "done",
                    "crack_count": rec["crack_count"] if rec else None,
                    "result_url": url_for("/".join([config.STATIC_DIR, name,
                                                    "output", out_name])),
                    "boxes": rec["boxes"] if rec else [],
                    "image_size": rec["image_size"] if rec else "",
                    "cost": rec["cost"] if rec else None, "error": ""})
            elif rec is not None:
                # 无结果图但有失败记录：该张确实处理过并失败
                handled_n += 1
                files.append({
                    "name": fn, "stored_name": fn, "status": "failed",
                    "crack_count": None, "result_url": "", "boxes": [],
                    "image_size": "", "cost": None,
                    "error": rec.get("error") or "识别失败"})
            else:
                files.append({
                    "name": fn, "stored_name": fn, "status": "failed",
                    "crack_count": None, "result_url": "", "boxes": [],
                    "image_size": "", "cost": None,
                    "error": "服务重启导致未完成"})

        total = len(images)
        if total > 0 and result_n == total:
            status, error, started = "done", "", True
        elif total > 0 and handled_n == total:
            status, error, started = "done", "", True
        elif handled_n == 0 and total > 0:
            # 从未开始处理（init 后未 run）：恢复为 ready，可重新分析
            status, error, started = "ready", "", False
            files = [{"name": fn, "stored_name": fn, "status": "pending",
                      "crack_count": None, "result_url": "", "boxes": [],
                      "image_size": "", "cost": None, "error": ""}
                     for fn in images]
        else:
            status, error, started = "failed", "服务重启导致批次中断，请重新上传分析", True

        BATCHES[name] = {
            "batch_id": name, "mode": "folder", "status": status,
            "total": total, "processed": handled_n, "current": None,
            "conf": conf if conf is not None else config.DEFAULT_CONF,
            "input_dir": "/".join([config.STATIC_DIR, name, "input"]),
            "output_dir": "/".join([config.STATIC_DIR, name, "output"]),
            "error": error, "files": files, "saved": total, "skipped": 0,
            "created_at": _created_at(name),
            "_started": started, "_out_names": out_names,
        }
        restored += 1
        LOG.info("启动扫描恢复批次 %s：status=%s 处理 %d/%d（结果图 %d，记录 %d），created_at=%s",
                 name, status, handled_n, total, result_n, len(rec_map),
                 BATCHES[name]["created_at"])
    LOG.info("启动扫描完成：共恢复 %d 个批次", restored)
