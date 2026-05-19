# Internal docs ingestion — Confluence & Azure DevOps

Epic branch: `feature/internal-docs-confluence-ado`  
Status: **Planning** (no collectors implemented yet)

## Goal

Extend the VSPP Standards Vault pipeline so **private internal documentation** from **Confluence** and **Azure DevOps (ADO) wikis** is discoverable, ingested, chunked, embedded, and retrievable alongside public standards — with metadata filters so internal content does not drown out normative specs.

## Non-goals (v1)

- ADO work-item history, PR threads, or pipeline logs as primary corpus
- Confluence comments, inline macros beyond text extraction
- Per-user ACL enforcement in the vector index (token scope only at ingest; product auth later)
- Hosted vector DB or Ask API (follows existing POC path)

## Architecture fit

Same pipeline as public standards:

```text
discover.py  →  discovery_manifest.json
ingest.py    →  data/{authority}/raw/
normalize.py →  data/{authority}/normalized/*.txt
sync_corpus.py (optional prune)
chunk.py → embed.py → retrieve.py / eval_retrieval.py
```

New pieces:

| Component | Path (planned) |
|-----------|----------------|
| ADO wiki collector | `src/collectors/ado_wiki.py` |
| Confluence collector | `src/collectors/confluence.py` |
| Discovery wiring | `discover.py` (`asyncio.gather`) |
| Ingest | extend `ingest.py` or `ingest_internal.py` |
| Normalize | Markdown (ADO), HTML/storage (Confluence) |
| Retrieval | filters in `src/core/retrieval.py` (`source`, `space_key`, `ado_project`) |

## Phase 0 — Decisions (fill before implementation)

| Item | Choice | Notes |
|------|--------|-------|
| Confluence deployment | Cloud / Data Center | API base URL differs |
| Confluence scope | Space keys: `___` | CQL optional for date/label filters |
| ADO scope | Org: `___`, projects: `___` | Wiki-only v1 |
| Auth | Service account + API token / PAT | Env vars only; never commit secrets |
| Manifest `category` | `Internal` (new) or reuse `Structural/System` | Prefer new `Internal` for router |
| Corpus partition | Single index + metadata filters | Split indexes later if needed |
| Incremental sync | `metadata.content_version` / ADO etag | Full re-embed OK for POC scale |

## Phase 1 — ADO wiki (recommended first)

**Why first:** Markdown bodies, simpler than Confluence storage format.

### Tasks

- [ ] `discover_ado_wiki()` — list wikis and pages per configured project
- [ ] Manifest rows: `source=ado_wiki`, `external_id=ado:{project}:{wikiId}:{path}`
- [ ] Ingest: save `.md` under `data/ado_wiki/raw/`
- [ ] Normalize: markdown → plain text
- [ ] Chunk metadata: `ado_org`, `ado_project`, `wiki_path`
- [ ] Eval: 3–5 queries in `data/eval/queries.jsonl` with `filter_hints`
- [ ] CLI: `retrieve.py --source ado_wiki` (or router keywords)

See [ADO_WIKI_API.md](./ADO_WIKI_API.md).

## Phase 2 — Confluence

### Tasks

- [ ] `discover_confluence()` — CQL search per space, paginated
- [ ] Manifest: `external_id=confluence:{spaceKey}:{pageId}`
- [ ] Ingest: `body.storage` or export HTML → raw
- [ ] Normalize: BS4 (reuse existing HTML path)
- [ ] Incremental: skip if `version.number` unchanged
- [ ] Optional: attachment PDFs via child attachment API

See [CONFLUENCE_API.md](./CONFLUENCE_API.md).

## Phase 3 — Quality & ops

- [ ] Scheduled discover+ingest (cron / GitHub Actions with secrets)
- [ ] Document rebuild loop in `AGENT_HANDOFF.md`
- [ ] Exclude sensitive spaces by config denylist
- [ ] Partial re-embed (only changed `external_id`) when chunk count grows

## Environment variables (template)

```bash
# Confluence Cloud
export CONFLUENCE_BASE_URL="https://YOURCO.atlassian.net"
export CONFLUENCE_EMAIL="bot@yourco.com"
export CONFLUENCE_API_TOKEN="..."          # from Atlassian account settings
export CONFLUENCE_SPACES="VSPP,ARCH"       # comma-separated space keys

# Azure DevOps
export ADO_ORG="yourorg"
export ADO_PAT="..."                         # Wiki (Read) + Project (Read)
export ADO_WIKI_PROJECTS="VSPP-Platform"   # comma-separated project names
```

Copy to `.env` locally; add `.env` to `.gitignore` if not already covered.

## Security & compliance

1. Confirm with security/legal that internal wiki content may be embedded and stored on ingest host/cloud.
2. Use read-only tokens; dedicated bot account, not personal PAT in production.
3. Denylist spaces/projects that contain credentials or HR content.
4. Do not commit exports, tokens, or full HTML dumps to git (`data/` remains gitignored).

## PR sequence (suggested)

```text
main
 └── feature/internal-docs-confluence-ado     ← this plan (merged when ready)
       ├── feature/ado-wiki-collector       → PR 1
       ├── feature/confluence-collector     → PR 2
       └── feature/internal-retrieval-filters → PR 3
```

## References

- Existing pipeline: [AGENT_HANDOFF.md](../AGENT_HANDOFF.md), [POC_DIAGRAM.md](../POC_DIAGRAM.md)
- Production target: [DIAGRAM.md](../DIAGRAM.md)
