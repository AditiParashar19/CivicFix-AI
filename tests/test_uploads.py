"""Upload safety: extension, signature, size, filenames and storage paths."""
from pathlib import Path

import pytest

import config
from services.validation import normalize_text, save_image, validate_image

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50
JPG = b"\xff\xd8\xff\xe0" + b"0" * 50
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 50


@pytest.mark.parametrize("name,data", [("a.png", PNG), ("a.jpg", JPG), ("a.JPEG", JPG), ("a.webp", WEBP)])
def test_valid_images_pass(name, data):
    assert validate_image(name, data) == []


@pytest.mark.parametrize("name", ["a.exe", "a.svg", "a.gif", "a.php", "a.png.exe", "noext", "", ".png.", "a.html"])
def test_invalid_extensions_rejected(name):
    assert validate_image(name, PNG)


@pytest.mark.parametrize("name,data", [("a.png", JPG), ("a.jpg", PNG), ("a.webp", PNG), ("a.png", b"<script>"),
                                       ("a.png", b"MZ\x90\x00"), ("a.jpg", b"GIF89a")])
def test_wrong_file_signature_rejected(name, data):
    assert any("do not match" in e for e in validate_image(name, data))


def test_oversized_and_empty_rejected():
    assert any("larger" in e for e in validate_image("a.png", PNG + b"0" * (config.MAX_IMAGE_MB * 1024 * 1024)))
    assert validate_image("a.png", PNG + b"0" * (config.MAX_IMAGE_MB * 1024 * 1024 - len(PNG))) == []   # exactly 5 MB ok
    assert validate_image("a.png", b"")


def test_filename_is_never_used_and_path_stays_inside_upload_dir(tmp_path):
    out = save_image("CF-20261019-3FA9", "../../etc/passwd.png", PNG, upload_dir=tmp_path / "up")
    saved = (tmp_path / "up" / "CF-20261019-3FA9.png")
    assert saved.is_file() and Path(out).name == "CF-20261019-3FA9.png" and saved.read_bytes() == PNG
    assert not (tmp_path / "etc").exists()


@pytest.mark.parametrize("cid", ["../evil", "CF-1", "", "CF-20261019-3FA9/../x", "cf-20261019-3fa9"])
def test_unsafe_complaint_ids_cannot_build_a_path(tmp_path, cid):
    with pytest.raises(ValueError):
        save_image(cid, "a.png", PNG, upload_dir=tmp_path)


def test_unsupported_extension_cannot_be_saved(tmp_path):
    with pytest.raises(ValueError):
        save_image("CF-20261019-3FA9", "shell.php", PNG, upload_dir=tmp_path)


def test_malformed_text_input_is_normalised():
    assert normalize_text(None) == "" and normalize_text("a\x00b\x07c") == "abc"
    assert normalize_text(" a \n b ") == "a b"
    assert normalize_text("a\r\n\r\n\r\n\r\nb", multiline=True) == "a\n\nb"
