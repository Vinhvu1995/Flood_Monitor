"""OCR the burned-in description of one image per camera.

Grayscale + Tesseract ``--psm 6`` over the camera images, writing
camera_metadata.csv; the curated final is camera_metadata_complete.csv
with columns Camera_ID, Description_Text, Image_URL, HTML_URL.

Note: grayscale via PIL/numpy (no OpenCV in this project's deps).

Usage: python camera_metadata.py --images <dir-or-found_cameras.json> [--out camera_metadata.csv]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import requests
from PIL import Image

TESSERACT_PSM = "6"


def ocr_description(image_bytes: bytes) -> str:
    """Grayscale the image and read its burned-in text with Tesseract PSM 6."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        name = tmp.name
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            grey = np.asarray(img.convert("L"))
            Image.fromarray(grey).save(name)
        out = subprocess.run(
            ["tesseract", name, "stdout", "--psm", TESSERACT_PSM],
            capture_output=True, text=True, timeout=60,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""
    finally:
        Path(name).unlink(missing_ok=True)


def iter_inputs(images_arg: str) -> list[dict]:
    """Accept found_cameras.json or a directory of <id>.jpg files."""
    p = Path(images_arg)
    if p.suffix == ".json":
        return json.loads(p.read_text())
    return [
        {"id": f.stem, "imageUrl": None, "viewUrl": "", "_file": str(f)}
        for f in sorted(p.glob("*.jpg"))
    ]


def fetch_bytes(entry: dict) -> bytes | None:
    if entry.get("_file"):
        return Path(entry["_file"]).read_bytes()
    try:
        r = requests.get(entry["imageUrl"], timeout=15)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--images", required=True)
    p.add_argument("--out", default="camera_metadata.csv")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["Camera_ID", "Description_Text",
                            "Image_URL", "HTML_URL"])
        w.writeheader()
        for entry in iter_inputs(args.images):
            blob = fetch_bytes(entry)
            text = ocr_description(blob) if blob else ""
            w.writerow({"Camera_ID": entry["id"],
                        "Description_Text": text,
                        "Image_URL": entry.get("imageUrl", ""),
                        "HTML_URL": entry.get("viewUrl", "")})
            print(f"{entry['id']}: {text[:60]}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
