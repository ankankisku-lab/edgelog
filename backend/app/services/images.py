import re
import uuid
from pathlib import Path

import cv2
import numpy as np

from ..config import get_settings
from ..models import TradeImage

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def safe_name(name: str) -> str:
    stem = re.sub(r"[^\w.\- ]+", "_", Path(name).name).strip() or "image"
    return stem[:150]


def save_upload(trade_id: int, file_name: str, data: bytes) -> Path:
    folder = get_settings().uploads_dir / str(trade_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex[:8]}_{safe_name(file_name)}"
    path.write_bytes(data)
    return path


def decode(data: bytes) -> np.ndarray | None:
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return arr


def image_bytes(img: TradeImage) -> bytes:
    """Return the image's bytes, downloading from Drive into the local cache on first use."""
    if img.local_path and Path(img.local_path).exists():
        return Path(img.local_path).read_bytes()
    if img.drive_file_id:
        from .drive import download_file

        data = download_file(img.drive_file_id)
        ext = Path(img.file_name).suffix.lower() if Path(img.file_name).suffix.lower() in IMAGE_EXTS else ".img"
        path = get_settings().cache_dir / f"{img.drive_file_id}{ext}"
        path.write_bytes(data)
        img.local_path = str(path)
        return data
    raise FileNotFoundError(f"image {img.id} has no local file or Drive ID")


def load_image(img: TradeImage) -> np.ndarray:
    arr = decode(image_bytes(img))
    if arr is None:
        raise ValueError(f"{img.file_name} is not a readable image")
    return arr
