# backend/main.py — 启动序列编排入口（必须 cd 到项目根目录后运行）
import logging
import os
import sys

import uvicorn

import batches
import config
import history
import yolo
from lock import acquire_lock, check_cwd
from logger import setup_logging
from routes import create_app

LOG = logging.getLogger("crack")


def main() -> None:
    # 1) cwd 自检：先于一切建目录动作，失败直接退出
    check_cwd()

    # 2) 日志系统：本次启动新建 log/<全角冒号秒级>.log
    setup_logging()
    LOG.core("cwd 检查通过，工作目录：%s", os.getcwd())

    # 3) 单实例锁：失败打印"已有实例正在运行"并退出（进程退出时 atexit 自动释放锁）
    if not acquire_lock():
        sys.exit(1)

    # 4) 静态目录
    os.makedirs(config.STATIC_DIR, exist_ok=True)
    LOG.info("static 目录就绪：%s", os.path.abspath(config.STATIC_DIR))

    # 5) SQLite 建表与历史条数
    history.init_db()

    # 6) 模型加载（失败不崩，健康检查报 model_loaded=false）
    yolo.load_model()

    # 7) 启动扫描：从磁盘重建批次索引（不补写 SQLite）
    batches.restore_batches()

    # 8) FastAPI + uvicorn（无 reload：会双加载模型并破坏单实例锁）
    app = create_app()
    LOG.core("uvicorn 开始监听 http://%s:%s", config.HOST, config.PORT)
    uvicorn.run(app, host=config.HOST, port=config.PORT, access_log=False)


if __name__ == "__main__":
    main()
