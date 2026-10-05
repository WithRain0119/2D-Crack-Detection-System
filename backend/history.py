# backend/history.py — SQLite 检测历史（backend/history.db，成功与失败都入库）
import json
import logging
import sqlite3

import config

LOG = logging.getLogger("crack")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    stored_name TEXT DEFAULT '',
    batch_id TEXT DEFAULT '',
    input_url TEXT DEFAULT '',
    result_url TEXT DEFAULT '',
    crack_count INTEGER,
    boxes TEXT DEFAULT '[]',
    image_size TEXT DEFAULT '',
    conf_threshold REAL,
    cost REAL,
    detection_time TEXT,
    status TEXT DEFAULT 'done',
    error TEXT DEFAULT ''
)
"""

_COLUMNS = ("record_id", "filename", "stored_name", "batch_id", "input_url", "result_url",
            "crack_count", "boxes", "image_size", "conf_threshold", "cost",
            "detection_time", "status", "error")


def init_db() -> None:
    """建表（幂等）并打印已有历史条数。"""
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute(_SCHEMA)
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    LOG.info("SQLite 建表完成：%s，已有历史记录 %d 条", config.DB_PATH, count)


def add_record(rec: dict) -> None:
    """插入一条检测记录；每次独立连接（自动提交，任意线程可调用）。"""
    row = {
        "record_id": rec.get("record_id", ""),
        "filename": rec.get("filename", ""),
        "stored_name": rec.get("stored_name", ""),
        "batch_id": rec.get("batch_id", ""),
        "input_url": rec.get("input_url", ""),
        "result_url": rec.get("result_url", ""),
        "crack_count": rec.get("crack_count"),
        "boxes": json.dumps(rec.get("boxes") or [], ensure_ascii=False),
        "image_size": rec.get("image_size", ""),
        "conf_threshold": rec.get("conf_threshold"),
        "cost": rec.get("cost"),
        "detection_time": rec.get("detection_time", ""),
        "status": rec.get("status", "done"),
        "error": rec.get("error", ""),
    }
    sql = (f"INSERT INTO records ({', '.join(_COLUMNS)}) "
           f"VALUES ({', '.join('?' for _ in _COLUMNS)})")
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute(sql, [row[c] for c in _COLUMNS])
    LOG.info("入库 %s：%s 状态=%s 裂缝=%s 批次=%s",
             row["record_id"], row["filename"], row["status"],
             row["crack_count"], row["batch_id"] or "-")


def query_by_batch(batch_id: str) -> list:
    """返回指定批次的全部记录（插入序），字段同 query_all；启动恢复时用。"""
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM records WHERE batch_id = ? ORDER BY rowid", (batch_id,)
        ).fetchall()
    records = []
    for row in rows:
        records.append({
            "record_id": row["record_id"],
            "filename": row["filename"],
            "stored_name": row["stored_name"],
            "crack_count": row["crack_count"],
            "conf_threshold": row["conf_threshold"],
            "detection_time": row["detection_time"],
            "result_url": row["result_url"],
            "image_size": row["image_size"],
            "boxes": json.loads(row["boxes"] or "[]"),
            "cost": row["cost"],
            "batch_id": row["batch_id"],
            "input_url": row["input_url"],
            "status": row["status"],
            "error": row["error"],
        })
    return records


def query_all() -> list:
    """返回全部记录（插入序 = 旧->新）；boxes 还原为数组，带 status/error。"""
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM records ORDER BY rowid").fetchall()
    records = []
    for row in rows:
        item = {
            "record_id": row["record_id"],
            "filename": row["filename"],
            "crack_count": row["crack_count"],
            "conf_threshold": row["conf_threshold"],
            "detection_time": row["detection_time"],
            "result_url": row["result_url"],
            "image_size": row["image_size"],
            "boxes": json.loads(row["boxes"] or "[]"),
        }
        # /detect 产生的记录没有 batch_id/input_url 键（契约 3.7）
        if row["batch_id"]:
            item["batch_id"] = row["batch_id"]
            item["input_url"] = row["input_url"]
        item["status"] = row["status"]
        item["error"] = row["error"]
        records.append(item)
    LOG.info("查询检测记录：%d 条", len(records))
    return records
