"""Recortes de rosto e de placa usados nas faixas laterais do player.

OpenCV/numpy continuam sendo importados tarde: as funções aqui só entram em
ação quando a detecção está ativa. O modelo de rosto (YuNet) é baixado uma vez
para `live_viewer/models/`; a placa usa uma heurística local, sem modelo extra.
"""
from __future__ import annotations

import logging
import os
import shutil
import urllib.request
from pathlib import Path

DEFAULT_FACE_MODEL = "face_detection_yunet_2023mar.onnx"
DEFAULT_FACE_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


def ensure_model(path: Path, url: str, logger: logging.Logger) -> Path:
    """Garante que o ONNX do YuNet exista, baixando-o apenas uma vez."""
    path = Path(path)
    if path.is_file() and path.stat().st_size > 1000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    logger.info("[visão] baixando modelo de rosto de %s", url)
    try:
        with urllib.request.urlopen(url, timeout=45) as response, tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if tmp.stat().st_size < 1000:
            raise RuntimeError("arquivo baixado está vazio")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return path


class FaceCropper:
    """Detects faces with YuNet and devolves caixas no espaço do frame."""

    def __init__(self, model_path: Path, model_url: str,
                 confidence: float, logger: logging.Logger):
        self.logger = logger
        self.detector = None
        self.error = ""
        try:
            import cv2

            resolved = ensure_model(Path(model_path), model_url, logger)
            self.detector = cv2.FaceDetectorYN.create(
                str(resolved), "", (320, 320), float(confidence), 0.3, 5000
            )
        except Exception as exc:  # noqa: BLE001 - sem rosto, o resto continua
            self.error = str(exc)
            logger.warning("[visão] detector de rosto indisponível (%s)", exc)

    def detect(self, frame):
        """Lista de (x, y, w, h, score) para os rostos encontrados."""
        if self.detector is None:
            return []
        height, width = frame.shape[:2]
        if width < 32 or height < 32:
            return []
        try:
            self.detector.setInputSize((width, height))
            _, faces = self.detector.detect(frame)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("[visão] falha ao detectar rosto: %s", exc)
            return []
        if faces is None:
            return []
        found = []
        for face in faces:
            try:
                x, y, w, h = (float(face[i]) for i in range(4))
                score = float(face[14])
            except (IndexError, TypeError, ValueError):
                continue
            if w > 4 and h > 4:
                found.append((x, y, w, h, score))
        return found


def crop_with_padding(frame, box, pad_x: float = 0.25, pad_y: float = 0.30):
    """Recorta `box` com uma margem proporcional; devolve None se vazio."""
    import numpy as np

    x, y, w, h = (float(value) for value in box[:4])
    if w <= 1 or h <= 1:
        return None
    px, py = w * pad_x, h * pad_y
    height, width = frame.shape[:2]
    x1 = max(0, int(x - px))
    y1 = max(0, int(y - py))
    x2 = min(width, int(x + w + px))
    y2 = min(height, int(y + h + py))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    return crop if crop.size else None


def resize_keep_aspect(image, size=(160, 200)):
    """Redimensiona sem distorcer, preenchendo o resto com preto."""
    import cv2
    import numpy as np

    if image is None or not getattr(image, "size", 0):
        return None
    target_w, target_h = size
    height, width = image.shape[:2]
    scale = min(target_w / width, target_h / height)
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    out = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    out[y:y + new_h, x:x + new_w] = resized
    return out


def find_plate(frame, box):
    """Tenta localizar uma placa dentro da caixa de um veículo.

    Heurística leve: contornos horizontais na metade inferior do veículo,
    proporção de placa e presença de caracteres. Não faz OCR — o recorte é
    guardado para a faixa lateral e para uso futuro.
    """
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    x, y, w, h = (float(value) for value in box[:4])
    x1, y1 = max(0, int(x)), max(0, int(y))
    x2, y2 = min(width, int(x + w)), min(height, int(y + h))
    if x2 - x1 < 24 or y2 - y1 < 20:
        return None
    crop = frame[y1:y2, x1:x2]
    if not crop.size:
        return None

    ch, cw = crop.shape[:2]
    scale = max(1.0, 180.0 / cw, 110.0 / ch)
    if scale > 1.01:
        crop = cv2.resize(crop, (int(cw * scale), int(ch * scale)),
                          interpolation=cv2.INTER_CUBIC)
    ch, cw = crop.shape[:2]

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    gray = cv2.bilateralFilter(gray, 5, 45, 45)
    edges = cv2.Canny(gray, 55, 155)
    kernel_w = max(9, cw // 18)
    if kernel_w % 2 == 0:
        kernel_w += 1
    closed = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 3)),
    )
    closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]

    candidates = []
    for contour in contours:
        rx, ry, rw, rh = cv2.boundingRect(contour)
        area = float(rw * rh)
        if rw < max(26, cw * 0.16) or rh < max(9, ch * 0.05):
            continue
        ratio = rw / float(rh)
        if not 1.8 <= ratio <= 8.0:
            continue
        if area < 0.003 * cw * ch or area > 0.42 * cw * ch:
            continue
        center_y = (ry + rh / 2.0) / ch
        if center_y < 0.42 or center_y > 0.95:
            continue

        roi = gray[ry:ry + rh, rx:rx + rw]
        if roi.size == 0:
            continue
        block = (min(rw, rh) // 2) * 2 + 1
        if block < 11:
            continue
        block = min(block, 31)
        if block % 2 == 0:
            block -= 1
        binary = cv2.adaptiveThreshold(
            roi, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, block, 7,
        )
        chars = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        glyphs = 0
        for char in chars:
            cx, _, cw_ch, ch_ch = cv2.boundingRect(char)
            if (4 <= cw_ch <= rw * 0.35 and 5 <= ch_ch <= rh * 0.9
                    and cv2.contourArea(char) >= 5):
                glyphs += 1
        if glyphs < 2:
            continue

        score = (glyphs * 10
                 - abs(ratio - 3.2) * 9
                 - abs(center_y - 0.76) * 14
                 + (area / (cw * ch)) * 25)
        candidates.append((score, rx, ry, rw, rh))

    if not candidates:
        return None
    _, rx, ry, rw, rh = max(candidates, key=lambda item: item[0])
    pad_x = max(2, int(rh * 0.30))
    pad_y = max(2, int(rh * 0.40))
    px1, py1 = max(0, rx - pad_x), max(0, ry - pad_y)
    px2, py2 = min(cw, rx + rw + pad_x), min(ch, ry + rh + pad_y)
    plate = crop[py1:py2, px1:px2]
    return resize_keep_aspect(plate, (240, 80))
