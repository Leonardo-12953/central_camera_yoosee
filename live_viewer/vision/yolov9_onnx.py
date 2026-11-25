"""Detector YOLOv9-t via OpenCV DNN.

A arquitetura/postprocessamento segue o que o projeto de referência (ClearCam)
faz com YOLOv9: letterbox -> inferência -> confiança por classe -> NMS por
classe -> caixas devolvidas no espaço da imagem original.

A diferença é prática: em vez de tinygrad + pesos .safetensors, usamos o mesmo
modelo exportado em ONNX e a API de DNN do OpenCV, que já é dependência do
nosso viewer e roda em CPU sem compilador externo.

Saída de ``detect``: array ``(N, 6)`` com
``[x1, y1, x2, y2, confiança, id_da_classe]``.
"""
from __future__ import annotations

import logging
import shutil
import time
import urllib.request
from pathlib import Path

LOGGER = logging.getLogger("viewer.vision")

MODEL_URL = (
    "https://huggingface.co/Kalray/yolov9/resolve/main/yolov9t.onnx"
)


class ModelUnavailable(RuntimeError):
    """O modelo não pôde ser obtido ou carregado."""


def _download(url: str, destination: Path, logger: logging.Logger) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    logger.info("[visão] baixando modelo YOLOv9-t de %s", url)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "central-cameras/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, temp.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        if temp.stat().st_size < 1024 * 1024:
            raise ModelUnavailable("arquivo baixado é pequeno demais para ser um modelo")
        temp.replace(destination)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def letterbox(image, size: int):
    """Redimensiona mantendo proporção e aplica padding (YOLO letterbox)."""
    import cv2

    height, width = image.shape[:2]
    gain = min(size / height, size / width)
    new_w = max(1, int(round(width * gain)))
    new_h = max(1, int(round(height * gain)))
    pad_x = (size - new_w) / 2
    pad_y = (size - new_h) / 2
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    padded = cv2.copyMakeBorder(
        resized,
        int(round(pad_y - 0.1)), int(round(pad_y + 0.1)),
        int(round(pad_x - 0.1)), int(round(pad_x + 0.1)),
        cv2.BORDER_CONSTANT, value=(114, 114, 114),
    )
    if padded.shape[:2] != (size, size):
        padded = cv2.resize(padded, (size, size), interpolation=cv2.INTER_LINEAR)
    return padded, gain, pad_x, pad_y


class YOLOv9Detector:
    def __init__(self, model_path: Path, *, input_size: int = 640,
                 confidence: float = 0.25, iou: float = 0.45,
                 classes: set[int] | None = None, prefer_cuda: bool = False,
                 model_url: str = MODEL_URL,
                 logger: logging.Logger | None = None):
        import cv2
        import numpy as np

        self._cv2 = cv2
        self._np = np
        self.logger = logger or LOGGER
        self.input_size = max(160, int(input_size))
        self.confidence = float(confidence)
        self.iou = float(iou)
        self.classes = classes
        self.path = Path(model_path)

        if not self.path.is_file():
            self.logger.info("[visão] modelo não encontrado em %s", self.path)
            _download(model_url, self.path, self.logger)

        self.net = cv2.dnn.readNetFromONNX(str(self.path))
        if prefer_cuda:
            try:
                self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
                self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
                self.logger.info("[visão] DNN usando CUDA")
            except Exception:  # noqa: BLE001 - nem todo OpenCV traz backend CUDA
                self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                self.logger.warning("[visão] CUDA indisponível; usando CPU")
        # CPU é o padrão do OpenCV. Não chamamos setPreferableTarget() aqui:
        # builds recentes avisam que "targets are not supported" e o aviso
        # apareceria em todas as câmeras sem mudar nada no resultado.
        self.logger.info("[visão] modelo carregado: %s (%dx%d)", self.path.name,
                         self.input_size, self.input_size)

    def detect(self, frame):
        """Detecta objetos em um frame BGR; devolve ``(N, 6)`` em float32."""
        np = self._np
        cv2 = self._cv2
        empty = np.empty((0, 6), dtype=np.float32)
        if frame is None or getattr(frame, "size", 0) == 0:
            return empty

        padded, gain, pad_x, pad_y = letterbox(frame, self.input_size)
        blob = cv2.dnn.blobFromImage(
            padded, scalefactor=1.0 / 255.0, size=(self.input_size, self.input_size),
            mean=(0, 0, 0), swapRB=True, crop=False,
        )
        self.net.setInput(blob)
        output = self.net.forward()
        if isinstance(output, (list, tuple)):
            output = output[0]

        # ONNX do YOLOv9: (1, 84, 8400). Algumas exportações vêm transpostas.
        # Queremos sempre (1, 8400, 84) para indexar raw[:, 0] etc.
        if output.ndim == 3 and output.shape[1] < output.shape[2]:
            output = np.transpose(output, (0, 2, 1))
        raw = output[0]  # (N, 4 + classes)
        if raw.shape[1] < 5:
            return empty

        cx, cy, bw, bh = raw[:, 0], raw[:, 1], raw[:, 2], raw[:, 3]
        class_scores = raw[:, 4:]
        class_ids = np.argmax(class_scores, axis=1).astype(np.float32)
        confidences = np.max(class_scores, axis=1).astype(np.float32)

        keep = confidences >= self.confidence
        if self.classes:
            allowed = np.fromiter(
                (cid in self.classes for cid in class_ids.astype(int)),
                dtype=bool, count=len(class_ids),
            )
            keep &= allowed
        if not np.any(keep):
            return empty

        cx, cy, bw, bh = cx[keep], cy[keep], bw[keep], bh[keep]
        confidences = confidences[keep]
        class_ids = class_ids[keep]

        x1 = (cx - bw / 2 - pad_x) / gain
        y1 = (cy - bh / 2 - pad_y) / gain
        x2 = (cx + bw / 2 - pad_x) / gain
        y2 = (cy + bh / 2 - pad_y) / gain
        height, width = frame.shape[:2]
        np.clip(x1, 0, width, out=x1)
        np.clip(y1, 0, height, out=y1)
        np.clip(x2, 0, width, out=x2)
        np.clip(y2, 0, height, out=y2)

        # NMS por classe: duas pessoas se sobrepondo competem entre si, mas
        # uma pessoa dentro de um carro não apaga o carro.
        selected: list[int] = []
        for class_id in np.unique(class_ids):
            indexes = np.flatnonzero(class_ids == class_id)
            boxes = [
                [float(x1[i]), float(y1[i]), float(x2[i] - x1[i]), float(y2[i] - y1[i])]
                for i in indexes
            ]
            scores = [float(confidences[i]) for i in indexes]
            try:
                picked = cv2.dnn.NMSBoxes(boxes, scores, self.confidence, self.iou)
            except Exception:  # noqa: BLE001 - versões antigas devolvem tuplas diferentes
                picked = []
            if len(picked) == 0:
                continue
            picked = np.asarray(picked).reshape(-1)
            selected.extend(int(indexes[i]) for i in picked)

        if not selected:
            return empty
        selected.sort(key=lambda i: float(confidences[i]), reverse=True)
        return np.stack([
            x1[selected], y1[selected], x2[selected], y2[selected],
            confidences[selected], class_ids[selected],
        ], axis=1).astype(np.float32)
