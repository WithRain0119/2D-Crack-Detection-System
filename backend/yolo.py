# backend/yolo.py — YOLO 模型加载与单张图片推理（强制 CPU）
import logging
import os
import time

from PIL import Image
from ultralytics import YOLO

import config

LOG = logging.getLogger("crack")

# 全局模型实例；加载失败保持 None（健康检查报 model_loaded=false，不崩服务）
MODEL = None


def load_model() -> None:
    """同步加载一次模型（main 在 uvicorn 启动前调用）。"""
    global MODEL
    LOG.info("开始加载模型：%s", config.MODEL_PATH)
    t0 = time.time()
    try:
        MODEL = YOLO(config.MODEL_PATH)
    except Exception:
        LOG.exception("模型加载失败：%s", config.MODEL_PATH)
        MODEL = None
        return
    LOG.info("模型加载完成，耗时 %.2f 秒，类别：%s", time.time() - t0, MODEL.names)


def detect_one(img_path: str, out_path: str, conf) -> dict:
    """识别单张图片：结果图恒存 PNG（已画框），异常上抛给调用方处理。"""
    if MODEL is None:
        raise RuntimeError("模型未加载")
    t0 = time.time()

    # 预检：坏图/假图片（文本改名 .png）直接抛异常，不让 predict 静默吞掉
    with Image.open(img_path) as im:
        im.verify()

    r = MODEL.predict(source=img_path, conf=float(conf), device="cpu",
                      verbose=config.DEBUG_VERBOSE, save=False)[0]

    # 提取框：像素坐标 + conf 四舍五入整数，class 取自模型 names
    boxes = []
    if r.boxes is not None and len(r.boxes) > 0:
        names = MODEL.names
        for i, (xyxy, c, cls) in enumerate(zip(r.boxes.xyxy.tolist(),
                                               r.boxes.conf.tolist(),
                                               r.boxes.cls.tolist()), start=1):
            x1, y1, x2, y2 = (int(round(v)) for v in xyxy)
            boxes.append({"id": i, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                          "conf": round(float(c), 4), "class": names[int(cls)]})

    image_size = f"{r.orig_shape[1]}x{r.orig_shape[0]}"  # 宽x高

    # 结果图用 PIL 保存：cv2.imwrite 对非 ASCII 路径（全角冒号/中文）在 Windows 上
    # 会静默返回 False；r.plot() 返回 BGR，转 RGB 后存 PNG。无裂缝也原图直出。
    plot = r.plot()
    Image.fromarray(plot[:, :, ::-1]).save(out_path)
    if not os.path.exists(out_path):
        raise RuntimeError("结果图保存失败：" + out_path)

    cost = round(time.time() - t0, 2)
    LOG.info("推理完成：%s 尺寸=%s conf=%s 框数=%d 耗时=%.2f秒",
             os.path.basename(img_path), image_size, conf, len(boxes), cost)
    return {"boxes": boxes, "image_size": image_size, "crack_count": len(boxes), "cost": cost}
