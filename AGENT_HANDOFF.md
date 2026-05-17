# VSPP-RAG — Agent Handoff Brief

**Repo:** https://github.com/GaliDev/vspp-rag  
**Path:** `/home/gali/Documents/vspp-rag-main`  
**Latest pushed commit:** `9844c73` — *Improve ingest fidelity, PDF normalization, and RAG chunk quality.*  
**Local work (2026-05-17, not necessarily pushed):** corpus cleanup, core structural ETSI/W3C ingest, embeddings + retrieval eval.

---

## Purpose

**VSPP Standards Vault** — a technical standards ingestion engine for building a RAG corpus over broadcast/streaming specs (3GPP, IETF, ETSI/DVB, ISO, W3C/IMSC, SMPTE, GitHub reference repos, etc.).

Goal: discover standards metadata → download artifacts on demand → normalize to plain text → chunk → **embed for retrieval**.

---

## Pipeline (run in order)

| Step | Script | Writes | Notes |
|------|--------|--------|-------|
| 1. Discover | `discover.py` | `discovery_manifest.json`, `PM_Catalog.md` | New rows default `status: "discovered"`; **merges** prior ingest when discovery fingerprint unchanged |
| 2. Ingest | `ingest.py` | `data/{authority}/raw/` + updates manifest | Selective; use `--reingest` after discovery changes |
| 3. Normalize | `normalize.py` | `data/{authority}/normalized/*.txt`, `data/normalized/records.jsonl` | Does **not** mutate manifest |
| 4. Chunk | `chunk.py` | `data/chunks/chunks.jsonl` | Paragraph-aware; reads `records.jsonl` |
| 5. Embed | `embed.py` | `data/embeddings/vectors.npy`, `chunk_index.jsonl`, `meta.json` | `sentence-transformers/all-MiniLM-L6-v2` (384-d) |
| 6. Eval (optional) | `eval_retrieval.py` | stdout | Queries in `data/eval/queries.jsonl` |

```bash
source .venv/bin/activate
python discover.py
python ingest.py --all --one-per-authority --max-mb 500   # or targeted --reingest IDs
python normalize.py
python chunk.py
python embed.py
python eval_retrieval.py
```

---

## Current scale (local `data/`, gitignored)

| Metric | Value |
|--------|------:|
| Manifest rows | **131** (112 transport, 19 structural/system) |
| `core_structural_syntax` flagged | **10** (EN 300 468 now flagged) |
| Manifest `ingested` | **72** (includes transport rows if bulk ingest was run; not all are normalized/chunked) |
| Normalized docs (`status: normalized` in `records.jsonl`) | **16** |
| Chunks | **2,868** (`paragraph_pack_v1`, 2000 chars / 200 overlap) |
| Embeddings | **2,868 × 384** in `data/embeddings/vectors.npy` |

**Largest chunk sources:** `MPEGGroup/FileFormatConformance` (~1237), `AOMediaCodec/av1-spec` (~587), `etsi-dvb-dash-ts103168` (~240), `etsi-en-300-468-dvb-si` (~84), `MPEGGroup/isobmff` (~92), `etsi-dvb-s2-en302307` (~95).

`data/` is in `.gitignore` — clones only have code + manifest/catalog until ingest is re-run.

---

## Architecture

```
discover.py ──► merge_discovery_preserving_ingest() (src/core/io.py)
  └─ collectors (async gather):
       ietf.py, threegpp.py, github_specs.py, webdrafts.py
       structural_system.py — ISO/ETSI/DVB/CTA/SMPTE/W3C IMSC

ingest.py ──► src/core/artifacts.py (PDF resolve, pick_best_per_authority, liaison URLs)
              GitHub repository archives preferred before runtime PDF crawl
normalize.py ──► pypdf, docx XML, BS4 HTML, repo zip bundles
chunk.py ──► src/core/text_prep.py
embed.py ──► sentence-transformers → vectors.npy
eval_retrieval.py ──► cosine search over embeddings
```

**Core modules**

- `src/core/models.py` — `DiscoveryRecord` dataclass
- `src/core/io.py` — manifest save/load, **`merge_discovery_preserving_ingest`**
- `src/core/catalog.py` — generates `PM_Catalog.md`
- `src/core/artifacts.py` — ingest priority, ETSI deliver PDF crawl, strict `pdf_url_matches_record`, skip PDF crawl for W3C TR + GitHub `repository`
- `src/core/text_prep.py` — `content_with_header`, `strip_manifest_preamble`

**Top-level scripts:** `discover.py`, `ingest.py`, `normalize.py`, `chunk.py`, `embed.py`, `eval_retrieval.py`

