# VSPP-Standards-Vault

Two-phase Technical Standards Ingestion Engine:

- Phase 1 (`discover.py`): discovers standards metadata and builds:
  - `discovery_manifest.json`
  - `PM_Catalog.md`
- Phase 2 (`ingest.py`): ingests assets on demand (`--source` or `--all`) and updates manifest status, local paths, and hashes.

## Sources

- IETF via Datatracker API (HLS, DASH, QUIC, RTP, WebRTC keyword topics)
- 3GPP via `ftp.3gpp.org` (`26_series` and `29_series` archives)
- GitHub trackers for W3C and AOM repositories
- Structural / broadcast / professional ingest (`structural_system` collector): ISO/IEC file-format and systems refs, ETSI DVB deliver trees + search fallback, W3C IMSC TRs + `w3c/imsc`, SMPTE listing resolution, CTA standards collection. Uses `requests` + BeautifulSoup (no Playwright required); portal-only rows are kept when a deliver path is not found.

Each manifest row includes `category` (`Transport` vs `Structural/System`), `tier` (`transport-level` vs `system-level`), and `publication_status` when the portal exposes it. Liaison-style links are logged under `metadata.liaison_or_attachment_links`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Discovery

```bash
python discover.py
```

This writes all discovered rows with `status: "discovered"`.

## Ingestion

```bash
python ingest.py --source 3gpp
python ingest.py --all
python ingest.py --source github --limit 5 --max-mb 100
python ingest.py --source iso --limit 1 --max-mb 5 --include-pages
```

Ingestion behavior:

- Downloads to `data/[authority]/raw/`
- Optional caps: `--limit` (max new ingestible rows per run) and `--max-mb` (per-file download cap). HTTP(S) uses `Content-Length` when available and streams with a hard byte limit.
- **IETF** discovery resolves `ietf.org/archive/id/...txt` artifacts (not only Datatracker HTML). **ISO** seeds add **MPEGGroup** GitHub reference repos where available. **ETSI/DVB** discovery tries to resolve direct `.pdf` links from deliver/listing pages.
- `--include-pages` enables lightweight HTML snapshots for `html`, `portal`, and `standard-page` rows (skipped for `metadata.access=paywalled` CTA shop pages unless you need catalog text).
- `--one-per-authority` picks the **best row per authority** (artifacts first). By default it **falls back to portal/TR HTML** when no artifact exists (`--no-page-fallback` to disable). At ingest time, ETSI deliver pages are crawled for `.pdf` links (including version subfolders).
- Zip archives (3GPP and others) are extracted under a sibling folder of the zip with bounded member count and total uncompressed size; paths are normalized to block zip-slip.
- GitHub `repository` rows download `https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip` (branch from manifest `version`, default `main`), with `metadata.ingest_archive_url` and `metadata.extracted_to` when unzip succeeds.
- For 3GPP zip archives, metadata still lists `.docx` paths in `docx_files`.
- Updates manifest records to `status: "ingested"` with `local_path` and `sha256`

## Normalization

```bash
python normalize.py --limit 10
python normalize.py --source iso --limit 1
python normalize.py --source github --max-files 200 --max-file-kb 512
```

Normalization behavior:

- Reads only manifest rows with `status: "ingested"`.
- Writes clean text artifacts next to each raw authority folder, e.g. `data/iso_iec/normalized/iso-iec-14496-12.txt`.
- Upserts a local global JSONL index at `data/normalized/records.jsonl`.
- HTML/page snapshots are converted with BeautifulSoup.
- Extracted GitHub repositories are bundled from text-like files (`.md`, `.txt`, `.xml`, `.html`, `.json`, etc.) with file-count and file-size caps.
- DOCX files inside extracted packages or ZIP archives are converted directly from WordprocessingML, so 3GPP packages can normalize without an extra dependency.
- PDF files (ETSI, SMPTE, etc.) are converted with `pypdf` (`normalizer: pdf_text_v1`). Use `--max-pdf-pages` to cap very large documents.
- Does not mutate `discovery_manifest.json`; normalized outputs are local because `data/` is gitignored.

## PM Workflow

1. Run `python discover.py` before PM reporting.
2. Share `PM_Catalog.md` as the current standards menu.
3. Trigger `ingest.py` only for requested authorities/specs.
4. Run `normalize.py` before chunking/indexing for RAG.

## Chunking and retrieval

```bash
python chunk.py
python embed.py
python eval_retrieval.py
```

- `chunk.py` writes `data/chunks/chunks.jsonl` (paragraph-aware packs).
- `embed.py` builds `data/embeddings/vectors.npy` and `chunk_index.jsonl` (MiniLM).
- `eval_retrieval.py` runs queries from `data/eval/queries.jsonl`.

## GitHub

Published as: [github.com/GaliDev/vspp-rag](https://github.com/GaliDev/vspp-rag)

```text
git remote add origin https://github.com/GaliDev/vspp-rag.git
git push -u origin main
```

(Remote may already be configured in this clone.) The `.gitignore` excludes `.venv/` and `data/`.
