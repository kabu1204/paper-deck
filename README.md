# Weekly Paper Sharing

A Quarto static website that turns academic papers into presentation-style HTML articles, then publishes them with GitHub Pages.

**Live site:** <https://kabu1204.github.io/paper-deck/>

## How it works

There are two content paths in this repository:

```text
PDF in inbox/ or papers/<folder>/paper.pdf
  └─ MinerU (hosted API or Modal) → full.md + images/ + content_list.json
      └─ figure catalog (image/chart blocks, captions, nearby context)
          └─ LLM (OpenAI-compatible or Google Gemini)
              └─ papers/<date>-<slug>/index.qmd + assets/figN.png
                  └─ Quarto → static HTML → gh-pages

historical .md deck directories
  └─ migrate.py → papers/<date>-<slug>/index.qmd + copied assets
```

1. **MinerU** parses the PDF in `vlm` mode into markdown, cropped figures, and a content list.
2. **`generate.py`** builds a figure catalog from `image` and `chart` blocks, including captions and surrounding text.
3. **The LLM** reads the paper text, figure catalog, and the four style-reference articles in `examples/`, then returns structured metadata plus a raw Quarto article body.
4. **`generate.py`** writes YAML frontmatter and body to `papers/<date>-<slug>/index.qmd`, then copies only the referenced/hero figures into `assets/` as `figN.png`.
5. **Quarto** renders the `papers/` tree into a grid listing with category filters and an RSS feed.
6. **GitHub Actions** renders and publishes the site to the `gh-pages` branch on pushes to `master` that touch site content/configuration.

## Repository layout

```text
generate.py                    # PDF → canonical article pipeline
modal_mineru.py                # optional self-hosted MinerU backend on Modal L4 GPUs
migrate.py                     # batch migration for historical .md presentation decks
index.qmd                      # homepage listing all papers
_quarto.yml                    # Quarto website config
styles.css                     # font, venue badge, and title metadata layout overrides
examples/                      # style references: h2o, impress, geminifs, scalexfs
papers/                        # published articles rendered by Quarto
  <date>-<slug>/
    index.qmd                  # YAML frontmatter + canonical article body
    assets/                    # figures referenced by the article
docs/                          # PRD and prompt-design notes
inbox/                         # drop new PDFs here; gitignored
.mineru_cache/                 # MinerU parse cache; gitignored
.env                           # local API keys and model/backend config; gitignored
.github/workflows/publish.yml  # CI render + publish to GitHub Pages
```

`papers/_metadata.yml` is not used in the current site. Per-paper defaults live in each article's frontmatter, and site-wide HTML defaults live in `_quarto.yml`.

## Article contract

Generated articles should follow this structure:

- Frontmatter fields: `title`, optional `subtitle` (venue badge), `institute`, `author`, `date`, `categories`, `description`, `image`, `background`, and `format: html`.
- Exactly three `# H1` sections, in this order:
  1. `# Background & Motivation`
  2. `# Design`
  3. `# Evaluation`
- `## H2` subsections are article subsections, not slides; target about 20-30 H2s for a full generated article.
- Figures use the catalog filenames and include explicit sizing/alignment, for example:

  ```markdown
  ![](assets/fig3.png){width=70% fig-align=center}
  ```

  Use widths roughly in the 25%-80% range depending on the figure.
- Every figure must be a standalone block with a blank line before and after it. Without those blank lines, Pandoc can merge the image and following bullets into one paragraph and break rendering.
- Do not use `---` slide separators or LaTeX `\centering` in article bodies.
- Keep bullets concise; no speaker notes.

Legacy migrated papers may still reference historical asset names such as `assets/image.png`, but new `generate.py` output uses `assets/figN.png`.

## Setup

### Prerequisites

