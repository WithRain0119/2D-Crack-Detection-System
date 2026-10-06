# backend/config.py — 全局配置常量（唯一定义处，所有模块从这里取值）
import os
import re

# ---------- 监听与地址 ----------
HOST = "127.0.0.1"
PORT = 8000
BASE_URL = "http://127.0.0.1:8000"

# ---------- 路径（全部相对 cwd，必须从项目根目录启动） ----------
MODEL_PATH = "models/version2.pt"   # 相对项目根目录；健康检查原样返回此字符串
STATIC_DIR = "static"
LOG_DIR = "log"
LOCK_PATH = "backend/running.lock"
DB_PATH = "backend/history.db"

# ---------- 时间格式（全角冒号 U+FF1A + 秒）：日志文件名 = 批次ID = batch_id ----------
# Windows 文件名不能用半角冒号，本项目统一全角冒号，前端拼目录名时按此理解
TIME_FORMAT = "%Y-%m-%d_%H：%M：%S"
# 记录字段 created_at / detection_time（契约 3.5/3.6 为半角格式）
CREATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"
# 启动扫描时识别批次目录（全角冒号 + 秒）
BATCH_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}：\d{2}：\d{2}$")

# ---------- 业务规则 ----------
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MAX_BATCH_FILES = 500
DEFAULT_CONF = 0.05

# YOLO predict 详细日志开关（环境变量 YOLO_LOG_VERBOSE，0/false/no 关闭，默认开）
DEBUG_VERBOSE = os.environ.get("YOLO_LOG_VERBOSE", "1").lower() not in {"0", "false", "no"}
