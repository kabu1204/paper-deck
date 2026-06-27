# PRD：论文分享网站自动化发布系统

**版本** 0.5 · **状态** M1 生成链路完成（经 grilling + M0 格式 pivot + M1 provider 落地）
**背景** 作者每周分享一篇 paper，曾用 markdown 写 slides、pandoc 转 beamer PDF。目标是把历史和新增 pre 自动化发布为可在线浏览的静态网站，并**自动从 `paper.pdf` 生成 presentation markdown**——每周只需把 PDF 拖进 `inbox/`、跑一条命令、过目、push。构建层采用 **Quarto**（HTML article + listing 自动索引）；生成层采用 **OpenAI-compatible Chat Completions**（vision 由 env flag 门控 + structured outputs）+ **MinerU Precision**（figure 提取）。

> **v0.2 → v0.3 主要变化**
> 1. 新增 `generate.py`：从 `paper.pdf` 端到端生成 `index.qmd`（v0.2 假设 `.qmd` 手写）。
> 2. 删除 `enrich.py`：MinerU 配图 + description 写回全部并入 `generate.py` 的单次 LLM 调用。
> 3. MinerU 改为 **Precision 模式**（非 Flash）：Flash 不输出 figure 文件且 10 MB 限制过小（`example-papers/` 中 ThunderKittens 14.8 MB 无法用 Flash）。
> 4. 定义**规范 slide 风格**：3 个 H1 节、`## H2` = 一张 slide、无 `---`、无 `\centering`。历史 5 套 deck 风格不一致，迁移时统一。
> 5. 触发方式改为 **inbox 目录**（v0.2 是 `enrich.py papers/xxx/`）。
> 6. `_freeze/` 暂不引入，CI 每次全量重建（v0.2 计划 commit freeze）。
> 7. LLM 从 Claude API 换为 **OpenAI Responses API**（`gpt-5.5`）。
> 8. **beamer PDF 输出（G6/P2）推迟到未来**：M0 仅验证 + listing；环境验证发现本机无 LaTeX 引擎且 sudo 不可用，TinyTeX/tectonic 暂不安装。beamer 在 PRD 中保留为**目标架构**，待用户选定 LaTeX 来源后再启用。