---

## Manifest record shape

Each row in `discovery_manifest.json` has roughly:

- `source`, `authority`, `external_id`, `title`, `remote_url`, `file_type`
- `category`: `Transport` | `Structural/System`
- `tier`: `transport-level` | `system-level`
- `status`: `discovered` | `ingested`
- After ingest: `local_path`, `sha256`, `ingested_at`, `metadata` (e.g. `artifact_url`, `ingest_kind`, `liaison_or_attachment_links`, `core_structural_syntax`)

**File types:** `zip`, `repository`, `pdf`, `txt`, `docx`, `html`, `standard-page`, `portal`, `error`

---

## Ingest behavior (important flags)

- `--one-per-authority` — best row per authority; for `core_structural_syntax`, groups by **`authority::external_id`**.
- `--no-page-fallback` — skip portal/HTML when no artifact.
- `--no-resolve-pdfs` — skip runtime ETSI deliver PDF crawl.
- `--reingest EXTERNAL_ID ...` — reset ingest fields and re-download (**use after `discover.py` changes ETSI/W3C rows**).
- **W3C `/TR/`** — runtime PDF resolution **skipped**; ingest as TR HTML (`ingest_kind: page_snapshot`).
- **GitHub `repository`** — runtime PDF resolution **skipped**; downloads branch archive zip first (`ingest_kind: repository_archive`). Resolved PDFs must start with `%PDF` or ingest fails.
- **ETSI PDF matching** — spec-specific tokens (e.g. DASH: `103168`/`103285` only; not generic `etsi_ts`).
- Caps: `--max-mb`, `--limit`; zip-slip protection.

---

## Discovery notes (structural / ETSI)

- `discover.py` calls `merge_discovery_preserving_ingest`: keeps `ingested` + paths when `(remote_url, file_type, metadata.artifact_url)` unchanged.
- **DASH** (`etsi-dvb-dash-ts103168`): deliver seeds use **`etsi_ts/103200_103299/103285/`** (MPEG-DASH profile; TS 103 285). Old `103100_103199/103168` tree 404s on ETSI.
- ETSI search prefers `/deliver/` links; avoids `etsi.org/technologies` hub as primary URL.
- **DVB-SI** (`etsi-en-300-468-dvb-si`) and **DVB subtitling** (`etsi-en-300-743-dvb-sub`): PDFs resolve at ingest from deliver version folders (same pattern as DVB-S2).

---

## Normalize behavior

- Inputs: manifest rows with `status: "ingested"` only.
- Formats: HTML pages, PDF (`pypdf`), single `.txt`, docx bundles from 3GPP zips, GitHub repo text bundles (capped files/size).
- Output: one `.txt` per record under `data/{authority}/normalized/`, index in `data/normalized/records.jsonl`.
- Stale normalized rows (no longer ingested) should be removed before re-chunking; `chunk.py` only reads `status == "normalized"`.

---

## Chunk behavior

- Reads `records.jsonl` where `status == "normalized"`.
- Strips manifest preamble, packs by `\n\n` paragraphs (`paragraph_pack_v1`).
- Chunk JSON: `chunk_id`, `text`, `source`, `external_id`, `authority`, `title`, `char_start`/`char_end`, etc.

---

## Embeddings and eval

| Path | Role |
|------|------|
| `data/embeddings/vectors.npy` | Normalized float32 matrix, one row per chunk |
| `data/embeddings/chunk_index.jsonl` | Row index → `chunk_id`, `external_id`, `authority`, `title` |
| `data/embeddings/meta.json` | Model id, dimensions, chunk count |
| `data/eval/queries.jsonl` | Eval queries with `expect_external_ids` |

Baseline eval (`eval_retrieval.py`, top-5): **6/7** pass — ISOBMFF query often ranks `MPEGGroup/FileFormatConformance` before `MPEGGroup/isobmff` (conformance repo is large in corpus).

---

## Core structural ingest status (`core_structural_syntax`)

| external_id | Status | Notes |
|-------------|--------|--------|
| `MPEGGroup/isobmff` | ingested | GitHub repo bundle |
| `MPEGGroup/FileFormatConformance` | ingested | Fixed: zip archive, not HTML-as-PDF |
| `etsi-dvb-dash-ts103168` | ingested | PDF via TS 103 285 deliver path |
| `etsi-en-300-468-dvb-si` | ingested | PDF at ingest |
| `etsi-en-300-743-dvb-sub` | ingested | PDF at ingest |
| `iso-iec-14496-12`, `iso-iec-14496-15`, `iso-iec-13818-1` | discovered | ISO.org portal only |
| `cta-cea-608`, `cta-cea-708` | discovered | CTA shop paywalled |

