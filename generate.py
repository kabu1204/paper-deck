#!/usr/bin/env python3
"""generate.py — paper.pdf -> canonical index.qmd for the paper-sharing site.

Pipeline (per PRD F1):
  1. MinerU Precision (vlm) parses PDF -> full markdown + cropped figures + content_list.json
  2. Build a figure catalog (filename, caption, surrounding context) from content_list.json
  3. LLM call (OpenAI-compatible Chat Completions OR Google Gemini; auto-selected by
     which API key is set) -> structured metadata + article body markdown
  4. Python assembles papers/<date>-<slug>/index.qmd (pyyaml frontmatter + body)

Usage:
  python generate.py                       # process inbox/*.pdf
  python generate.py --regenerate papers/<folder>/   # re-run from paper.pdf in that folder, keep date
  python generate.py --preview             # launch quarto preview after generation
  python generate.py --force               # overwrite existing slug folder in inbox mode
  python generate.py --refresh-cache       # ignore MinerU local cache; re-parse + update it

MinerU parse results are cached under .mineru_cache/<sha256>/ so re-runs (including
--regenerate) skip the slow/expensive MinerU API call. The cache is invalidated
automatically when MINERU_MODEL or MINERU_LANGUAGE change.

LLM backend (experimental): Google Gemini is used when GOOGLE_API_KEY is set,
otherwise the OpenAI-compatible endpoint. Set LLM_BACKEND=openai|google to
override. The OpenAI backend reads OPENAI_BASE_URL + OPENAI_API_KEY + MODEL; the
Google backend reads GOOGLE_API_BASE_URL + GOOGLE_API_KEY + GOOGLE_API_MODEL.
Gemini models are natively multimodal, so vision (attaching figures as images)
is auto-enabled for the google backend.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path

import requests
import yaml
from openai import OpenAI

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
EXAMPLES_DIR = ROOT / "examples"
PAPERS_DIR = ROOT / "papers"
INBOX_DIR = ROOT / "inbox"

MINERU_BASE = "https://mineru.net/api/v4"
MINERU_MODEL = "vlm"          # precision mode (non-flash)
MINERU_LANGUAGE = "en"
MINERU_CACHE_DIR = ROOT / ".mineru_cache"
POLL_INTERVAL = 6             # seconds
POLL_TIMEOUT = 1800           # 30 min max
VISION_FIG_CAP = 20           # max figures sent as images when vision enabled
QUARTO_BIN = os.environ.get("QUARTO_BIN") or shutil.which("quarto") or \
    str(Path.home() / ".local/opt/quarto-1.9.38/bin/quarto")


def die(msg: str) -> "NoReturn":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_env(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE lines); real os.environ wins over file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env(ENV_FILE)

MINERU_TOKEN = os.environ.get("MINERU_API_TOKEN", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or None
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("MODEL", "")
SUPPORTS_VISION = os.environ.get("MODEL_SUPPORTS_VISION", "false").lower() == "true"
SUPPORTS_STRICT_SCHEMA = os.environ.get("MODEL_SUPPORTS_STRICT_JSON_SCHEMA", "false").lower() == "true"
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_API_BASE_URL = os.environ.get("GOOGLE_API_BASE_URL") or None
GOOGLE_API_MODEL = os.environ.get("GOOGLE_API_MODEL", "")

# LLM backend selection: an explicit LLM_BACKEND (openai|google) wins; otherwise
# auto-detect by which API key is present. Gemini models are natively multimodal,
# so vision (attaching figures as images) is auto-enabled for the google backend.
BACKEND = (os.environ.get("LLM_BACKEND") or "").strip().lower()
if BACKEND and BACKEND not in ("openai", "google"):
    die(f"LLM_BACKEND must be 'openai' or 'google', got {BACKEND!r}")
if not BACKEND:
    BACKEND = "google" if GOOGLE_API_KEY else "openai"
EFFECTIVE_VISION = SUPPORTS_VISION or (BACKEND == "google")


def check_env() -> None:
    required = [("MINERU_API_TOKEN", MINERU_TOKEN)]
    if BACKEND == "google":
        required += [("GOOGLE_API_KEY", GOOGLE_API_KEY), ("GOOGLE_API_MODEL", GOOGLE_API_MODEL)]
    else:
        required += [("OPENAI_API_KEY", OPENAI_API_KEY), ("MODEL", MODEL)]
    missing = [k for k, v in required if not v]
    if missing:
        die(f"missing env vars: {', '.join(missing)} (set in .env)")


# --------------------------------------------------------------------------- #
# MinerU Online API (precision / vlm mode)
# --------------------------------------------------------------------------- #

class MinerUResult:
    def __init__(self, work_dir: Path, is_cached: bool = False):
        self.work_dir = work_dir
        self.is_cached = is_cached
        self.full_md = work_dir / "full.md"
        self.images_dir = work_dir / "images"
        self.content_list = work_dir / "content_list.json"


def sha256_of_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()


def mineru_parse(pdf_path: Path, *, use_cache: bool = True) -> MinerUResult:
    """Upload pdf, poll until done, download + unzip result. Returns parsed artifacts.

    A local on-disk cache (.mineru_cache/<sha>/) avoids re-parsing the same PDF.
    Set use_cache=False to force a fresh MinerU call; the cache is still updated
    with the new result. Cache entries are invalidated automatically when
    MINERU_MODEL or MINERU_LANGUAGE change.
    """
    sha = sha256_of_file(pdf_path)
    cache_dir = MINERU_CACHE_DIR / sha
    meta_path = cache_dir / "meta.json"

    if use_cache and (cache_dir / "full.md").exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if (meta.get("model_version") == MINERU_MODEL
                    and meta.get("language") == MINERU_LANGUAGE):
                print(f"[mineru] cache hit ({sha[:12]}) for {pdf_path.name}")
                return MinerUResult(cache_dir, is_cached=True)
            print("[mineru] cache entry stale (model/language changed); re-parsing")
        except Exception as e:
            print(f"[mineru] cache meta unreadable ({e}); re-parsing")

    if not MINERU_TOKEN:
        die("MINERU_API_TOKEN not set")
    headers = {"Authorization": f"Bearer {MINERU_TOKEN}"}
    file_name = pdf_path.name
    data_id = pdf_path.stem
    print(f"[mineru] requesting upload url for {file_name} ...")
    res = requests.post(
        f"{MINERU_BASE}/file-urls/batch",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "files": [{"name": file_name, "data_id": data_id}],
            "model_version": MINERU_MODEL,
            "enable_formula": True,
            "enable_table": True,
            "language": MINERU_LANGUAGE,
        },
        timeout=60,
    )
    res.raise_for_status()
    body = res.json()
    if body.get("code") != 0:
        die(f"mineru /file-urls/batch failed: {body}")
    batch_id = body["data"]["batch_id"]
    upload_url = body["data"]["file_urls"][0]

    print(f"[mineru] uploading {pdf_path.stat().st_size // 1024} KB ...")
    # NOTE: presigned OSS URLs are self-authenticating via query params; sending a
    # Content-Type header that wasn't part of the signature yields 403. Upload raw
    # bytes with no Content-Type.
    put = requests.put(upload_url, data=pdf_path.read_bytes(), timeout=300)
    if not put.ok:
        die(f"mineru upload failed ({put.status_code}): {put.text[:500]}")

    print(f"[mineru] parsing (batch_id={batch_id}), polling every {POLL_INTERVAL}s ...")
    deadline = time.time() + POLL_TIMEOUT
    last_state = ""
    while time.time() < deadline:
        r = requests.get(f"{MINERU_BASE}/extract-results/batch/{batch_id}", headers=headers, timeout=60)
        r.raise_for_status()
        rb = r.json()
        if rb.get("code") != 0:
            die(f"mineru poll failed: {rb}")
        results = rb["data"].get("extract_result", [])
        if not results:
            time.sleep(POLL_INTERVAL); continue
        item = results[0]
        state = item.get("state", "")
        if state != last_state:
            print(f"[mineru] state: {state}")
            last_state = state
        if state == "done":
            zip_url = item.get("full_zip_url")
            if not zip_url:
                die("mineru done but no full_zip_url")
            tmp = _mineru_download(zip_url)
            return _persist_to_cache(tmp, cache_dir, meta_path, sha, pdf_path)
        if state == "failed":
            die(f"mineru failed: {item.get('err_msg', 'unknown')}")
        time.sleep(POLL_INTERVAL)
    die("mineru timed out")


def _persist_to_cache(tmp: MinerUResult, cache_dir: Path, meta_path: Path,
                      sha: str, pdf_path: Path) -> MinerUResult:
    """Move a freshly-downloaded temp result into the on-disk cache and return a
    MinerUResult pointing at the cache dir (so the caller must not delete it)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    # clear any stale/partial contents first
    for p in cache_dir.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    for p in tmp.work_dir.iterdir():
        shutil.move(str(p), str(cache_dir / p.name))
    shutil.rmtree(tmp.work_dir, ignore_errors=True)
    meta = {
        "sha": sha,
        "pdf_name": pdf_path.name,
        "size": pdf_path.stat().st_size,
        "model_version": MINERU_MODEL,
        "language": MINERU_LANGUAGE,
        "created_at": time.time(),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[mineru] cached -> .mineru_cache/{sha[:12]}/")
    return MinerUResult(cache_dir, is_cached=True)


def _mineru_download(zip_url: str) -> MinerUResult:
    work = Path(tempfile.mkdtemp(prefix="mineru_"))
    print(f"[mineru] downloading result zip ...")
    r = requests.get(zip_url, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        zf.extractall(work)
    # MinerU zip may nest files under a subdir; normalize.
    if not (work / "full.md").exists():
        nested = [p for p in work.rglob("full.md")]
        if nested:
            sub = nested[0].parent
            for p in sub.iterdir():
                shutil.move(str(p), str(work / p.name))
            shutil.rmtree(sub, ignore_errors=True)
    if not (work / "full.md").exists():
        die(f"mineru zip missing full.md; contents: {sorted(p.name for p in work.iterdir())}")
    print(f"[mineru] parsed -> {work} (full.md, images/, content_list.json)")
    return MinerUResult(work)


# --------------------------------------------------------------------------- #
# Figure catalog
# --------------------------------------------------------------------------- #

class Figure:
    def __init__(self, n: int, orig_path: Path, caption: str, context: str):
        self.n = n
        self.orig_path = orig_path
        self.caption = caption
        self.context = context
        self.filename = f"fig{n}.png"


def build_figure_catalog(mr: MinerUResult) -> list[Figure]:
    """Scan content_list.json for image blocks; collect caption + nearby context."""
    figures: list[Figure] = []
    blocks: list = []
    if mr.content_list.exists():
        try:
            blocks = json.loads(mr.content_list.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] could not parse content_list.json: {e}")
    if not isinstance(blocks, list):
        blocks = []

    def text_of(block: dict) -> str:
        for key in ("text", "img_caption", "caption"):
            val = block.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, list):
                joined = " ".join(
                    (s.get("text", "") if isinstance(s, dict) else str(s)) for s in val
                ).strip()
                if joined:
                    return joined
        return ""

    for i, block in enumerate(blocks):
        btype = str(block.get("type", "")).lower()
        if btype != "image":
            continue
        img_rel = (block.get("img_path") or block.get("img_url")
                   or block.get("img_name") or block.get("path") or "")
        if not img_rel:
            continue
        # resolve image file inside the unzipped work dir
        candidates = [mr.work_dir / img_rel,
                      mr.images_dir / Path(img_rel).name,
                      mr.work_dir / Path(img_rel).name]
        orig_path = next((c for c in candidates if c.exists()), None)
        if orig_path is None:
            print(f"[warn] image file not found for block {i}: {img_rel}")
            continue
        caption = text_of(block)
        if not caption:
            for j in range(i + 1, min(len(blocks), i + 3)):
                if "caption" in str(blocks[j].get("type", "")).lower():
                    caption = text_of(blocks[j]); break
        context = ""
        for j in range(i - 1, max(-1, i - 4), -1):
            if str(blocks[j].get("type", "")).lower() == "text":
                context = text_of(blocks[j])
                if context:
                    break
        figures.append(Figure(len(figures) + 1, orig_path, caption, context))

    if not figures and mr.images_dir.exists():
        # fallback: no content_list structure recognized; take all images in order
        for p in sorted(mr.images_dir.iterdir()):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                figures.append(Figure(len(figures) + 1, p, "", ""))
    print(f"[figs] cataloged {len(figures)} figures")
    return figures


def catalog_as_text(figs: list[Figure]) -> str:
    lines = []
    for f in figs:
        cap = f.caption or "(no caption)"
        ctx = f.context or "(no surrounding context)"
        lines.append(f"- fig{f.n}.png | caption: {cap} | context: {ctx}")
    return "\n".join(lines)


def copy_used_figures(figs: list[Figure], used_ns: set[int], assets_dir: Path) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    by_n = {f.n: f for f in figs}
    copied = []
    for n in sorted(used_ns):
        f = by_n.get(n)
        if not f or not f.orig_path.exists():
            print(f"[warn] figure fig{n}.png referenced but not available; skipping")
            continue
        dst = assets_dir / f.filename
        try:
            if f.orig_path.suffix.lower() == ".png":
                shutil.copy2(f.orig_path, dst)
            else:
                from PIL import Image
                with Image.open(f.orig_path) as im:
                    im.convert("RGB").save(dst, "PNG")
            copied.append(f.filename)
        except Exception as e:
            print(f"[warn] failed to copy fig{n}.png: {e}")
    print(f"[figs] copied {len(copied)} used figures -> {assets_dir}")


def figure_as_data_uri(f: Figure, max_dim: int = 1024) -> str:
    from PIL import Image
    with Image.open(f.orig_path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #

_STYLE_RULES = """You are an expert at turning academic papers into presentation-style \
articles for a personal paper-sharing website. You are given a paper's full text (MinerU-extracted \
markdown), a catalog of its figures (filename, caption, surrounding context), and 4 style-reference \
articles written by the site owner. Mimic the owner's style: structure, density, bullet conciseness, \
and figure usage.

CANONICAL ARTICLE STYLE (must follow exactly):
- Exactly three H1 sections, in this order and with these exact titles:
    # Background & Motivation
    # Design
    # Evaluation
- Use `## H2` for subsections (NOT slides). ~20-30 H2 subsections total, matching the density of the \
style references. Do NOT number headings.
- NO `---` separators. NO `\\centering` (it is a LaTeX command, invalid in HTML articles).
- Figures: `![](assets/figN.png){width=70% fig-align=center}` — always include `fig-align=center`. \
Choose width per figure (25%-80%). Reference figures by the exact filenames in the catalog \
(assets/fig1.png, assets/fig2.png, ...). Only reference figures that exist in the catalog.
- BLANK LINES AROUND FIGURES (critical): every `![](...)` MUST be a standalone block with a blank \
line before AND after it. Never place a figure on the same paragraph as bullets or text. Always put \
a blank line after a `## H2` heading before any figure or bullet, and a blank line between a figure \
and a following bullet list. Correct:
      ## Heading\n\n![](assets/fig1.png){width=70% fig-align=center}\n\n- bullet one\n- bullet two
  WRONG (do not do this — figure merges with bullets and breaks rendering):
      ## Heading\n![](assets/fig1.png){width=70% fig-align=center}\n- bullet one\n- bullet two
- Bullets are concise (one line each where possible). No speaker notes.
- The article body is raw Quarto markdown (H1/H2 + bullets + figure refs). Do NOT include YAML \
frontmatter in the body — metadata fields are provided separately.
"""

_METADATA_FIELDS = """METADATA FIELDS (provided via the tool call / JSON object — NOT in the body):
- slug: url-safe lowercase-hyphen, short, descriptive (e.g. "h2o", "flashinfer", "thunderkittens")
- title: full paper title (may contain a colon; escape as needed for JSON)
- subtitle: publication venue + year, displayed as a venue tag. Determine it from (in order): \
(1) the paper's first-page header/footer, "Proceedings of ...", or conference/journal name if it \
appears in the text; (2) your own knowledge of the paper if it is a well-known publication. Use a \
concise form like "NeurIPS 2023", "FAST '25", "ASPLOS 2024", "arXiv 2024". Use empty string "" ONLY \
if you cannot determine any venue with reasonable confidence. Do NOT put the date here (date is set \
by the script to the current date).
- institute: parenthesized affiliation list (e.g. "(UW, NVIDIA, CMU)")
- author: comma-separated author names
- categories: array of 3-6 lowercase short tags
- description: ONE sentence, <=80 characters, the paper's core contribution
- background: 2-3 paragraphs of web-only prose (separate paragraphs with \\n\\n) giving context/motivation \
that frames the article; this is NOT part of the article body
- hero_figure: one filename from the catalog (e.g. "fig3.png") to use as the listing card image
"""

# --- Output mode: tool call (primary) ---
SYSTEM_PROMPT_TOOLS = _STYLE_RULES + _METADATA_FIELDS + """
OUTPUT INSTRUCTIONS (tool-call mode):
1. Call the `set_paper_metadata` function with all the metadata fields above.
2. Write the FULL article body as your message content — raw Quarto markdown following the canonical \
style. The body is NOT part of the function call; it goes in the message content as plain text. \
Do NOT wrap the body in JSON, code fences, or any enclosure. Do NOT repeat the metadata in the body.
"""

# --- Output mode: delimiter (fallback for models without tool support) ---
SYSTEM_PROMPT_DELIMITERS = _STYLE_RULES + _METADATA_FIELDS + """
OUTPUT INSTRUCTIONS (delimiter mode):
Output exactly two sections separated by markers:
1. A line containing only <<<METADATA>>> followed by a JSON object with the metadata fields above.
2. A line containing only <<<BODY>>> followed by the full article body in raw Quarto markdown.
Do NOT wrap the body in JSON, code fences, or any enclosure.
"""

METADATA_REQUIRED = ["slug", "title", "subtitle", "institute", "author", "categories",
                     "description", "background", "hero_figure"]

METADATA_TOOL = {
    "type": "function",
    "function": {
        "name": "set_paper_metadata",
        "description": "Set the paper article's metadata fields (everything except the article body, "
                       "which goes in the message content).",
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "institute": {"type": "string"},
                "author": {"type": "string"},
                "categories": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
                "background": {"type": "string"},
                "hero_figure": {"type": "string"},
            },
            "required": METADATA_REQUIRED,
            "additionalProperties": False,
        },
    },
}


