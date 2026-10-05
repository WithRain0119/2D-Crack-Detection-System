# backend/logger.py — 日志系统初始化与全局 LOG（控制台 + 日志文件双输出）
import logging
import os
import sys
from datetime import datetime

import config

# 全项目统一 logger，各模块 logging.getLogger("crack") 使用
LOG = logging.getLogger("crack")

# 本次进程启动的活动日志路径（logs/clear 清理时跳过它）
_active_log_path = ""


def setup_logging() -> str:
    """创建 log/ 目录并为本次启动新建一个日志文件，返回该文件路径。"""
    global _active_log_path
    os.makedirs(config.LOG_DIR, exist_ok=True)
    # 文件名用全角冒号 + 秒（半角冒号在 Windows 文件名里不合规，且要与批次ID同格式）
    log_name = datetime.now().strftime(config.TIME_FORMAT) + ".log"
    log_path = os.path.join(config.LOG_DIR, log_name)
    _active_log_path = log_path

    formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    LOG.setLevel(logging.DEBUG)
    LOG.propagate = False
    # 文件必须 utf-8：Windows 默认 gbk 写中文日志会坏
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        LOG.addHandler(handler)

    LOG.info("日志文件创建：%s", log_path)
    LOG.info("日志目录：%s", os.path.abspath(config.LOG_DIR))
    return log_path


def active_log_path() -> str:
    """返回本次启动的活动日志路径。"""
    return _active_log_path