- Python 3.11+
- [Quarto](https://quarto.org/) 1.9.x or newer
- For the default MinerU backend: a hosted MinerU API token from <https://mineru.net>
- For the Modal MinerU backend: a Modal account plus `modal setup`
- An LLM endpoint: either OpenAI-compatible Chat Completions or Google Gemini

### Install Python dependencies

```bash
pip install openai requests pyyaml Pillow
```

For the Google Gemini backend, also install:

```bash
pip install google-genai
```

For the Modal MinerU backend, also install and authenticate:

```bash
pip install modal
modal setup
```

### Configure environment

Create a `.env` file in the repository root. Environment variables already set in the shell take precedence over `.env` values.

```dotenv
# --- MinerU hosted API backend (default) ---
MINERU_API_TOKEN=your_mineru_token

# --- OpenAI-compatible LLM backend ---
# OPENAI_BASE_URL is optional for the official OpenAI API, required for most compatible providers.
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=your_openai_key
MODEL=qwen/qwen-2.5-vl-72b
MODEL_SUPPORTS_VISION=true
MODEL_SUPPORTS_STRICT_JSON_SCHEMA=false

# --- Google Gemini LLM backend (experimental) ---
GOOGLE_API_KEY=your_google_key
# Leave GOOGLE_API_BASE_URL unset for Google's default endpoint.
GOOGLE_API_BASE_URL=https://generativelanguage.googleapis.com
GOOGLE_API_MODEL=gemini-2.5-flash

# --- Optional: force the LLM backend when both OpenAI and Google keys are present ---
# LLM_BACKEND=openai      # openai | google

# --- Optional: self-hosted MinerU on Modal GPU VMs ---
# MINERU_BACKEND=modal    # api (default) | modal
# MINERU_DPI=300          # modal only; hosted API output is fixed at 200 DPI
# MINERU_MODEL_SOURCE=huggingface

# --- Optional: path used by `python generate.py --preview` ---
# QUARTO_BIN=/path/to/quarto
```

Backend selection rules:

- LLM: `LLM_BACKEND=openai|google` wins. If unset, `generate.py` uses Google when `GOOGLE_API_KEY` is present; otherwise it uses the OpenAI-compatible backend.
- Vision: Gemini is treated as multimodal automatically. For OpenAI-compatible models, set `MODEL_SUPPORTS_VISION=true` to attach up to 20 figure images as data URIs; otherwise the model only sees figure captions/context.
- MinerU: `MINERU_BACKEND=api` uses the hosted API. `MINERU_BACKEND=modal` imports `modal_mineru.py`, runs MinerU `vlm-engine` on Modal L4 GPUs, and honors `MINERU_DPI`.

## Usage

### Generate new articles from PDFs

```bash
# Drop one or more PDFs into inbox/ and run:
python generate.py

# Ignore the local MinerU cache and re-parse PDFs:
python generate.py --refresh-cache

# Allow generation even if the LLM returns a slug that already exists:
python generate.py --force

# Generate, then launch Quarto preview through QUARTO_BIN/PATH:
python generate.py --preview
```

`generate.py` processes every `*.pdf` in `inbox/`. The PDFs are gitignored and are not removed automatically, so move or delete them after generation if you do not want the next run to consider them again. If a generated slug already exists, the default behavior is to skip it; `--force` bypasses that guard in inbox mode.

### Regenerate an existing article

To regenerate a published paper while keeping the existing folder and date, put the source PDF back as `paper.pdf` inside that paper directory:

```bash
cp /path/to/paper.pdf papers/2026-06-27-marlin/paper.pdf
python generate.py --regenerate papers/2026-06-27-marlin/
```

Regeneration overwrites `index.qmd` and `assets/` for that paper, but preserves the article date from the existing frontmatter when present. Add `--refresh-cache` if the PDF should be reparsed instead of read from `.mineru_cache/`.

### Preview or render the site

```bash
quarto preview
# or
quarto render
```

Use these commands when you only need to check the site; they do not require API credentials.

### Migrate historical presentations

`migrate.py` batch-converts existing root-level `.md` presentation decks into canonical `papers/<date>-<slug>/index.qmd` articles:

```bash
# Preview without writing:
python migrate.py --dry-run

# Migrate all eligible source directories at the repo root:
python migrate.py

# Migrate specific directories:
python migrate.py ariadne flashattn
```

For each source directory, it parses the `.md` frontmatter, falls back to the file modification date when needed, strips slide-only markup (`---`, `\centering`, fenced div markers), removes percentage widths from legacy figures, adds `fig-align=center`, rewrites images into `assets/`, copies referenced images, and fills heuristic metadata such as categories, description, hero figure, and background.

### Test the Modal MinerU backend directly

```bash
modal run modal_mineru.py --pdf inbox/paper.pdf
modal run modal_mineru.py --pdf inbox/paper.pdf --dpi 400 --no-cache
```

For normal generation through Modal, set `MINERU_BACKEND=modal` and run `python generate.py`.

### Deploy

Push site content/configuration changes to `master`. `.github/workflows/publish.yml` runs Quarto and publishes to the `gh-pages` branch, creating that branch on the first run if necessary. The workflow is path-filtered; changes outside `papers/`, `examples/`, `_quarto.yml`, `index.qmd`, `styles.css`, or the workflow file itself do not trigger a deploy.

## MinerU cache

MinerU results are cached under `.mineru_cache/<sha256>/`, keyed by the PDF's SHA-256.

- Hosted API cache entries record `MINERU_MODEL` and `MINERU_LANGUAGE` and are invalidated when those change.
- Modal cache entries record `dpi` and backend and are invalidated when those change.
- `--refresh-cache` forces a fresh parse and updates the cache.

Both backends normalize output to the same files expected by `generate.py`: `full.md`, `images/`, and `content_list.json`.

## License and content notes

Source code in this repository is for personal use. Source PDFs are gitignored because of copyright and size. Generated/cropped figures under `papers/*/assets/` are used only to render the paper-sharing site; respect the original papers' licenses and re-download PDFs from the publisher or arXiv when needed.