**Also ingested (structural, not all flagged core):** `w3c-tr-imsc11`, `w3c-tr-imsc13` (TR HTML), `etsi-dvb-s2-en302307`, `smpte-st-2110`, `AOMediaCodec/av1-spec`, `w3c/ttml1`, `w3c/imsc`.

---

## What works well (RAG-quality)

| Document | Notes |
|----------|--------|
| `etsi-dvb-dash-ts103168` | TS 103 285 PDF (~240 chunks) |
| `etsi-en-300-468-dvb-si`, `etsi-en-300-743-dvb-sub` | Full ETSI PDF text |
| `etsi-dvb-s2-en302307` | ~170k chars PDF (may show `discovered` in manifest if discover reset ingest — raw + normalized may still exist) |
| `w3c-tr-imsc11`, `w3c-tr-imsc13` | W3C TR HTML snapshots |
| `MPEGGroup/isobmff` | Reference SW text |
| `AOMediaCodec/av1-spec` | Large repo bundle (dominates chunk count if unfiltered) |
| `MPEGGroup/FileFormatConformance` | Repo bundle (large; conformance JSON/fixtures, not normative ISO) |

---

## Known gaps / pitfalls

1. **Re-run `discover.py`** can reset `status` to `discovered` when discovery fingerprint changes — re-`--reingest` affected IDs, then `normalize.py` → `chunk.py` → `embed.py`.
2. **Manifest vs corpus drift:** `records.jsonl` / `chunks.jsonl` can retain normalized text for rows no longer `ingested` in manifest — purge stale `external_id` rows before re-chunking.
3. **CTA CEA-608/708** — paywalled; `metadata.access=paywalled`.
4. **ISO.org** — portal HTML only unless using MPEGGroup GitHub repos.
5. **Transport bulk:** manifest may show many `ingested` transport rows; intentional catalog is 131 rows, not all normalized.
6. **Retrieval imbalance:** AV1 + FileFormatConformance dominate chunk/embed space; filter by `external_id` / `authority` for topic-focused RAG.
7. **ISOBMFF eval:** semantic search may prefer conformance repo over `isobmff` — consider metadata filters or separate indexes.
8. **PM_Catalog.md** can lag manifest until `discover.py` is re-run.
9. **Environment:** `sentence-transformers` + `torch` are heavy deps; first `embed.py` run downloads the HF model.

---

## Dependencies

```
requests, beautifulsoup4, lxml, pypdf, numpy, sentence-transformers
```

Setup: `python -m venv .venv && pip install -r requirements.txt`

---

## Git / PM workflow

- Remote: `https://github.com/GaliDev/vspp-rag.git`
- PM artifact: `PM_Catalog.md` (share after `discover.py`)
- Ingest on demand per authority/spec, not full catalog by default
- **Do not commit unless the user asks**; never run `git config` in agent sessions

---

## Suggested next work

1. **Manifest/corpus sync** — script or doc step to drop stale `records.jsonl` / re-chunk / re-embed after discover.
2. **Retrieval quality** — authority/`external_id` filters; fix ISOBMFF vs conformance ranking; optional Chroma/FAISS instead of flat `vectors.npy`.
3. **Remaining core structural** — ISO PDFs (paid), CTA captions (paywalled), optional DVB BlueBook PDFs from dvb.org.
4. Optional: sentence-aware chunking, portal chrome filtering, 3GPP bulk ingest strategy.

---

## Quick file map

| Path | Role |
|------|------|
| `discovery_manifest.json` | Source of truth for discovery + ingest state |
| `PM_Catalog.md` | Human/PM table view |
| `data/normalized/records.jsonl` | Normalize index |
| `data/chunks/chunks.jsonl` | RAG-ready chunks |
| `data/embeddings/vectors.npy` | Chunk embedding matrix |
| `data/embeddings/chunk_index.jsonl` | Embedding row → chunk metadata |
| `data/embeddings/meta.json` | Embedding model metadata |
| `data/eval/queries.jsonl` | Retrieval eval queries |
| `discover.py` | Discovery + ingest merge |
| `ingest.py` | Download + manifest updates |
| `embed.py` | Build embeddings |
| `eval_retrieval.py` | Run eval queries |
| `src/core/artifacts.py` | PDF/authority selection logic |
| `src/core/io.py` | Manifest I/O + ingest merge |
| `src/collectors/structural_system.py` | ETSI/ISO/DVB/W3C/SMPTE/CTA discovery |
