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

## Phase 0 — Decisions

| # | Item | Status | Choice | Notes |
|---|------|--------|--------|-------|
| 1 | Confluence deployment | **Done** | **Data Center / Server** | `http://10.65.130.11:8090` (HTTP, port 8090). Internal network / VPN only — not reachable from public CI. |
| 2 | Confluence scope | **Done** | Spaces: **DevOps**, **NG GUI**, **QA Automation** | See [space keys](#confluence-space-keys) below. CQL in v1: **no** (space list only). Denylist: **TBD**. |
| 3 | ADO scope | Pending | Org: `___`, projects: `___` | Wiki-only v1 |
| 4 | Auth | Pending | Service account + API token / PAT | Env vars only; never commit secrets |
| 5 | Manifest `category` | Pending | `Internal` (recommended) | Prefer new `Internal` for router |
| 6 | Corpus partition | Pending | Single index + metadata filters | Split indexes later if needed |
| 7 | Incremental sync | Pending | Version skip on discover; full re-embed OK for POC | Schedule: TBD |

### Decision 1 — Confluence host (locked)

| Field | Value |
|-------|--------|
| Deployment | Data Center / Server (self-hosted) |
| Base URL | `http://10.65.130.11:8090` |
| Context path | `/` (root; re-check if REST returns 404) |
| REST prefix | `http://10.65.130.11:8090/rest/api/` |

Ingest must run on a machine with route access to `10.65.130.11` (office LAN or VPN).

### Confluence space keys

You named three **spaces** (likely display names). The REST API and `CONFLUENCE_SPACES` env var use **space keys** (short codes in URLs), not display names.

| Display name (you provided) | Space key (verify in UI or API) |
|-----------------------------|----------------------------------|
| DevOps | Often `DEVOPS` or `DevOps` — check URL: `/display/DEVOPS/` or Space settings → Key |
| NG GUI | Often `NGGUI`, `NG`, or `NGGUI` — keys rarely contain spaces |
| QA Automation | Often `QA`, `QAA`, or `QAAUTOMATION` |

**How to verify:** open each space → **Space settings** → **Space details** → **Key**, or read the URL when browsing the space home page.

**Planned env (after keys confirmed):**

```bash
export CONFLUENCE_BASE_URL="http://10.65.130.11:8090"
export CONFLUENCE_SPACES="DEVOPS,NGGUI,QAAUTOMATION"   # placeholder — replace with real keys
```

Collector should call `GET /rest/api/space` once at setup to map display name → key if keys differ from guesses above.

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
# Confluence Data Center (internal)
export CONFLUENCE_BASE_URL="http://10.65.130.11:8090"
export CONFLUENCE_USER="..."                 # DC: username (or email if configured)
export CONFLUENCE_PASSWORD="..."             # or PAT from Confluence profile
export CONFLUENCE_SPACES="DEVOPS,NGGUI,QA"   # space keys — verify in Space settings

# Azure DevOps (TBD)
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
