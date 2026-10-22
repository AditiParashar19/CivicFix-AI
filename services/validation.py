"""Input validation for text fields and uploaded images. Uploaded bytes are only ever checked
and written to disk as inert data files; they are never executed or rendered as HTML."""
import re
from pathlib import Path
from typing import Optional

import config

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(value, multiline: bool = False) -> str:
    """Strip control characters and collapse whitespace. Newlines survive only when ``multiline``."""
    text = _CONTROL.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    return " ".join(text.split())


def validate_report_input(description: str, location: str) -> list[str]:
    """Return a list of friendly error messages (empty list = valid)."""
    errors = []
    d, loc = (description or "").strip(), (location or "").strip()
    if len(d) < config.DESCRIPTION_MIN:
        errors.append(f"Please describe the issue in at least {config.DESCRIPTION_MIN} characters "
                      "(what is wrong, and how it affects people).")
    if len(d) > config.DESCRIPTION_MAX:
        errors.append(f"The description is too long (max {config.DESCRIPTION_MAX} characters).")
    if len(loc) < config.LOCATION_MIN:
        errors.append("Please enter a location (street, landmark or area).")
    if len(loc) > config.LOCATION_MAX:
        errors.append(f"The location is too long (max {config.LOCATION_MAX} characters).")
    return errors


def _magic_ok(ext: str, data: bytes) -> bool:
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def validate_image(filename: str, data: bytes) -> list[str]:
    """Check extension, size and file signature (magic bytes), not just the file name."""
    errors = []
    ext = Path(filename or "").suffix.lower()
    if ext not in config.ALLOWED_IMAGE_EXTENSIONS:
        errors.append("Unsupported image type. Please upload a PNG, JPG or WEBP file.")
        return errors
    if len(data) == 0:
        errors.append("The uploaded image is empty.")
    elif len(data) > config.MAX_IMAGE_MB * 1024 * 1024:
        errors.append(f"The image is larger than {config.MAX_IMAGE_MB} MB. Please upload a smaller file.")
    elif not _magic_ok(ext, data):
        errors.append("The file contents do not match its image type, so it was rejected.")
    return errors


def save_image(complaint_id: str, filename: str, data: bytes, upload_dir: Optional[Path] = None) -> str:
    """Save under a server-generated name (never the user's file name). Returns a path relative to ROOT."""
    ext = Path(filename or "").suffix.lower()
    ext = ".jpg" if ext == ".jpeg" else ext
    if ext not in (".png", ".jpg", ".webp") or not re.fullmatch(r"CF-\d{8}-[0-9A-F]{4}", complaint_id or ""):
        raise ValueError("Refusing to store the file: unsupported type or invalid complaint ID.")
    target_dir = Path(upload_dir or config.UPLOAD_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{complaint_id}{ext}"
    target.write_bytes(data)
    try:
        return str(target.relative_to(config.ROOT))
    except ValueError:
        return str(target)
