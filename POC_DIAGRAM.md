# VSPP-RAG — POC (high level)

Local CLI pipeline: run steps **in order** (discover → ingest → normalize → chunk → embed → eval). No API, chat UI, or hosted vector DB.

> **One-shot runner:** [`run_pipeline.sh`](run_pipeline.sh) executes every phase in the same order. Example: `./run_pipeline.sh --source ado_wiki --limit 1 --summarize`.

**Preview:** Markdown: Open Preview (`Ctrl+Shift+V` / `Cmd+Shift+V`).

```mermaid
flowchart TB
    Sources["Sources<br/>IETF · 3GPP · GitHub · ETSI · ISO · W3C · SMPTE · DVB · CTA"]

    Discover["Discover<br/>discover.py<br/>Python 3 · asyncio · requests · BeautifulSoup · lxml<br/>collectors: ietf · threegpp · github · webdrafts · structural_system"]

    Manifest[("discovery_manifest.json<br/>PM_Catalog.md")]

    Ingest["Ingest<br/>ingest.py<br/>urllib / requests · zip extract · ETSI PDF crawl at ingest"]

    Raw[("data/{authority}/raw/")]

    Normalize["Normalize<br/>normalize.py<br/>pypdf · BS4 · WordprocessingML docx · repo file bundle"]

    Text[("data/{authority}/normalized/*.txt<br/>data/normalized/records.jsonl")]

    Summarize["Summarize optional<br/>normalize.py --summarize<br/>local LLM Qwen2.5-1.5B-Instruct · transformers + accelerate<br/>single-pass or map-reduce · idempotent via input_sha256"]

    SummaryMeta[("discovery_manifest.json<br/>metadata.doc_summary per row")]

    Chunk["Chunk<br/>chunk.py<br/>paragraph_pack_v1 · 2000 char / 200 overlap<br/>metadata: authority · external_id · category · tier"]

    Chunks[("data/chunks/chunks.jsonl")]

    Embed["Embed<br/>embed.py<br/>sentence-transformers/all-MiniLM-L6-v2 384-d<br/>NumPy float32 matrix"]

    Vectors[("data/embeddings/vectors.npy<br/>data/embeddings/chunk_index.jsonl<br/>data/embeddings/meta.json")]

    Eval["Eval optional<br/>eval_retrieval.py<br/>query embed · cosine similarity vs vectors.npy<br/>data/eval/queries.jsonl"]

    Sources --> Discover
    Discover --> Manifest
    Manifest --> Ingest
    Ingest --> Raw
    Raw --> Normalize
    Normalize --> Text
    Text -.-> Summarize
    Summarize -.-> SummaryMeta
    SummaryMeta -.-> Manifest
    Text --> Chunk
    Chunk --> Chunks
    Chunks --> Embed
    Embed --> Vectors
    Vectors --> Eval
    Chunks --> Eval
```

*Pipeline order is synchronous (manual CLI). Inside **Discover**, collectors run in parallel via `asyncio`, then one manifest is written.*

## Phases

| # | Step | Script | Output | Technology (POC) |
|---|------|--------|--------|------------------|
| 1 | Sources | — | — | Public standards sites and APIs |
| 2 | Discover | `discover.py` | `discovery_manifest.json`, `PM_Catalog.md` | Python 3 · `asyncio` · `requests` · BeautifulSoup · `lxml` |
| 3 | Ingest | `ingest.py` | `data/{authority}/raw/` | `urllib` / `requests` · zip extract · ETSI PDF crawl at ingest |
| 4 | Normalize | `normalize.py` | `*.txt`, `records.jsonl` | `pypdf` (PDF) · BS4 (HTML) · WordprocessingML (docx) · repo file bundle |
| 4b | Summarize (opt) | `normalize.py --summarize` | `metadata.doc_summary` in `discovery_manifest.json` | Local LLM **`Qwen/Qwen2.5-1.5B-Instruct`** · `transformers` · `accelerate` · single-pass or map-reduce · idempotent via `input_sha256` |
| 5 | Chunk | `chunk.py` | `chunks.jsonl` | Paragraph packing · 2000 char / 200 overlap · metadata on each chunk |
| 6 | Embed | `embed.py` | `vectors.npy`, `chunk_index.jsonl`, `meta.json` | **`sentence-transformers/all-MiniLM-L6-v2`** (384-d) · NumPy float32 matrix |
| 7 | Eval | `eval_retrieval.py` | stdout pass/fail | Query embed + **cosine similarity** vs `vectors.npy` · `queries.jsonl` |

`normalize.py` does **not** mutate the manifest unless `--summarize` is passed; with `--summarize`, it writes back `metadata.doc_summary` (atomic temp-file replace) per row.

## Dependencies (`requirements.txt`)

`requests` · `beautifulsoup4` · `lxml` · `pypdf` · `numpy` · `sentence-transformers` (pulls **PyTorch** for encoding) · `transformers` · `accelerate` (only used when `normalize.py --summarize` runs the local LLM).

## Scope today

- **Catalog:** ~131 standards listed; ingest is **selective** (not all rows downloaded).
- **Searchable corpus:** ingest → normalize → chunk → embed (~2–3k chunks typical).
- **Retrieval:** embedding similarity only; **no LLM answers**, reranker, or BM25.
- **Per-doc summaries (opt-in):** `normalize.py --summarize` runs a local instruct model and stores `metadata.doc_summary` (text + model + method + `input_sha256` + timestamp) inside `discovery_manifest.json`. Used for human review and as the seed for production-time doc-level routing.

## Not in POC

Answer generation, MCP/API, hosted vector DB, hybrid search, scheduled jobs, full-catalog bulk ingest. (Per-doc summaries above are opt-in; not on the embedding path yet.)

**Ops detail:** [`AGENT_HANDOFF.md`](AGENT_HANDOFF.md) · **Production target:** [`DIAGRAM.md`](DIAGRAM.md)