def load_examples() -> list[tuple[str, str]]:
    out = []
    for name in ("h2o", "impress", "geminifs", "scalexfs"):
        p = EXAMPLES_DIR / f"{name}.qmd"
        if p.exists():
            out.append((name, p.read_text(encoding="utf-8")))
        else:
            print(f"[warn] missing example {p}")
    return out


def build_user_text(paper_md: str, figs: list[Figure], examples: list[tuple[str, str]]) -> str:
    ex_block = "\n\n".join(f"=== Example: {name} ===\n{content}" for name, content in examples)
    return (
        "PAPER FULL TEXT (MinerU markdown):\n\n"
        f"{paper_md}\n\n"
        "FIGURE CATALOG (reference these exact filenames in the article body):\n"
        f"{catalog_as_text(figs)}\n\n"
        "STYLE REFERENCES (mimic structure/density/figure usage; do NOT copy their content):\n\n"
        f"{ex_block}\n\n"
        "TASK: Per the system instructions, provide the metadata (via tool call or JSON) "
        "AND write the full article body as your message content. "
        "Reference figures from the catalog using assets/figN.png. "
        "Pick the most representative figure as hero_figure."
    )


def build_user_message(paper_md: str, figs: list[Figure], examples: list[tuple[str, str]]):
    """Returns the user message dict (content is str or multipart list for vision).
    The system prompt is NOT included — it is injected by call_llm based on approach."""
    user_text = build_user_text(paper_md, figs, examples)
    if EFFECTIVE_VISION and figs:
        content: list = [{"type": "text", "text": user_text}]
        for f in figs[:VISION_FIG_CAP]:
            try:
                content.append({"type": "text", "text": f"Figure {f.filename}:"})
                content.append({"type": "image_url",
                                "image_url": {"url": figure_as_data_uri(f)}})
            except Exception as e:
                print(f"[warn] could not attach {f.filename} as image: {e}")
        if len(figs) > VISION_FIG_CAP:
            content.append({"type": "text",
                            "text": f"(showing first {VISION_FIG_CAP} of {len(figs)} figures; "
                                    "remaining are described in the catalog above)"})
        return {"role": "user", "content": content}
    return {"role": "user", "content": user_text}


