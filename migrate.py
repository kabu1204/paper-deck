#!/usr/bin/env python3
r"""migrate.py — one-time migration of historical presentation .md files to
canonical papers/<date>-<slug>/index.qmd articles.

For each source directory (e.g. ariadne/, flashattn/):
  1. Parse the .md frontmatter (title, subtitle, institute, author, date)
  2. Use the .md file's mtime as the date if frontmatter has no date
  3. Normalize the body:
     - strip standalone `---` separator lines
     - strip `\centering` lines
     - strip `width=XX%` from figure attributes (keep fig-align=center)
     - ensure all figures have `fig-align=center`
     - rewrite image paths to `assets/<basename>`
     - ensure blank lines around every figure
  4. Copy referenced images to papers/<date>-<slug>/assets/
  5. Generate heuristic metadata (categories, description, hero_figure, background)
  6. Assemble papers/<date>-<slug>/index.qmd

Usage:
  python migrate.py                    # migrate all source dirs
  python migrate.py ariadne flashattn  # migrate specific dirs
  python migrate.py --dry-run          # preview without writing
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
PAPERS_DIR = ROOT / "papers"

# Dirs already migrated or to skip
ALREADY_MIGRATED = {"h2o", "impress", "flashinfer", "llmsys"}


def die(msg: str) -> "NoReturn":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def find_source_dirs() -> list[Path]:
    """Find all source directories with .md files at the top level."""
    dirs = []
    for p in sorted(ROOT.iterdir()):
        if not p.is_dir():
            continue
        if p.name in ("papers", "examples", "docs", "inbox", "_site", "_freeze",
                       "__pycache__", ".github", ".git", ".quarto", ".mineru_cache",
                       "example-papers", "marlin", "os-render"):
            continue
        if p.name in ALREADY_MIGRATED:
            continue
        mds = list(p.glob("*.md"))
        if mds:
            dirs.append(p)
    return dirs


def find_md_file(src_dir: Path) -> Path:
    """Find the .md file in a source dir (prefer one matching dir name)."""
    mds = list(src_dir.glob("*.md"))
    if not mds:
        die(f"no .md file in {src_dir}")
    # prefer the one matching dir name
    for md in mds:
        if md.stem == src_dir.name:
            return md
    return mds[0]


# --------------------------------------------------------------------------- #
# Frontmatter parsing
# --------------------------------------------------------------------------- #

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a .md file into (frontmatter_dict, body_str).
    Handles files with no frontmatter gracefully."""
    m = re.match(r'^---\n(.*?)\n---\n?', text, re.DOTALL)
    if not m:
        return {}, text.strip()
    fm_text = m.group(1)
    body = text[m.end():].strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except Exception as e:
        print(f"[warn] frontmatter parse error: {e}")
        fm = {}
    return fm if isinstance(fm, dict) else {}, body


def clean_title(title: str) -> str:
    """Remove backslash escapes from titles (e.g. Ariadne\\: -> Ariadne:)."""
    if not title:
        return ""
    # remove \: -> :, \& -> &, etc.
    return title.replace("\\:", ":").replace("\\&", "&").replace("\\_", "_")


def parse_date(fm: dict, md_path: Path) -> str:
    """Get date from frontmatter or fall back to file mtime."""
    d = fm.get("date")
    if d:
        ds = str(d).strip()
        # try various formats
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%b %d %Y", "%Y-%m-%d",
                     "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
            try:
                from datetime import datetime
                return datetime.strptime(ds, fmt).date().isoformat()
            except ValueError:
                continue
        # if it's already ISO-like
        if re.match(r'^\d{4}-\d{2}-\d{2}', ds):
            return ds[:10]
        print(f"[warn] unparseable date '{ds}' in {md_path}, using mtime")
    # fall back to mtime
    import datetime
    mtime = os.path.getmtime(md_path)
    return datetime.datetime.fromtimestamp(mtime).date().isoformat()


# --------------------------------------------------------------------------- #
# Body normalization
# --------------------------------------------------------------------------- #

