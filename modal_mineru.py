"""Modal-based MinerU PDF parser with custom DPI support.

Prerequisites:
  pip install modal
  modal setup

Usage:
  modal run modal_mineru.py --pdf inbox/paper.pdf
  modal run modal_mineru.py --pdf inbox/paper.pdf --dpi 400 --no-cache
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import modal

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

MINERU_MODEL_SOURCE = os.environ.get("MINERU_MODEL_SOURCE", "huggingface")


class MinerUResult:
    """Same interface as generate.py's MinerUResult."""

    def __init__(self, work_dir: Path, is_cached: bool = False):
        self.work_dir = work_dir
        self.is_cached = is_cached
        self.full_md = work_dir / "full.md"
        self.images_dir = work_dir / "images"
        self.content_list = work_dir / "content_list.json"


# --------------------------------------------------------------------------- #
# Modal image — bake mineru + VLM models in at build time (zero ongoing cost)
# --------------------------------------------------------------------------- #

_base = "nvidia/cuda:12.8.0-devel-ubuntu22.04"
image = (
    modal.Image.from_registry(_base, add_python="3.12")
    .apt_install("libgl1", "libglib2.0-0", "fonts-noto-cjk", "fontconfig",
                 "build-essential")
    .run_commands("fc-cache -fv")
    .pip_install("vllm==0.21.0")
    .pip_install("mineru[core]>=3.4.0")
    .env({"MINERU_MODEL_SOURCE": MINERU_MODEL_SOURCE})
    .run_commands(f"mineru-models-download -s {MINERU_MODEL_SOURCE} -m vlm")
)

app = modal.App("mineru-parse")


# --------------------------------------------------------------------------- #
# Remote function — executes on Modal T4 GPU
# --------------------------------------------------------------------------- #

@app.function(image=image, gpu="L4", timeout=900)
def parse_pdf_remote(pdf_bytes: bytes, dpi: int = 200,
                     backend: str = "vlm-engine") -> bytes:
    """Run mineru + monkey-patch DPI, return zip of output directory."""
    import mineru.utils.pdf_image_tools as pit

    # Patch DPI in the installed source so ALL subprocesses use it
    src_path = Path(pit.__file__)
    src = src_path.read_text(encoding="utf-8")
    src = src.replace("DEFAULT_PDF_IMAGE_DPI = 200",
                      f"DEFAULT_PDF_IMAGE_DPI = {dpi}")
    src_path.write_text(src, encoding="utf-8")

    os.environ.setdefault("MINERU_MODEL_SOURCE", "local")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pdf_path = tmp / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)
        out_dir = tmp / "out"
        out_dir.mkdir()

        subprocess.run(
            ["mineru", "-p", str(pdf_path), "-o", str(out_dir),
             "-b", backend],
            check=True, timeout=900,
        )

        # MinerU nests output as out/<stem>/<backend>/<stem>.md — flatten
        md_files = list(out_dir.rglob("*.md"))
        if not md_files:
            raise RuntimeError(f"No .md output found in {out_dir}")
        vlm_dir = md_files[0].parent

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(vlm_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(vlm_dir))
        return buf.getvalue()


# --------------------------------------------------------------------------- #
# Local cache + dispatch — same interface as generate.py's mineru_parse()
# --------------------------------------------------------------------------- #

def parse(pdf_path: Path, *, use_cache: bool = True, dpi: int = 200,
          backend: str = "vlm-engine") -> MinerUResult:
    """Parse a PDF via Modal mineru.

    Mirrors the interface of ``generate.py``:mineru_parse() so the two are
    drop-in swappable via ``MINERU_BACKEND=modal``.
    """
    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    cache_dir = Path(".mineru_cache") / sha
    meta_path = cache_dir / "meta.json"

    if use_cache and (cache_dir / "full.md").exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("dpi") == dpi and meta.get("backend") == backend:
                print(f"[modal-mineru] cache hit ({sha[:12]}) for {pdf_path.name}")
                return MinerUResult(cache_dir, is_cached=True)
        except Exception:
            pass

    print(f"[modal-mineru] calling remote (dpi={dpi}, backend={backend}) ...")
    with app.run():
        zip_bytes = parse_pdf_remote.remote(
            pdf_path.read_bytes(), dpi=dpi, backend=backend,
        )

    cache_dir.mkdir(parents=True, exist_ok=True)
    for p in cache_dir.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(cache_dir)

    # mineru names files after the input file; normalize to expected names
    input_md = cache_dir / "input.md"
    full_md = cache_dir / "full.md"
    if input_md.exists() and not full_md.exists():
        shutil.move(str(input_md), str(full_md))

    input_cl = cache_dir / "input_content_list.json"
    full_cl = cache_dir / "content_list.json"
    if input_cl.exists() and not full_cl.exists():
        shutil.move(str(input_cl), str(full_cl))

    input_cl_v2 = cache_dir / "input_content_list_v2.json"
    full_cl_v2 = cache_dir / "content_list_v2.json"
    if input_cl_v2.exists() and not full_cl_v2.exists():
        shutil.move(str(input_cl_v2), str(full_cl_v2))

    meta = {
        "sha": sha,
        "pdf_name": pdf_path.name,
        "size": pdf_path.stat().st_size,
        "dpi": dpi,
        "backend": backend,
        "created_at": time.time(),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[modal-mineru] cached -> .mineru_cache/{sha[:12]}/ (dpi={dpi})")
    return MinerUResult(cache_dir, is_cached=True)


# --------------------------------------------------------------------------- #
# Standalone CLI for testing
# --------------------------------------------------------------------------- #

@app.local_entrypoint()
def main(pdf: str, dpi: int = 300, no_cache: bool = False):
    pdf_path = Path(pdf)
    if not pdf_path.exists():
        print(f"error: {pdf} not found")
        return
    result = parse(pdf_path, use_cache=not no_cache, dpi=dpi)
    text = result.full_md.read_text(encoding="utf-8")
    print(f"\nfull.md ({len(text)} chars):\n{'-' * 50}")
    print(text[:1500])
    print("..." if len(text) > 1500 else "")