# --------------------------------------------------------------------------- #
# LLM call
# --------------------------------------------------------------------------- #

def _extract_json(raw: str) -> str:
    """Recover a JSON object from a possibly-noisy string: strip code fences
    and surrounding prose, then brace-match the outermost object."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    if not s.startswith("{"):
        i = s.find("{")
        if i < 0:
            return s
        j = s.rfind("}")
        if j > i:
            s = s[i:j + 1]
    return s


def _validate_metadata(data: dict) -> None:
    """Raise ValueError if metadata is missing required fields or has wrong types."""
    missing = [k for k in METADATA_REQUIRED if k not in data]
    if missing:
        raise ValueError(f"missing metadata fields: {missing}")
    if not isinstance(data.get("categories"), list):
        raise ValueError("categories must be an array")


def _parse_delimiter_response(content: str) -> tuple[dict, str]:
    """Parse a <<<METADATA>>> ... <<<BODY>>> ... response into (metadata, body)."""
    meta_marker = "<<<METADATA>>>"
    body_marker = "<<<BODY>>>"
    body_idx = content.find(body_marker)
    if body_idx < 0:
        raise ValueError("response missing <<<BODY>>> marker")
    body = content[body_idx + len(body_marker):].strip()
    meta_part = content[:body_idx]
    meta_idx = meta_part.find(meta_marker)
    if meta_idx >= 0:
        meta_part = meta_part[meta_idx + len(meta_marker):]
    metadata = json.loads(_extract_json(meta_part.strip()))
    _validate_metadata(metadata)
    return metadata, body


def call_llm(user_msg: dict) -> tuple[dict, str]:
    """Dispatch to the configured LLM backend. Returns (metadata_dict, body_markdown).

    Backend is auto-selected: Google Gemini if GOOGLE_API_KEY is set, else the
    OpenAI-compatible endpoint. Force via LLM_BACKEND=openai|google if both keys
    are present. Both backends share the dual-message design: a forced tool /
    function call captures metadata, and the message content carries the article
    body; a <<<METADATA>>>/<<<BODY>>> delimiter mode is the fallback."""
    if BACKEND == "google":
        return _call_llm_google(user_msg)
    return _call_llm_openai(user_msg)


def _call_llm_openai(user_msg: dict) -> tuple[dict, str]:
    """OpenAI-compatible Chat Completions backend. See call_llm for the design.

    Primary approach: tool call for metadata + message content for body.
    Fallback (if tools unsupported): <<<METADATA>>> / <<<BODY>>> delimiters.
    The body is always raw markdown — never JSON-encoded."""
    client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
    print(f"[llm] calling {MODEL} via OpenAI-compatible API (vision={EFFECTIVE_VISION}, dual-message) ...")

    # --- Primary: tool-call approach ---
    msgs = [{"role": "system", "content": SYSTEM_PROMPT_TOOLS}, user_msg]
    try:
        metadata, body = _call_with_tools(client, msgs)
        if metadata:
            if not body:
                print("[llm] tool call returned but no content; requesting body...")
                body = _request_body_only(client, msgs, metadata)
            if body:
                _validate_metadata(metadata)
                print("[llm] ok (metadata via tool_call, body via content)")
                return metadata, body
            print("[llm] tool-call approach: metadata ok but body still empty")
    except Exception as e:
        print(f"[llm] tool-call approach failed: {e}")

    # --- Fallback: delimiter approach ---
    print("[llm] falling back to delimiter approach...")
    delim_msgs = [{"role": "system", "content": SYSTEM_PROMPT_DELIMITERS}, user_msg]
    raw = ""
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=delim_msgs,
                max_tokens=12000,
                temperature=0.3,
            )
            raw = resp.choices[0].message.content or ""
            metadata, body = _parse_delimiter_response(raw)
            print(f"[llm] ok (delimiter approach, tokens={resp.usage.total_tokens if resp.usage else '?'})")
            return metadata, body
        except Exception as e:
            print(f"[llm] delimiter attempt {attempt+1} failed: {e}")
            if raw:
                print(f"[llm]   raw len={len(raw)} head={raw[:80]!r} tail={raw[-80:]!r}")
            if attempt == 0:
                delim_msgs = delim_msgs + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Your output was invalid ({e}). "
                     "Re-output with <<<METADATA>>> and <<<BODY>>> markers as instructed."},
                ]
            else:
                die(f"LLM failed after all approaches: {e}")
    die("unreachable")


def _call_with_tools(client: OpenAI, messages: list) -> tuple[dict, str]:
    """Tool-call approach: metadata via forced tool call, body via message content."""
    tool = METADATA_TOOL
    if SUPPORTS_STRICT_SCHEMA:
        tool = {**METADATA_TOOL, "function": {**METADATA_TOOL["function"], "strict": True}}
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "set_paper_metadata"}},
        max_tokens=12000,
        temperature=0.3,
    )
    msg = resp.choices[0].message
    body = (msg.content or "").strip()
    metadata = {}
    if msg.tool_calls:
        raw_args = msg.tool_calls[0].function.arguments
        metadata = json.loads(_extract_json(raw_args))
    else:
        raise ValueError("model did not call the required tool")
    print(f"[llm] tool-call response: tool_calls={len(msg.tool_calls)}, "
          f"content_len={len(body)}, tokens={resp.usage.total_tokens if resp.usage else '?'}")
    return metadata, body


def _request_body_only(client: OpenAI, messages: list, metadata: dict) -> str:
    """Second call: ask the model to write just the article body (metadata already captured)."""
    meta_summary = ", ".join(f"{k}={metadata[k]!r}" for k in ["slug", "title", "subtitle"])
    followup = messages + [
        {"role": "user", "content": f"The metadata has been captured ({meta_summary}). "
         "Now write ONLY the full article body as your message content — raw Quarto markdown "
         "following the canonical style. Do NOT call any tools. Do NOT output JSON."},
    ]
    resp = client.chat.completions.create(
        model=MODEL,
        messages=followup,
        max_tokens=12000,
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip()


# --------------------------------------------------------------------------- #
# Google Gemini backend (experimental)
# --------------------------------------------------------------------------- #
# Lazily imports google-genai so the OpenAI backend never requires it installed.
# Mirrors _call_llm_openai's dual-message design: a forced function call captures
# metadata, the text content carries the article body, with a delimiter fallback.

def _openai_tool_to_gemini(oai_tool: dict) -> dict:
    """Convert an OpenAI-style function tool to a Gemini FunctionDeclaration dict.

    Gemini's schema is near-identical to OpenAI's but wants uppercase type enums
    and the declaration without the {'type':'function','function':{...}} wrapper."""
    fn = oai_tool["function"]
    params = fn.get("parameters", {})

    def norm(t):
        return t.upper() if isinstance(t, str) else t

    props = {}
    for k, v in params.get("properties", {}).items():
        v = dict(v)
        if "type" in v:
            v["type"] = norm(v["type"])
        if v.get("type") == "ARRAY" and isinstance(v.get("items"), dict):
            v["items"] = {**v["items"], "type": norm(v["items"].get("type", "STRING"))}
        props[k] = v
    return {
        "name": fn["name"],
        "description": fn["description"],
        "parameters": {
            "type": norm(params.get("type", "object")),
            "properties": props,
            "required": params.get("required", []),
        },
    }


def _openai_msg_to_gemini_parts(user_msg: dict) -> list:
    """Convert an OpenAI-style user message (str or multipart content list) into
    Gemini Part objects. Image data URIs are decoded to inline_data Blobs."""
    from google.genai import types
    content = user_msg["content"]
    if isinstance(content, str):
        return [types.Part(text=content)]
    parts = []
    for item in content:
        t = item.get("type")
        if t == "text":
            parts.append(types.Part(text=item["text"]))
        elif t == "image_url":
            url = item["image_url"]["url"]
            if url.startswith("data:"):
                header, b64 = url.split(",", 1)
                mime = header.split(":")[1].split(";")[0]
                parts.append(types.Part(inline_data=types.Blob(
                    data=base64.b64decode(b64), mime_type=mime)))
    return parts


def _parse_gemini_response(resp) -> tuple[dict, str]:
    """Extract (metadata_dict, body_text) from a Gemini GenerateContentResponse.

    Walks candidate parts: function_call args become metadata; text parts are
    joined into the article body."""
    metadata: dict = {}
    body_parts: list[str] = []
    for cand in (resp.candidates or []):
        if not cand.content or not cand.content.parts:
            continue
        for part in cand.content.parts:
            if getattr(part, "text", None):
                body_parts.append(part.text)
            elif getattr(part, "function_call", None):
                fc = part.function_call
                if fc.name == "set_paper_metadata":
                    metadata = dict(fc.args or {})
    return metadata, "\n".join(body_parts).strip()


def _request_body_only_google(client, contents: list, metadata: dict) -> str:
    """Second Gemini call: write just the article body (metadata already captured)."""
    from google.genai import types
    meta_summary = ", ".join(f"{k}={metadata[k]!r}" for k in ["slug", "title", "subtitle"])
    followup = contents + [
        types.Content(role="model", parts=[types.Part(text="(metadata captured via function call)")]),
        types.Content(role="user", parts=[types.Part(
            text=f"The metadata has been captured ({meta_summary}). Now write ONLY the full "
                 "article body as your message content — raw Quarto markdown following the "
                 "canonical style. Do NOT call any tools. Do NOT output JSON.")]),
    ]
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT_TOOLS,
        temperature=0.3,
        max_output_tokens=12000,
    )
    resp = client.models.generate_content(model=GOOGLE_API_MODEL, contents=followup, config=cfg)
    return (resp.text or "").strip()


def _call_llm_google(user_msg: dict) -> tuple[dict, str]:
    """Google Gemini backend (experimental). Mirrors _call_llm_openai's dual-message
    design using Gemini function calling + multimodal inline images.

    Reads GOOGLE_API_BASE_URL + GOOGLE_API_KEY + GOOGLE_API_MODEL (not the shared
    MODEL / OPENAI_* vars). Lazily imports google-genai so the OpenAI backend
    never requires it."""
    from google import genai
    from google.genai import types

    http_opts = types.HttpOptions(base_url=GOOGLE_API_BASE_URL) if GOOGLE_API_BASE_URL else None
    client = genai.Client(api_key=GOOGLE_API_KEY, http_options=http_opts)
    print(f"[llm] calling {GOOGLE_API_MODEL} via Google Gemini "
          f"(base_url={GOOGLE_API_BASE_URL or 'default'}, vision={EFFECTIVE_VISION}, dual-message) ...")
    contents = [types.Content(role="user", parts=_openai_msg_to_gemini_parts(user_msg))]

    tool = types.Tool(function_declarations=[_openai_tool_to_gemini(METADATA_TOOL)])
    tool_cfg = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
        mode="ANY", allowed_function_names=["set_paper_metadata"]))
    base_cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT_TOOLS,
        tools=[tool],
        tool_config=tool_cfg,
        temperature=0.3,
        max_output_tokens=12000,
    )

    # --- Primary: forced function-call approach ---
    try:
        resp = client.models.generate_content(model=GOOGLE_API_MODEL, contents=contents, config=base_cfg)
        metadata, body = _parse_gemini_response(resp)
        if metadata:
            if not body:
                print("[llm] google function call returned but no content; requesting body...")
                body = _request_body_only_google(client, contents, metadata)
            if body:
                _validate_metadata(metadata)
                usage = resp.usage_metadata
                print(f"[llm] ok (google: metadata via function_call, body via content, "
                      f"tokens={usage.total_token_count if usage else '?'})")
                return metadata, body
            print("[llm] google function-call approach: metadata ok but body still empty")
    except Exception as e:
        print(f"[llm] google function-call approach failed: {e}")

    # --- Fallback: delimiter approach ---
    print("[llm] google falling back to delimiter approach...")
    delim_cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT_DELIMITERS,
        temperature=0.3,
        max_output_tokens=12000,
    )
    raw = ""
    cur_contents = contents
    for attempt in range(2):
        try:
            resp = client.models.generate_content(model=GOOGLE_API_MODEL, contents=cur_contents, config=delim_cfg)
            raw = resp.text or ""
            metadata, body = _parse_delimiter_response(raw)
            usage = resp.usage_metadata
            print(f"[llm] ok (google delimiter, tokens={usage.total_token_count if usage else '?'})")
            return metadata, body
        except Exception as e:
            print(f"[llm] google delimiter attempt {attempt+1} failed: {e}")
            if raw:
                print(f"[llm]   raw len={len(raw)} head={raw[:80]!r} tail={raw[-80:]!r}")
            if attempt == 0:
                cur_contents = cur_contents + [
                    types.Content(role="model", parts=[types.Part(text=raw)]),
                    types.Content(role="user", parts=[types.Part(
                        text=f"Your output was invalid ({e}). Re-output with <<<METADATA>>> "
                             "and <<<BODY>>> markers as instructed.")]),
                ]
            else:
                die(f"Google LLM failed after all approaches: {e}")
    die("unreachable")


# --------------------------------------------------------------------------- #
# .qmd assembly
# --------------------------------------------------------------------------- #

def sanitize_slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "paper"


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _str_presenter)


def assemble_qmd(data: dict, paper_dir: Path, pub_date: str) -> Path:
    slug = sanitize_slug(data.get("slug") or "")
    hero = data.get("hero_figure", "")
    try:
        date_val = date.fromisoformat(pub_date)
    except ValueError:
        date_val = pub_date
    # Venue tag: only include subtitle if the paper states a venue (non-empty),
    # so no empty badge renders on the listing card / article page.
    subtitle = (data.get("subtitle") or "").strip()
    frontmatter = {
        "title": data["title"],
    }
    if subtitle:
        frontmatter["subtitle"] = subtitle
    frontmatter.update({
        "institute": data.get("institute", ""),
        "author": data.get("author", ""),
        "date": date_val,
        "categories": data.get("categories", []),
        "description": data.get("description", ""),
        "image": f"assets/{hero}" if hero else "",
        "background": data.get("background", ""),
        "format": "html",
    })
    fm_yaml = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    body = data.get("body_markdown", "").strip()
    qmd = "---\n" + fm_yaml + "---\n\n" + body + "\n"
    qmd_path = paper_dir / "index.qmd"
    paper_dir.mkdir(parents=True, exist_ok=True)
    qmd_path.write_text(qmd, encoding="utf-8")
    return qmd_path


def used_figure_ns(data: dict) -> set[int]:
    ns: set[int] = set()
    body = data.get("body_markdown", "")
    for m in re.finditer(r"fig(\d+)\.png", body):
        ns.add(int(m.group(1)))
    hero = data.get("hero_figure", "")
    hm = re.match(r"fig(\d+)\.png", hero)
    if hm:
        ns.add(int(hm.group(1)))
    return ns


# --------------------------------------------------------------------------- #
# Outline printer
# --------------------------------------------------------------------------- #

def print_outline(data: dict) -> None:
    print("\n" + "=" * 60)
    print(f"slug       : {data.get('slug')}")
    print(f"title      : {data.get('title')}")
    print(f"subtitle   : {data.get('subtitle')}")
    print(f"hero       : {data.get('hero_figure')}")
    print(f"categories : {data.get('categories')}")
    print(f"description: {data.get('description')}")
    bg = data.get("background", "")
    first_para = bg.split("\n\n")[0] if bg else ""
    print(f"background : {first_para[:200]}{'...' if len(first_para) > 200 else ''}")
    print("-" * 60)
    print("outline:")
    for line in data.get("body_markdown", "").splitlines():
        if line.startswith("# "):
            print(f"  {line}")
        elif line.startswith("## "):
            print(f"      {line}")
    figs_used = sorted(used_figure_ns(data))
    print("-" * 60)
    print(f"figures used: {['fig%d.png' % n for n in figs_used]}")
    print("=" * 60 + "\n")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def find_existing_folder(slug: str) -> Path | None:
    target = sanitize_slug(slug)
    for d in PAPERS_DIR.glob(f"*-{target}"):
        if d.is_dir():
            return d
    return None


def process_pdf(pdf_path: Path, *, regenerate_dir: Path | None,
                force: bool, use_cache: bool = True) -> Path | None:
    print(f"\n>>> processing {pdf_path}")
    mr = mineru_parse(pdf_path, use_cache=use_cache)
    paper_md = mr.full_md.read_text(encoding="utf-8") if mr.full_md.exists() else ""
    if not paper_md:
        die("mineru produced empty full.md")
    figs = build_figure_catalog(mr)
    examples = load_examples()
    user_msg = build_user_message(paper_md, figs, examples)
    metadata, body = call_llm(user_msg)
    data = {**metadata, "body_markdown": body}

    slug = sanitize_slug(data.get("slug", ""))
    if regenerate_dir is not None:
        paper_dir = regenerate_dir
        # keep existing date from current frontmatter
        pub_date = _read_existing_date(paper_dir) or date.today().isoformat()
        # clear old assets
        old_assets = paper_dir / "assets"
        if old_assets.exists():
            shutil.rmtree(old_assets)
    else:
        pub_date = date.today().isoformat()
        existing = find_existing_folder(slug)
        if existing and not force:
            print(f"[skip] papers/{existing.name} already exists (use --force to overwrite)")
            return None
        paper_dir = PAPERS_DIR / f"{pub_date}-{slug}"

    assets_dir = paper_dir / "assets"
    copy_used_figures(figs, used_figure_ns(data), assets_dir)
    qmd_path = assemble_qmd(data, paper_dir, pub_date)
    print(f"[done] wrote {qmd_path}")
    print_outline(data)
    _cleanup(mr)
    return paper_dir


def _read_existing_date(paper_dir: Path) -> str | None:
    qmd = paper_dir / "index.qmd"
    if not qmd.exists():
        return None
    text = qmd.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
        d = fm.get("date")
        return str(d) if d else None
    except Exception:
        return None


def _cleanup(mr: MinerUResult) -> None:
    if mr.is_cached:
        return
    try:
        shutil.rmtree(mr.work_dir, ignore_errors=True)
    except Exception:
        pass


def launch_preview() -> None:
    if not Path(QUARTO_BIN).exists():
        print(f"[preview] quarto not found at {QUARTO_BIN}; skip")
        return
    print("[preview] launching quarto preview (Ctrl+C to stop) ...")
    os.execv(QUARTO_BIN, [QUARTO_BIN, "preview", str(ROOT)])


def main() -> None:
    check_env()
    ap = argparse.ArgumentParser(description="Generate index.qmd from paper.pdf")
    ap.add_argument("--regenerate", metavar="FOLDER",
                    help="re-run from paper.pdf inside an existing papers/<folder>/, keep its date")
    ap.add_argument("--preview", action="store_true", help="launch quarto preview after generation")
    ap.add_argument("--force", action="store_true", help="overwrite existing slug folder (inbox mode)")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="ignore the MinerU local cache; re-parse the PDF and update the cache")
    args = ap.parse_args()
    use_cache = not args.refresh_cache

    if args.regenerate:
        regen = Path(args.regenerate)
        if not regen.is_absolute():
            regen = ROOT / args.regenerate
        if not regen.is_dir():
            die(f"--regenerate folder not found: {regen}")
        pdf = regen / "paper.pdf"
        if not pdf.exists():
            die(f"no paper.pdf in {regen}; place the PDF there first")
        process_pdf(pdf, regenerate_dir=regen, force=False, use_cache=use_cache)
    else:
        INBOX_DIR.mkdir(exist_ok=True)
        pdfs = sorted(INBOX_DIR.glob("*.pdf"))
        if pdfs:
            for pdf in pdfs:
                process_pdf(pdf, regenerate_dir=None, force=args.force, use_cache=use_cache)
        else:
            print(f"no PDFs in {INBOX_DIR}; nothing to generate.")

    if args.preview:
        launch_preview()


if __name__ == "__main__":
    main()