def normalize_body(body: str, src_dir: Path) -> tuple[str, list[str]]:
    """Normalize the markdown body to canonical article style.
    Returns (normalized_body, list_of_referenced_image_filenames in order of first
    appearance)."""
    lines = body.split("\n")
    out: list[str] = []
    referenced_images: list[str] = []
    seen: set[str] = set()

    for line in lines:
        stripped = line.strip()

        # Skip standalone --- separators (but not frontmatter delimiters,
        # which are already stripped)
        if stripped == "---":
            continue

        # Skip \centering lines
        if stripped == "\\centering":
            continue

        # Skip Quarto fenced div syntax (:::, ::: column, ::::: columns, etc.)
        if re.match(r'^:{3,}\s*[\w-]*\s*$', stripped):
            continue

        # Process image lines
        img_match = re.match(
            r'^(\s*)!\[([^\]]*)\]\(([^)]+)\)(\{[^}]*\})?\s*$', line)
        if img_match:
            indent, alt, img_path, attrs = img_match.groups()
            # Normalize the image path to assets/<basename>
            # Decode URL-encoded paths (e.g. Pasted%20image -> Pasted image)
            from urllib.parse import unquote
            basename = Path(unquote(img_path)).name
            assets_path = f"assets/{basename}"
            if basename not in seen:
                referenced_images.append(basename)
                seen.add(basename)

            # Process attributes: strip width, keep/add fig-align=center
            attr_str = ""
            if attrs:
                # Remove the braces
                attr_content = attrs.strip("{}").strip()
                # Remove width=XX%
                attr_content = re.sub(r'width\s*=\s*\d+%\s*', '', attr_content)
                # Keep fig-align if present
                parts = [p.strip() for p in attr_content.split() if p.strip()]
                if "fig-align=center" not in parts:
                    parts.append("fig-align=center")
                attr_str = "{" + " ".join(parts) + "}"
            else:
                attr_str = "{fig-align=center}"

            # Ensure blank line before figure
            if out and out[-1].strip() != "":
                out.append("")

            out.append(f"![{alt}]({assets_path}){attr_str}")

            # Ensure blank line after figure (will be handled by next iteration)
            out.append("")
            continue

        out.append(line)

    # Clean up excessive blank lines (max 2 consecutive)
    cleaned: list[str] = []
    blank_count = 0
    for line in out:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    # Strip leading/trailing blanks
    while cleaned and cleaned[0].strip() == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1].strip() == "":
        cleaned.pop()

    return "\n".join(cleaned), referenced_images


# --------------------------------------------------------------------------- #
# Heuristic metadata
# --------------------------------------------------------------------------- #

def derive_slug(src_dir: Path, title: str) -> str:
    """Derive a URL-safe slug from the dir name or title."""
    s = src_dir.name.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')
    return s or "paper"


def derive_categories(title: str, body: str) -> list[str]:
    """Heuristic: derive 3-5 category tags from title + body keywords."""
    text = (title + " " + body).lower()
    cats = []
    keyword_map = {
        "gpu": ["gpu", "cuda", "kernel", "tensor core", "nvidia"],
        "llm": ["llm", "language model", "transformer", "gpt", "inference"],
        "attention": ["attention", "flash", "kv-cache", "kv cache"],
        "memory": ["memory", "swap", "compression", "defragment", "gmlake"],
        "systems": ["system", "os", "kernel", "scheduling", "storage"],
        "training": ["training", "distributed", "parallel", "gpu cluster"],
        "quantization": ["quant", "quantization", "low-bit", "int8", "int4"],
        "networking": ["network", "rdma", "rpc", "communication", "infiniband"],
        "serving": ["serving", "inference", "latency", "throughput"],
        "compilation": ["compiler", "tvm", "triton", "code generation", "ir"],
        "database": ["database", "db", "query", "transaction", "oltp"],
        "security": ["sgx", "tee", "enclave", "trusted", "confidential"],
        "streaming": ["stream", "video", "live", "dash"],
        "caching": ["cache", "caching", "buffer", "prefetch"],
        "scheduling": ["schedule", "scheduling", "fairness", "preempt"],
        "hardware": ["hardware", "accelerator", "npu", "asic", "fpga"],
        "storage": ["storage", "ssd", "disk", "file system", "zns"],
        "inference": ["inference", "serving", "latency", "batch"],
    }
    for cat, keywords in keyword_map.items():
        if any(kw in text for kw in keywords):
            cats.append(cat)
        if len(cats) >= 5:
            break
    if not cats:
        cats = ["systems"]
    return cats[:5]


def derive_description(title: str) -> str:
    """Truncate title as a rough description (<=80 chars)."""
    d = clean_title(title)
    if len(d) <= 80:
        return d
    # try to cut at a colon
    if ":" in d:
        d = d.split(":")[0].strip()
    if len(d) <= 80:
        return d
    return d[:77] + "..."


def derive_hero_figure(referenced_images: list[str]) -> str:
    """Pick the first referenced image as hero."""
    return referenced_images[0] if referenced_images else ""


def derive_background(title: str, body: str) -> str:
    """Heuristic: take the first paragraph of the body as background."""
    # Find first text paragraph (skip headings and figures)
    lines = body.split("\n")
    paras = []
    current = []
    for line in lines:
        if line.startswith("#"):
            if current:
                paras.append(" ".join(current))
                current = []
            continue
        if line.strip().startswith("!["):
            if current:
                paras.append(" ".join(current))
                current = []
            continue
        if line.strip() == "":
            if current:
                paras.append(" ".join(current))
                current = []
            continue
        current.append(line.strip())
    if current:
        paras.append(" ".join(current))

    if paras:
        bg = paras[0]
        # Clean up
        bg = re.sub(r'\*\*', '', bg)
        bg = re.sub(r'`([^`]+)`', r'\1', bg)
        if len(bg) > 300:
            bg = bg[:297] + "..."
        return bg
    return ""


# --------------------------------------------------------------------------- #
# .qmd assembly
# --------------------------------------------------------------------------- #

