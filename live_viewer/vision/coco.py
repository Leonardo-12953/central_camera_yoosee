"""Rótulos COCO usados pelo YOLO (a mesma convenção do projeto de referência)."""

COCO_LABELS: tuple[str, ...] = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
)

# Classes de interesse de uma câmera de segurança (mesmo conjunto usado como
# padrão pelo ClearCam): pessoas, bicicletas, carros, motos, ônibus e caminhões.
DEFAULT_CLASSES: tuple[int, ...] = (0, 1, 2, 3, 5, 7)


def label(class_id: int) -> str:
    try:
        return COCO_LABELS[int(class_id)]
    except (IndexError, ValueError, TypeError):
        return f"classe-{class_id}"
