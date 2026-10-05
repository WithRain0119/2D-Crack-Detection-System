# backend/lock.py — 启动 cwd 自检与单实例锁（原子抢锁 + ctypes 探活）
import atexit
import ctypes
import os
import sys
import logging

import config

LOG = logging.getLogger("crack")

_STILL_ACTIVE = 259                        # Windows GetExitCodeProcess 的“仍在运行”
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def check_cwd() -> None:
    """必须从项目根目录启动：cwd 下要有 backend/ 目录与 version2.pt，否则立即退出。"""
    if not (os.path.isdir("backend") and os.path.isfile(config.MODEL_PATH)):
        print("请从项目根目录启动：cd 到项目根目录后执行 python backend/main.py")
        print(f"当前工作目录：{os.getcwd()}")
        sys.exit(1)


def _pid_alive(pid: int) -> bool:
    """ctypes 探活进程。禁止 os.kill(pid, 0)——Windows 上会真的杀掉进程。"""
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False  # 打不开通常 = 进程不存在
    try:
        code = ctypes.c_ulong()
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == _STILL_ACTIVE
        return True  # 拿不到退出码时保守当作存活，不删锁
    finally:
        k32.CloseHandle(handle)


def _read_lock_pid() -> int:
    try:
        with open(config.LOCK_PATH, "r", encoding="utf-8") as fp:
            return int(fp.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _release_lock(expected_pid: int) -> None:
    """进程正常退出时删除自己的锁（内容仍是自己的 pid 才删，避免误删他人锁）。"""
    try:
        if _read_lock_pid() == expected_pid:
            os.remove(config.LOCK_PATH)
            LOG.info("已释放单实例锁（pid=%s）", expected_pid)
    except OSError as e:
        LOG.warning("释放单实例锁失败：%s", e)


def acquire_lock() -> bool:
    """原子抢占 backend/running.lock；成功注册退出自动释放，失败返回 False。"""
    pid = os.getpid()
    for _ in range(2):  # 首次 + 残留锁清理后重试一次
        try:
            fd = os.open(config.LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            other = _read_lock_pid()
            if other and _pid_alive(other):
                LOG.warning("已有实例正在运行（pid=%s，锁文件 %s）", other, config.LOCK_PATH)
                return False
            # 死亡进程留下的残留锁：删除后重试一次
            LOG.warning("发现残留锁文件（pid=%s 已退出），删除后重试", other)
            try:
                os.remove(config.LOCK_PATH)
            except OSError as e:
                LOG.warning("删除残留锁失败：%s", e)
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(str(pid))
        atexit.register(_release_lock, pid)
        LOG.info("单实例锁获取成功：%s（pid=%s）", config.LOCK_PATH, pid)
        return True
    LOG.warning("单实例锁获取失败")
    return False