def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _str_presenter)


def assemble_qmd(fm: dict, body: str, paper_dir: Path, pub_date: str) -> Path:
    title = clean_title(fm.get("title", ""))
    subtitle = (fm.get("subtitle") or "").strip()
    institute = fm.get("institute", "")
    author = fm.get("author", "")

    try:
        date_val = date.fromisoformat(pub_date)
    except ValueError:
        date_val = pub_date

    frontmatter = {"title": title}
    if subtitle:
        frontmatter["subtitle"] = subtitle
    frontmatter.update({
        "institute": institute,
        "author": author,
        "date": date_val,
        "categories": fm.get("categories", []),
        "description": fm.get("description", ""),
        "image": f"assets/{fm['hero_figure']}" if fm.get("hero_figure") else "",
        "background": fm.get("background", ""),
        "format": "html",
    })

    fm_yaml = yaml.dump(frontmatter, default_flow_style=False,
                        allow_unicode=True, sort_keys=False)
    qmd = "---\n" + fm_yaml + "---\n\n" + body + "\n"
    qmd_path = paper_dir / "index.qmd"
    paper_dir.mkdir(parents=True, exist_ok=True)
    qmd_path.write_text(qmd, encoding="utf-8")
    return qmd_path


# --------------------------------------------------------------------------- #
# Image copying
# --------------------------------------------------------------------------- #

def copy_images(src_dir: Path, referenced_images: list[str],
                assets_dir: Path) -> int:
    """Copy referenced images from src_dir (or its assets/ subdir) to assets_dir.
    Returns count of successfully copied images."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    # Search locations: src_dir/, src_dir/assets/
    search_dirs = [src_dir, src_dir / "assets"]
    copied = 0
    for basename in referenced_images:
        found = None
        for sd in search_dirs:
            candidate = sd / basename
            if candidate.exists():
                found = candidate
                break
        if found:
            shutil.copy2(found, assets_dir / basename)
            copied += 1
        else:
            print(f"[warn] image not found: {basename} in {src_dir}")
    return copied


# --------------------------------------------------------------------------- #
# Main migration
# --------------------------------------------------------------------------- #

def migrate_dir(src_dir: Path, dry_run: bool = False) -> str | None:
    """Migrate a single source directory. Returns the paper dir name or None."""
    md_path = find_md_file(src_dir)
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    pub_date = parse_date(fm, md_path)
    slug = derive_slug(src_dir, fm.get("title", ""))
    paper_name = f"{pub_date}-{slug}"
    paper_dir = PAPERS_DIR / paper_name

    print(f"\n>>> {src_dir.name} -> papers/{paper_name}")

    # Normalize body
    norm_body, ref_images = normalize_body(body, src_dir)

    # Generate heuristic metadata
    title = clean_title(fm.get("title", src_dir.name))
    fm.setdefault("categories", derive_categories(title, body))
    fm.setdefault("description", derive_description(title))
    fm.setdefault("hero_figure", derive_hero_figure(ref_images))
    fm.setdefault("background", derive_background(title, body))

    if dry_run:
        print(f"  date={pub_date} slug={slug}")
        print(f"  title={title[:60]}")
        print(f"  categories={fm['categories']}")
        print(f"  hero={fm['hero_figure']}")
        print(f"  images={len(ref_images)} referenced")
        print(f"  H1={len([l for l in norm_body.splitlines() if l.startswith('# ')])}")
        print(f"  H2={len([l for l in norm_body.splitlines() if l.startswith('## ')])}")
        return paper_name

    # Check for conflicts
    if paper_dir.exists():
        print(f"  [skip] papers/{paper_name} already exists")
        return None

    # Copy images
    assets_dir = paper_dir / "assets"
    copied = copy_images(src_dir, ref_images, assets_dir)
    print(f"  copied {copied}/{len(ref_images)} images")

    # Assemble
    qmd_path = assemble_qmd(fm, norm_body, paper_dir, pub_date)
    print(f"  wrote {qmd_path}")
    return paper_name


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate historical .md presentations to canonical .qmd")
    ap.add_argument("dirs", nargs="*", help="specific source dirs to migrate (default: all)")
    ap.add_argument("--dry-run", action="store_true", help="preview without writing")
    args = ap.parse_args()

    if args.dirs:
        src_dirs = [ROOT / d for d in args.dirs if (ROOT / d).is_dir()]
    else:
        src_dirs = find_source_dirs()

    if not src_dirs:
        print("no source directories found")
        return

    print(f"Found {len(src_dirs)} source directories")
    migrated = []
    skipped = 0
    for d in src_dirs:
        result = migrate_dir(d, dry_run=args.dry_run)
        if result:
            migrated.append(result)
        else:
            skipped += 1

    print(f"\n=== {'DRY RUN: ' if args.dry_run else ''}Done: "
          f"{len(migrated)} migrated, {skipped} skipped ===")
    if migrated:
        print("Migrated papers:")
        for name in sorted(migrated):
            print(f"  papers/{name}")


if __name__ == "__main__":
    main()
