# Weekly Paper Sharing

A static website that turns academic papers into presentation-style articles, published via GitHub Pages.

**Live site:** <https://kabu1204.github.io/paper-deck/>

## How it works

```
paper.pdf  ──MinerU──▶  markdown + figures  ──LLM──▶  index.qmd  ──Quarto──▶  HTML
```

1. **MinerU** (precision `vlm` mode) parses the PDF into full markdown, cropped figures, and a content list.
2. **An LLM** (OpenAI-compatible or Google Gemini) reads the parsed text + figure catalog + 4 style-reference articles and produces structured metadata + an article body in canonical Quarto markdown.
3. **`generate.py`** assembles `papers/<date>-<slug>/index.qmd` (YAML frontmatter + body) and copies the used figures into `assets/`.
4. **Quarto** renders the `.qmd` files into a static website with a grid listing, category sidebar, and RSS feed.
5. **GitHub Actions** builds and publishes the site to the `gh-pages` branch on every push.

## Repository layout

```
generate.py                  # PDF -> index.qmd pipeline (MinerU + LLM + assembly)
index.qmd                    # homepage: Quarto listing of all papers
_quarto.yml                  # site config (theme: flatly, font: Lora)
styles.css                   # venue-badge + font overrides
papers/                      # published articles (one folder per paper)
  <date>-<slug>/
    index.qmd                # frontmatter + canonical article body
    assets/                  # figures referenced by the article
papers/_metadata.yml         # per-paper html defaults (toc, lightbox)
examples/                    # 4 style-reference .qmd files fed to the LLM
inbox/                       # drop PDFs here (gitignored)
.mineru_cache/               # MinerU result cache (gitignored)
.env                         # API keys + model config (gitignored)
.github/workflows/publish.yml  # CI: build + deploy to GitHub Pages
docs/                        # PRD + prompt design docs
```

## Canonical article style

Every generated article follows a fixed structure:

- Exactly three `# H1` sections: **Background & Motivation**, **Design**, **Evaluation**
- `## H2` subsections (~20-30), no numbering
- Figures: `![](assets/figN.png){fig-align=center}` — centered, natural size
- Blank lines around every figure (critical for rendering)
- Concise one-line bullets, no speaker notes

## Setup

### Prerequisites

- Python 3.11+
- [Quarto](https://quarto.org/) 1.9+
- A MinerU API token (from <https://mineru.net>)
- An LLM endpoint — either an OpenAI-compatible API or a Google Gemini API key

### Install Python dependencies

```bash
pip install openai requests pyyaml Pillow google-genai
```

(`google-genai` is only needed for the Gemini backend.)

### Configure environment

Create a `.env` file in the repo root:

```dotenv
MINERU_API_TOKEN=your_mineru_token

# --- OpenAI-compatible backend ---
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=your_openai_key
MODEL=qwen/qwen-2.5-vl-72b
MODEL_SUPPORTS_VISION=true
MODEL_SUPPORTS_STRICT_JSON_SCHEMA=false

# --- Google Gemini backend (experimental) ---
GOOGLE_API_KEY=your_google_key
GOOGLE_API_BASE_URL=https://generativelanguage.googleapis.com
GOOGLE_API_MODEL=gemini-2.5-flash

# --- Optional: force a backend when both keys are present ---
# LLM_BACKEND=google
```

**Backend selection:** if `GOOGLE_API_KEY` is set, the Gemini backend is used; otherwise the OpenAI-compatible endpoint. Set `LLM_BACKEND=openai` or `LLM_BACKEND=google` to override. Gemini is natively multimodal, so figure images are auto-attached (vision) for the google backend; for OpenAI, set `MODEL_SUPPORTS_VISION=true` if the model supports it.

## Usage

### Generate an article from a PDF

```bash
# Drop a PDF into inbox/ and run:
python generate.py

# Re-generate an existing paper (keeps its original date):
python generate.py --regenerate papers/2025-12-19-flashinfer/

# Force-overwrite an existing slug folder:
python generate.py --force

# Ignore the MinerU cache and re-parse:
python generate.py --refresh-cache

# Render and preview locally:
python generate.py --preview
```

### Preview the site

```bash
quarto preview
```

### Deploy

Push to `master`. The GitHub Actions workflow (`.github/workflows/publish.yml`) renders the site and publishes to the `gh-pages` branch, which GitHub Pages serves at <https://kabu1204.github.io/paper-deck/>.

## MinerU cache

MinerU parse results are cached under `.mineru_cache/<sha256>/` keyed by the PDF's SHA-256. Re-runs (including `--regenerate`) skip the slow API call on cache hit. The cache auto-invalidates when `MINERU_MODEL` or `MINERU_LANGUAGE` changes. Use `--refresh-cache` to force a fresh parse.

## License

Source code in this repository is for personal use. Paper PDFs and figures are not committed (copyright); re-download from arXiv when needed.