> **v0.3 → v0.4 主要变化（M0 后格式 pivot）**
> 9. **Web 格式从 revealjs slides 改为 HTML article**：M0 验证 revealjs 时发现 figure 尺寸/居中/字号受 slide-canvas 限制，且 revealjs 与 html 同为 `.html` 输出在 website 项目中冲突（Quarto [#4470](https://github.com/quarto-dev/quarto-cli/issues/4470)）。Quarto 官方推荐 HTML article 用于 website 内容（[Figures doc](https://quarto.org/docs/authoring/figures.html) 的 `fig-align`/`lightbox`/`width` 等特性均为 article 能力）。
> 10. **目录级元数据 `papers/_metadata.yml`**：revealjs/html defaults 从项目级 `_quarto.yml` 移到 `papers/_metadata.yml`，避免 #4470 的 html+revealjs 双渲染冲突（项目级只留 `html`）。
> 11. **规范风格从"slide"重定义为"article"**：H1/H2 仍是 3 节结构，但 H2 不再是"一张 slide"而是 article 子节；去掉 `slide-level: 2`、`---`/`\centering` 的禁用条款（article 无关）；figure 加 `fig-align=center`。
> 12. **beamer 仍 `[未来]`**，定位变为"Download slides (PDF)"下载物（M5），而非 web 格式。beamer 输出 `.pdf` 与 html 的 `.html` 不冲突，未来可在 `_metadata.yml` 与 html 并列启用。

> **v0.4 → v0.5 主要变化（M1 生成链路落地）**
> 13. **LLM 从 OpenAI Responses API + `gpt-5.5` 改为 OpenAI-compatible Chat Completions**：`.env` 实为 OpenRouter + `qwen/qwen3.7-plus`（非 vision、非 Responses API）。`generate.py` 经 `OPENAI_BASE_URL`/`MODEL` 走 Chat Completions，换 provider 无需改代码。vision 由手动 env flag `MODEL_SUPPORTS_VISION` 门控（默认 false → 仅用 MinerU caption/context 选图；true → 送 `image_url` data URI）。决策 #19。
> 14. **双消息模式**（决策 #23）：metadata 与 body 分离——metadata 走 tool call（`set_paper_metadata`）或 `<<<METADATA>>>` 分隔符 JSON，body 走 message content（raw markdown，无 JSON 转义）。取代 v0.4 的"单 JSON 对象 + `body_markdown` string property"（body 嵌入 JSON 偶发截断）。Qwen thinking mode 不支持 forced `tool_choice`，故实际走 delimiter fallback。
> 15. **figure 前后必须空行**（决策 #20）：`![](fig){...}` 紧接 `- bullet`（无空行）会被 pandoc 合并成单段，lightbox 失效 + 列表损坏。已写入 system prompt 显式规则 + Appendix B。
> 16. **MinerU 上传细节**：presigned OSS URL 的 `PUT` 不带 `Content-Type`（否则签名校验 403）。`content_list.json` caption 字段名容错（`img_caption`/`caption`/`text`）。
> 17. **`examples/` 在 M1-prep 提前部分填充**：4 套（h2o/impress 复用 M0 产物 + geminifs/scalexfs 从 `docs/prompts` 轻清理 strip `---`/canonicalize H1）；flashinfer + 完整 frontmatter 补全仍归 M2 `migrate.py`。

---

## 1. 目标

| # | 目标 | 优先级 |
|---|------|--------|
| G1 | 历史所有 pre 可在线浏览（HTML article，scrollable + lightbox） | P0 |
| G2 | 主页自动聚合所有 paper 的索引，无需手动维护 | P0 |
| G3 | 新增一篇 paper **只需把 PDF 拖进 `inbox/` 并跑一条命令**，其余全自动 | P0 |
| G4 | 每篇 paper 配有 LLM 生成的一句话简介 + 网站专属背景段落 | P1 |
| G5 | 每篇 article 自动配上 paper 中的相关 figure（LLM 在生成时即选定） | P1 |
| G6 | beamer PDF 同时自动产出并托管 **[未来，当前推迟]** | P2 |

**新增 G7**：从 `paper.pdf` 自动生成符合个人风格的 presentation markdown（article 正文，含 H1/H2 结构、bullets、figure 引用）。

**不在本期范围**：评论系统、用户账号、搜索（Quarto 内置 `search.json` 已够用）、非 arxiv 来源 paper 的自动 fetch、**beamer PDF 输出（G6，待选定 LaTeX 来源后启用）**。

---

## 2. 用户故事

**每周发布新 paper（核心流程）**

> 作为分享者，我把本周 paper 的 PDF 拖进 `inbox/`，运行 `python generate.py`。脚本调用 MinerU 解析 PDF、调用 OpenAI 生成完整 `index.qmd`（含 frontmatter、article body、figure 引用、description、background），打印 outline 让我过目。我 `git add && commit && push`，网站自动更新。若想看渲染效果，加 `--preview` 自动跑 `quarto preview`。

**浏览历史 pre**

> 作为读者，我打开主页，看到按时间倒序排列的 paper 卡片（标题、作者、日期、一句话简介、来自 paper 的封面图缩略图）。点进去先看到一段背景 prose，再滚动浏览 article 正文（H1/H2 结构 + 配图，图可点击 lightbox 放大）。

**重做一篇已发布的 paper（如换了模型/prompt）**

> 作为分享者，我把 `paper.pdf` 重新放回 `papers/<folder>/`，运行 `python generate.py --regenerate papers/<folder>/`，脚本覆写 `index.qmd` + `assets/`，保留原 folder 名和日期。

---

## 3. 系统架构

### 3.1 数据源原则：一份 `.qmd`，所有东西从这里生长

`index.qmd` 由 `generate.py` 生成并落盘，是后续一切（listing、HTML 渲染）的单一源。`paper.pdf` gitignore，不进 Git（版权 + 体积；arxiv 可随时重下载）。

```
_quarto.yml                        # 项目级配置（唯一，只声明 html 作 website 默认）
index.qmd                          # 主页，Quarto listing 自动生成索引
inbox/                             # 待处理 PDF（gitignore）
  └── *.pdf
papers/
├── _metadata.yml                  # 目录级元数据：html article defaults（toc/lightbox），[未来] 加 beamer
├── 2025-12-19-flashinfer/         # 历史迁移（date 取自原 frontmatter）
│   ├── index.qmd                  # 唯一写作源（迁移自原 flashinfer.md）
│   ├── paper.pdf                  # gitignore
│   └── assets/
│       ├── image.png              # 迁移时沿用原图
│       └── ...
└── 2026-06-27-thunderkittens/     # 新生成
    ├── index.qmd                  # generate.py 产出
    ├── paper.pdf                  # gitignore
    └── assets/
        ├── fig1.png               # MinerU 提取 + LLM 选中的图
        └── ...
examples/                          # 风格参考（few-shot），不进 listing
├── geminifs.qmd
├── scalexfs.qmd
├── flashinfer.qmd
├── h2o.qmd
└── impress.qmd
```

`index.qmd` 的 frontmatter 是整个系统的单一元数据源，同时驱动 Quarto listing 和 HTML 渲染（beamer 未来，作为额外 PDF 输出）：

```yaml
---
title: "FlashInfer: Efficient and Customizable Attention Engine for LLM Inference Serving"
subtitle: "MLSys '25"             # venue 标签（venue+year）；无 venue 时整行省略，不渲染空标签
institute: "(UW, NVIDIA, Perplexity AI, CMU)"
author: "Zihao Ye, Lequn Chen, ..."
date: 2025-12-19                   # 分享日（脚本取当天，不从 paper 提取）
categories: [attention, inference, gpu]
description: "Unified block-sparse KV-cache format + JIT-customizable attention templates, balancing diverse workloads with hardware adaptation."
image: assets/image.png            # listing 卡片封面（hero figure）
background: |                      # 网站专属 prose，2-3 段，不在 article 正文内
  LLM inference serving 的 attention 算子面临两类挑战：workload 模式多样（prefill / decode / prefix-reuse / tree decoding），以及不同 GPU 架构的定制化需求 ...
format: html                       # 继承 papers/_metadata.yml 的 html defaults（toc/lightbox）
                                   # [未来] beamer 启用时改为 format: {html: ..., beamer: ...}
---

# Background & Motivation

## Attention is Critical for LLM Inference

- Transformer architecture dominates LLMs
- Attention mechanism reads from KV-cache and computes outputs based on queries
- ![](assets/image.png){width=70% fig-align=center}
- ...
```

**Quarto 原生字段**（`title` / `date` / `author` / `description` / `image` / `categories`）直接被 listing 消费。`subtitle` / `institute` / `background` 是自定义字段，Quarto 透传，供模板或脚本读取。

**frontmatter 是 LLM 生成的落盘位置**，commit 后即为事实，CI 只读 frontmatter，不再调用 LLM。

**关于 `papers/_metadata.yml`**：Quarto 的目录级元数据机制——该目录下所有 `.qmd` 自动继承其中的 `format:` defaults（[Quarto docs: Directory Metadata](https://quarto.org/docs/projects/quarto-projects.html#directory-metadata)）。我们把 html article defaults（`toc: true`、`lightbox: true`）放这里，**不**放项目级 `_quarto.yml`，原因是项目级同时声明 `html` 和 `revealjs` 会触发 [#4470](https://github.com/quarto-dev/quarto-cli/issues/4470)（两格式都输出 `.html`，post-render move 冲突）。`_metadata.yml` 只对 `papers/` 生效，主页 `index.qmd` 不受影响。未来 beamer 启用时也加在此处（beamer 输出 `.pdf`，与 html 的 `.html` 不冲突）。

### 3.2 单条流水线，两段职责

```
写作期（本地，交互式，调用 LLM）              构建期（CI，确定性，无 LLM）
──────────────────────────────              ──────────────────────────────
拖 PDF 进 inbox/                            push 触发
python generate.py                          quarto render
  ├─ MinerU Precision 解析 PDF                ├─ listing 扫 frontmatter → 主页
  │   → text + cropped figures + captions     ├─ HTML article（每篇，含 toc/lightbox）
  ├─ OpenAI Responses API（单次调用）         └─ beamer PDF（每篇）[未来]
  │   → 完整 index.qmd（frontmatter + body         ↓
  │     + figure 引用 + description + bg）   _site/ 静态产物 → GitHub Pages
  └─ 打印 outline 供过目
python generate.py --preview（可选）
git add && commit && push
```

**LLM 永远不在 CI 里调用。** 生成是一次性的、有人工把关的操作，结果落盘即为事实。

### 3.3 工具链选型

| 环节 | 选型 | 理由 |
|------|------|------|
| 源格式 | `.qmd`（Quarto Markdown） | 与现有 `.md` 语法兼容，迁移改后缀即可 |
| Web 渲染 | Quarto（`quarto render`）→ **HTML article** | Quarto website 的原生内容格式；figure `width`/`fig-align`/`lightbox` 全支持；无 #4470 冲突 |
| 网站索引 | Quarto listing（内置） | 扫描 frontmatter 自动生成卡片索引，零额外代码 |
| PDF slides 格式 | beamer（Quarto 内置）**[未来]** | 作为"Download slides (PDF)"下载物；需 LaTeX 引擎（TinyTeX/tectonic），待选定；输出 `.pdf` 与 html 不冲突 |
| 网站主题 | Quarto website + cosmo/flatly | 开箱即用，响应式 |
| Figure 提取 | **MinerU Online API · Precision 模式** | 返回 cropped figure PNG + caption + bbox；200 MB / 200–600 页；免费 token |
| LLM 生成（article + 配图 + 元数据） | **OpenAI-compatible Chat Completions · 双消息模式**（经 `OPENAI_BASE_URL`/`MODEL`） | metadata 走 tool call 或 `<<<METADATA>>>` 分隔符，body 走 message content（raw markdown，无 JSON 转义）；vision 由 `MODEL_SUPPORTS_VISION` 门控；`MODEL_SUPPORTS_STRICT_JSON_SCHEMA=true` 时 tool 加 `strict`。换 provider 无需改代码 |
| LLM 备选 | 任意 OpenAI-compatible vision 模型（如 `openai/gpt-4o`、`qwen/qwen-2.5-vl-72b`） | 需 vision 选图时切到此类模型并置 `MODEL_SUPPORTS_VISION=true` |
| CI | GitHub Actions + quarto-actions | 官方 Action，含 TinyTeX（beamer 启用时） |
| 托管 | GitHub Pages（`quarto publish gh-pages`） | Quarto 官方支持 |
| 增量构建 | **无（v1 全量重建）** | 仅缓存 TinyTeX 包（beamer 启用时）；N 增长后再评估 `_freeze/` |

---

## 4. 功能详述

### F1 — 生成脚本 `generate.py`（核心，取代 v0.2 的 `enrich.py`）

**触发方式**：
- `python generate.py`：处理 `inbox/` 下所有 PDF，每个生成新 `papers/<today>-<slug>/`。inbox 为空则 no-op（这就是幂等性）。
- `python generate.py --regenerate papers/<folder>/`：要求该 folder 内已重新放入 `paper.pdf`，覆写 `index.qmd` + `assets/`，**保留 folder 名和 frontmatter `date`**。
- `python generate.py --preview`：生成后自动跑 `quarto preview` 打开浏览器。

**输入**：`inbox/*.pdf`（或 `--regenerate` 指定 folder 内的 `paper.pdf`）+ `examples/*.qmd`（风格 few-shot，只读文本，不读图）。

**步骤**：

1. **MinerU Precision 解析**（`https://mineru.net/api/v4`，`model_version: "vlm"`，Bearer token）：`POST /file-urls/batch` 取 presigned URL → `PUT` 原始字节（**不带 `Content-Type`**，否则 OSS 签名校验 403）→ 轮询 `GET /extract-results/batch/{batch_id}` 至 `done` → 下载 `full_zip_url` 解压，获取：
   - 完整正文 markdown（`full.md`，含公式 LaTeX、表格 HTML）
   - 每张 figure 的裁剪图（`images/`，复制到 `assets/` 命名 `figN.png`；非 PNG 用 Pillow 转 PNG）
   - 对应 caption（从 `content_list.json` image block 取，字段名容错 `img_caption`/`caption`/`text`；无则取相邻 text block 作 context）
   - `content_list.json` 中的 bbox 与正文引用位置（图的"语境"）

2. **组装 OpenAI Chat Completions 输入**（OpenAI-compatible，经 `OPENAI_BASE_URL`/`MODEL` 配置，可指向 OpenRouter 或真实 OpenAI）：
   - system message：角色 + 规范风格规则（3 H1 节、`## H2` = article 子节、`![](assets/figN.png){width=70% fig-align=center}`、~20-30 H2 子节、**figure 前后必须空行**（否则图与 bullet 合并成一段，lightbox 失效 + 列表渲染损坏；见决策 #20）、content density 同历史 deck）+ JSON schema 字段说明
   - user message（multipart content）：
     - `text`：paper 正文 markdown + figure catalog（filename + caption + 引用句）+ 4 个风格示例 deck 全文（来自 `examples/`）+ 任务说明
     - 若 `MODEL_SUPPORTS_VISION=true`：追加 `image_url`（data URI，Pillow 缩至 ≤1024px，cap 20 张）让模型"看"图来选最佳表示
     - 若 `MODEL_SUPPORTS_VISION=false`（默认，如 `qwen/qwen3.7-plus`）：不送图，模型仅依据 catalog 的 caption + context 选图

3. **单次 Chat Completions 调用 — 双消息模式**（metadata + body 分离，决策 #23）：
   ```python
   client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
   # Primary: tool call for metadata + content for body
   resp = client.chat.completions.create(
       model=MODEL, messages=[{"role":"system","content":SYSTEM_PROMPT_TOOLS}, user_msg],
       tools=[METADATA_TOOL],                      # set_paper_metadata(slug, title, ...)
       tool_choice={"type":"function","function":{"name":"set_paper_metadata"}},
       max_tokens=12000, temperature=0.3,
   )
   metadata = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
   body = resp.choices[0].message.content           # raw markdown, NOT JSON-escaped
   # Fallback (if tools unsupported): <<<METADATA>>> json <<<BODY>>> raw markdown
   ```
   metadata 字段：`slug`（url-safe 小写连字符）/`title`/`subtitle`（**venue 标签**：venue+year，paper 首页/Proceedings 文本或 LLM 对知名 paper 的认知；无 venue 则空串，组装时省略）/`institute`/`author`/`categories`(array)/`description`(≤80 字一句)/`background`(2-3 段 web prose)/`hero_figure`(如 `fig3.png`)。body = article 正文（H1/H2 + `![](){width fig-align}`），raw markdown。

   **关于双消息模式的实现注记**（决策 #23）：v0.5 原用单 JSON 对象（`body_markdown` 作为 string property），实测 body 嵌入 JSON 后偶发截断（JSON 转义退化）。改为双消息：metadata 走 tool call（或 fallback 的 `<<<METADATA>>>` 分隔符 JSON），body 走 message content（raw markdown，无 JSON 转义）。**Qwen thinking mode 不支持 forced `tool_choice`**（400 error），故实际走 delimiter fallback；OpenAI/其他模型走 tool-call 主路径。两种路径均产出 `(metadata, body)` 元组，Python 组装时合为 `data = {**metadata, "body_markdown": body}`。

   **关于 LLM provider 的实现注记**（决策 #19）：PRD v0.4 原写 OpenAI Responses API + `gpt-5.5`，M1 实现时据 `.env`（OpenRouter + `qwen/qwen3.7-plus`）改为 **OpenAI-compatible Chat Completions**（`OPENAI_BASE_URL`/`MODEL` 可指向 OpenRouter 或真实 OpenAI，换 provider 无需改代码）。vision 由手动 env flag `MODEL_SUPPORTS_VISION` 门控（默认 false；切到 vision 模型时置 true，送 `image_url` data URI）。`MODEL_SUPPORTS_STRICT_JSON_SCHEMA=true` 时 tool 定义加 `strict: true`（OpenAI structured outputs for function calling）。

4. **Python 组装 `.qmd`**：用 YAML 库（`pyyaml`）写 frontmatter（避免 LLM 直出 YAML 的引号/多行 `title: |` 块损坏），拼接 `body_markdown`，写入 `papers/<today>-<slug>/index.qmd`。`hero_figure` → `image: assets/<hero>`。`today` 取**当天本地日期**（presentation/分享日；**不从 paper 提取**——paper 本身未必标出版日期，决策 #22）。`subtitle`（venue 标签）仅在非空时写入 frontmatter，空则省略（避免空标签）。JSON 解析容错：`_extract_json` 剥离 ```json 代码围栏 + 大括号匹配，`max_tokens=12000`，1 次重试。

5. **终端展示预览**：打印 `description`、`background` 首段、`hero_figure`、figure 列表、slide outline（H1/H2 标题树），供作者确认。

**幂等性**：inbox 模式下，已处理 PDF（`papers/` 中已有同 slug folder）默认跳过，除非 `--force`。`--regenerate` 模式显式覆写。

**成本估算**：`gpt-5.5`，~40k input tokens（paper 正文 + 5 示例）+ ~15 张 figure（vision，patches 算法）+ ~5k output tokens ≈ **$0.10–0.20 / paper**。每周一次，可忽略。换 `gpt-5.4` 可降到 ~$0.05–0.10。

### F2 — 迁移脚本 `migrate.py`（一次性，处理 3 套历史 deck）

历史 deck（`flashinfer/`、`h2o/`、`impress/`）是人工精修作品，**不**走 `generate.py`（避免被 LLM 重写降低质量）。迁移机械化：

1. 读每个 folder 的 `*.md` frontmatter，取 `date`。
2. 重命名 folder 为 `papers/<date>-<slug>/`（slug 从原 folder 名或 title 派生）。
3. `.md` → `.qmd`。
4. 规范化：删除所有 `\centering`（flashinfer 用了，LaTeX 命令，HTML article 不识别；figure 居中改用 `fig-align=center` 属性）；删除所有 `---`（flashinfer/ScaleXFS 用了，article 不需要分隔符，H2 自然分节）；统一 H1 节标题为 `Background & Motivation` / `Design` / `Evaluation`；figure 加 `fig-align=center`。
5. 图片移到 `assets/`，更新 `![]()` 路径。
6. 补全 frontmatter：`format: html`、`categories`、`description`、`image`（hero）、`background`。其中 `description`/`background`/`categories` 用一次小型 OpenAI 调用（读 article + abstract）生成；其余字段机械化。
7. 把规范化后的副本也写入 `examples/`（作为 `generate.py` 的 few-shot）。

**同时**：把 `docs/prompts-for-paper-presentation-generation.md` 里的 GeminiFS、ScaleXFS 两套示例也规范化后存入 `examples/`。最终 `examples/` 含 5 套规范化 deck。

**清理**：删除 `h2o.html`、`impress.html`（旧 pandoc revealjs 产物）和根目录 `reveal.css`/`slides.css`/`reveal_slides.scss`/`pandoc-custom.css`（Quarto 主题系统取代）。**注：M0 调试时这些 CSS 已被删除（无可恢复备份），迁移时无需再处理。**

### F3 — Quarto 项目配置

**`_quarto.yml`**（项目级，只声明 `html` 作 website 默认 —— 同时声明 `revealjs` 会触发 [#4470](https://github.com/quarto-dev/quarto-cli/issues/4470)）：

```yaml
project:
  type: website
  output-dir: _site
  render:
    - index.qmd
    - "papers/**/*.qmd"

website:
  title: "Weekly Paper Sharing"
  navbar:
    left:
      - href: index.qmd
        text: All Papers
  page-footer:
    center: "Source on [GitHub](https://github.com/yourname/paper-sharing)"

format:
  html:
    theme: cosmo
    css: styles.css             # venue 标签 pill 徽章样式（.subtitle.lead / .listing-subtitle）
    toc: false
```

**`papers/_metadata.yml`**（目录级，被 `papers/` 下所有 `.qmd` 继承；html article defaults + 未来 beamer）：

```yaml
format:
  html:
    toc: true
    number-sections: false
    lightbox: true
  # beamer:                    # [未来] 待选定 LaTeX 来源后启用
  #   slide-level: 2
  #   pdf-engine: xelatex
  #   fontsize: 11pt
```

**主页 listing `index.qmd`**：

```yaml
---
title: "Weekly Paper Sharing"
listing:
  contents: papers
  sort: "date desc"
  type: grid
  fields: [title, subtitle, date, author, description, image, categories]  # subtitle = venue 标签
  categories: true
  feed: true
---
```

**构建命令**：`quarto render` / `quarto preview` / `quarto publish gh-pages`。

**已知风险**：beamer 与 html article 对公式、图片布局处理存在差异（beamer 是 paged PDF，html 是 scrollable），但两者输出格式不同（`.pdf` vs `.html`），不触发 #4470。beamer 启用时（M5）单独验证 figure 在 paged 布局下的表现（`{width=70%}` 在 beamer 是相对页宽，在 html 是相对内容栏，可能需要各格式微调）。

### F4 — CI 构建流（GitHub Actions，无 freeze）

> beamer 未启用前，CI 不需要 TinyTeX；下方 TinyTeX 步骤标 `[未来]`，beamer 启用时取消注释。

```yaml
# .github/workflows/publish.yml
on:
  push:
    branches: [main]
    paths: ['papers/**', '_quarto.yml', 'index.qmd']

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    permissions: { contents: write }
    steps:
      - uses: actions/checkout@v4
      - name: Setup Quarto
        uses: quarto-dev/quarto-actions/setup@v2
        with: { tinytex: false }    # [未来] beamer 启用时改 true
      # [未来] beamer 启用时取消注释：
      # - name: Cache TinyTeX
      #   uses: actions/cache@v4
      #   with:
      #     path: ~/.TinyTeX
      #     key: tinytex-${{ hashFiles('papers/**/*.qmd') }}
      - name: Render & Publish
        uses: quarto-dev/quarto-actions/publish@v2
        with: { target: gh-pages }
        env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
```

**v1 不用 `_freeze/`**：每次全量 `quarto render`。N 增长到 CI 慢得难受时再评估 commit freeze + LFS。

**单篇失败隔离**：beamer 编译失败只影响该篇 PDF（未来），HTML article 独立渲染，CI 整体不中断。

### F5 — 网站结构

```
_site/
├── index.html                        # listing 卡片
├── papers/
│   └── 2026-06-27-thunderkittens/
│       ├── index.html                # HTML article（scrollable，含 toc + lightbox）
│       ├── index.pdf                 # beamer PDF [未来]
│       └── assets/fig*.png
└── search.json                       # Quarto 自动全文搜索
```

每篇落地页为 HTML article（顶部 title block + 右侧/顶部 toc + 滚动正文 + lightbox 图）；`background` prose 通过 detail-page partial 在正文前展示（M4 打磨）；beamer PDF 下载链接（M5 启用后）通过自定义 partial 注入。

---

## 5. 数据流总览

```
paper.pdf ──► MinerU Precision ──► text + cropped figures + captions + bbox
                                        │
examples/*.qmd (风格) ───────────────► OpenAI Responses API (gpt-5.5, vision, structured)
                                        │
                                        ▼
                              structured JSON
                              {slug, title, ..., hero_figure, body_markdown}
                                        │
                              Python 组装（YAML 库写 frontmatter）
                                        ▼
                              papers/<today>-<slug>/index.qmd + assets/
                                        │
                                  git push
                                        │
                                 quarto render
                          ├───────────────┼───────────────┐
                          ▼               ▼               ▼
                  HTML article    [未来]beamer PDF  listing 主页
                          └───────────────┴───────────────┘
                                        ▼
                                  _site/ → GitHub Pages
```

---

## 6. 非功能需求

| 需求 | 指标 |
|------|------|
| CI 构建时间 | v1 全量（html-only）：3 篇 < 2 min；50 篇 < 8 min。beamer 启用后 +50%（LaTeX 是瓶颈） |
| 页面加载 | article 首屏 < 2s（Quarto 默认 lazy load 图片） |
| MinerU Precision 容忍 | 200 MB / 200 页；超出提示用户用 `--pages` 分段 |
| 幂等性 | inbox 模式：同 slug folder 已存在则跳过；`--regenerate` 显式覆写 |
| LLM 成本 | 模型相关：M1 实测 Qwen via OpenRouter ~33-43k tokens/paper ≪ $0.05；vision 模型或 gpt-5.5 上界 < $0.20/paper |
| 图片版权 | 每张配图在页面上注明来源论文标题 + arxiv 链接 |
| Secrets | `.env`（gitignore）含 `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `MODEL` + `MINERU_API_TOKEN` + `MODEL_SUPPORTS_VISION`(bool) + `MODEL_SUPPORTS_STRICT_JSON_SCHEMA`(bool)；CI 无 LLM 调用，无需 secrets |

---

## 7. 里程碑

| 阶段 | 交付物 | 估算 |
|------|--------|------|
| **M0** Quarto 验证（html article + listing）✅ 已完成 | 1-2 套历史 deck 转 canonical `.qmd` + `_quarto.yml` + `papers/_metadata.yml`（html defaults）+ `quarto render` **html** + listing 验证。**Checklist**（全部 PASS）：`{width=70%}` 渲染为 `style="width:70.0%"` + `img-fluid`；`title: \|` 多行 frontmatter 存活；listing 卡片显示 6 个原生字段；`fig-align`/`lightbox`/`toc` 生效；2 卡片 + 类别侧边栏 + 倒序排序。**过程中发现并修复 [#4470](https://github.com/quarto-dev/quarto-cli/issues/4470)**（项目级 html+revealjs 双格式冲突 → 改用 `_metadata.yml`）。**过程中 pivot：revealjs → html article**（figure 受 slide-canvas 限制；见 v0.3→v0.4 changelog）。 | 2-4h（实际） |
| **M1** 生成链路 ✅ 已完成 | `generate.py`：MinerU Precision（`vlm`）接入 + 单次 **OpenAI-compatible Chat Completions** 调用（`json_object` 默认 / strict `json_schema` 可选）+ Python `.qmd` 组装 + inbox 处理 + `--regenerate` + `--preview` + `--force`。跑 `example-papers/` 两篇（T-MAC 904KB / ThunderKittens 14.8MB）→ 质量基准。**结果**：两篇均产出 3 H1 + 22-28 H2 + 10-11 figure（lightbox + 居中 + width 俱全）+ 完整 frontmatter，`quarto render` 通过、listing 4 卡片。**过程中修复**：(1) OSS presigned PUT 不带 `Content-Type`（403）；(2) figure 前后空行规则（否则 lightbox 失效 + 列表损坏，决策 #20）；(3) `--preview` 在 inbox 空时仍启动。**已知**：`--preview` 的 quarto preview 在本沙箱 HTTP 503（on-demand render 环境问题，不影响 `quarto render` 静态产物）。 | 1 天（实际） |
| **M2** 历史迁移 | `migrate.py`：3 套历史 deck 重命名 + `.md`→`.qmd` + 规范化（strip `\centering`/`---`，加 `fig-align=center`）+ 补全 frontmatter + 写入 `examples/`。清理旧 html。 | 2-4h |
| **M3** 构建发布 | listing `index.qmd` + GitHub Actions（无 freeze，无 TinyTeX）+ gh-pages 发布。网站上线（html article）。 | 0.5 天 |
| **M4** 打磨 | 自定义 partial（background prose 展示、figure 来源标注）、主题微调、`--preview` 体验、`feed` 启用（需 `site-url`）。 | 0.5 天 |
| **M5** beamer 启用 `[未来]` | 选定 LaTeX 来源（TinyTeX via `quarto install tool tinytex`，或 tectonic 单二进制）→ 本地装 → `papers/_metadata.yml` 启用 beamer format 块 → 验证 paged PDF 下 figure 表现 → 验证 PDF 下载 partial。 | 2-4h |

---

## 8. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| **生成质量不及人工标准**（最高风险） | 中 | M1 的 2 篇 example 是试金石。若 LLM article 明显劣于手写，降级为"生成骨架 + 人工 refine body"（退回 Q1 的 Hybrid 选项）。 |
| MinerU figure crop 在密集双栏系统论文上出错 | 中 | M1 揭示。Fallback：Precision crops + 全页 PNG 渲染（`pdftoppm`）双喂 OpenAI，让其选最佳表示。 |
| Quarto html+beamer 双格式 figure 表现不一致（`width` 相对基准不同） | 中（M5 时） | beamer `width=70%` 相对页宽，html 相对内容栏；M5 启用时各格式微调或用 `fig-pos`/`fig-align` 显式控制。 |
| 单次调用过载（slug + 9 字段 + 20-30 H2 子节 + 配图一次产出） | 中 | 质量不佳则拆为 2 次内部调用（article body 先、metadata 后），仍保持对用户是单条 `generate.py` 命令。 |
| OpenAI vision token 计费不透明（gpt-5.2+ 的 patches 算法有未文档化乘数） | 低 | 默认 `detail: auto`；监控单次 cost；超预算则换 `gpt-5.4` 或缩图后上传。 |
| MinerU Precision 限流 / token 失效 | 低 | `.env` 管理；失败重试 + 提示。 |
| `examples/` 风格示例与生成 article 风格漂移 | 低 | `migrate.py` 把 5 套 deck 规范化后写入 `examples/`，生成只读 `examples/`，不读 `papers/`。 |
| 历史 `.md` 迁移兼容性 | 低 | 语法兼容，仅需加 `format: html` + 规范化；`migrate.py` 一次性处理。 |
| paper.pdf 版权（图放公网） | 低 | 每张图标注来源；个人非商业分享属合理使用。 |

---

## 附录 A：Grilling 决策日志（18 项，用于追溯）

| # | 分支 | 决议 |
|---|---|---|
| 1 | 范围 | 全集成：`paper.pdf` → 生成 article → 发布，一个系统 |
| 2 | 生成输入 | Text + figures（vision）：MinerU 先跑，OpenAI 看 cropped figure 并在生成时选定 |
| 3 | 生成调用 | 单次 LLM 调用/篇（2-prompt 链是手动 chat 的产物） |
| 4 | enrich 步骤 | 并入 `generate.py`；`enrich.py` 删除 |
| 5 | 规范风格 | 3 H1 节，`## H2` = article 子节，`{width= fig-align=center}` on figures（v0.4 从"slide"重定义为"article"） |
| 6 | 触发 | inbox 目录：拖 PDF 进 `inbox/`，跑 `generate.py`（无参） |
| 7 | folder 命名 | `<today>-<llm-slug>`；历史 3 套迁移时重命名 |
| 8 | review | Hybrid：默认终端过目，`--preview` 触发 `quarto preview` |
| 9 | MinerU | Precision 模式（免费 token，非 Flash）；v1 信任其 crop |
| 10 | 迁移 | 3 套历史机械化迁移 + 2 篇 example 跑 `generate.py` 作质量基准 |
| 11 | `background` 字段 | 保留为网站专属 prose（detail page，不在 article 正文内） |
| 12 | 构建顺序 | M0 Quarto 验证优先，再做生成 |
| 13 | `paper.pdf` | gitignore + 需要时从 arxiv 重下载 |
| 14 | 重生成 | 两模式：`generate.py`（inbox→新）与 `--regenerate <folder>`（重放 PDF，覆写） |
| 15 | `_freeze/` | v1 不用，全量重建；慢了再说 |
| 16 | 生成输出 | Hybrid：body 原始 markdown + metadata JSON。~~OpenAI 实现细化：用 strict `json_schema`，`body_markdown` 作为 string property~~ → **改为双消息模式**（决策 #23）：metadata 走 tool call / 分隔符 JSON，body 走 message content（raw，无 JSON 转义）。 |
| 17 | beamer PDF 输出 | **推迟到 M5（未来）**：环境验证发现本机无 LaTeX 且 sudo 不可用；beamer 在 PRD 保留为目标架构并标 `[未来]`，待选定 TinyTeX/tectonic 后启用。 |
| 18 | Web 格式（v0.4 pivot） | **revealjs → HTML article**：M0 验证 revealjs 时发现 figure 受 slide-canvas 限制 + [#4470](https://github.com/quarto-dev/quarto-cli/issues/4470) 双格式冲突；改用 Quarto website 原生的 html article（`fig-align`/`lightbox`/`toc` 全支持）。revealjs defaults 从项目级 `_quarto.yml` 移到 `papers/_metadata.yml`。 |
| 19 | LLM provider（M1 实现） | **OpenAI Responses API + gpt-5.5 → OpenAI-compatible Chat Completions**：`.env` 实为 OpenRouter + `qwen/qwen3.7-plus`（非 vision，非 Responses API）。改为经 `OPENAI_BASE_URL`/`MODEL` 的 Chat Completions（换 provider 无需改代码）。vision 由手动 env flag `MODEL_SUPPORTS_VISION` 门控（默认 false → 仅用 caption/context 选图；true → 送 `image_url` data URI）。structured output 默认 `json_object` + Python 校验 + 1 次重试；`MODEL_SUPPORTS_STRICT_JSON_SCHEMA=true` 升级 strict `json_schema`。 |
| 20 | Figure 渲染（M1 发现） | **figure 前后必须空行**：`![](fig){width fig-align=center}` 紧接 `- bullet`（无空行）会被 pandoc 合并成单段 `<p><img> - text - text</p>`，导致 lightbox 失效 + 列表渲染损坏。standalone（前后空行）才渲染为 `<figure><a class="lightbox"><img class="quarto-figure-center">`（lightbox + 居中 + width 三者俱全）。已写入 system prompt 显式规则 + Appendix B。 |
| 21 | venue 标签（M1 增量） | **`subtitle` 作为 venue 标签 pill**：`subtitle` = venue+year（如 "NeurIPS 2023"），listing `fields` 含 `subtitle`，`styles.css` 将 `.subtitle.lead`（article 标题块）与 `.listing-subtitle`（卡片）渲染为 pill 徽章，index 页 + article 页均显示。venue 不可得时 `subtitle` 空串，组装时省略 frontmatter 字段，不渲染空标签（"if available"）。venue 来源：paper 首页/Proceedings 文本（MinerU 常丢页眉故未必有）或 LLM 对知名 paper 的认知；review 时可手动 refine（如 T-MAC "arXiv 2024"→"ASPLOS 2024"）。 |
| 22 | date 字段（M1 增量） | **date 始终取当天，不从 paper 提取**：paper 本身未必标出可靠版日期（会议/期刊 date 与 arxiv date 常不一致），且 `date` 语义是"分享日"。schema 无 date 字段，脚本用 `date.today()`（inbox）；`--regenerate` 保留原 frontmatter date（重做不应挪动 timeline 位置）。 |
| 23 | 双消息模式（M1 增量） | **metadata + body 分离**：取代单 JSON 对象（`body_markdown` 作为 string property 偶发截断）。主路径：`set_paper_metadata` tool call 携带 metadata + message content 携带 raw body（无 JSON 转义）。fallback：`<<<METADATA>>>` / `<<<BODY>>>` 分隔符（Qwen thinking mode 不支持 forced `tool_choice`，实际走此路径）。两者均产出 `(metadata, body)` 元组。若 tool call 返回但 content 为空，追加第二次调用专写 body。 |

---

## 附录 B：规范 article 风格（生成 prompt 与迁移共用）

- **3 个 H1 节**：`# Background & Motivation` / `# Design` / `# Evaluation`
- **`## H2` = article 子节**（不再是 slide；H2 自然分节，无需分隔符）
- **不用 `\centering`**（LaTeX 命令，html article 不识别；figure 居中用 `fig-align=center` 属性）
- **不用 `---` 分隔符**（article 无需；H2 自然分节）
- **图片**：`![](assets/figN.png){width=70% fig-align=center}`，宽度按图调整（25%–80%）；`fig-align=center` 确保居中（无 caption 的 `![]()` 默认不居中，需显式属性）
- **图片前后必须空行**（M1 发现，决策 #20）：每个 `![](...)` 必须是 standalone block，前后各一空行；图后紧跟 `- bullet`（无空行）会被 pandoc 合并成 `<p><img> - text</p>`，lightbox 失效 + 列表渲染损坏。`## H2` 与首个 figure/bullet 之间也要空行。
- **frontmatter**：`title` / `subtitle`（**venue 标签**：venue+year，无则省略）/ `institute` / `author` / `date`（分享日，脚本取当天，不从 paper 提取）/ `categories` / `description` / `image`（hero）/ `background` / `format: html`
- **venue 标签展示**（决策 #21）：`subtitle` 经 `styles.css` 在 listing 卡片（`.listing-subtitle`）与 article 标题块（`.subtitle.lead`）渲染为 pill 徽章；listing `fields` 含 `subtitle`；空 venue 不写入 frontmatter，故不渲染空标签。
- **长度**：~20-30 H2 子节（content density 同历史 deck）
- **无 speaker notes**（article 无此概念）
