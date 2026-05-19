# Internal docs ingestion — Confluence & Azure DevOps

Epic branch: `feature/internal-docs-confluence-ado`  
Status: **In progress** (Phase 1 ADO wiki collector implemented)

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
| 2 | Confluence scope | **Done** | **Option A — allowlist only** | Space keys: **`NGGUI`**, **`VP`**, **`PM`** (not DevOps / QA Automation). CQL in v1: **no**. |
| 3 | ADO scope | **Done** | Org **`tm-vspp`**, project **`MK-VSPP`** | [Azure DevOps](https://dev.azure.com/tm-vspp/MK-VSPP). Wiki-only v1; allowlist (single project). |
| 4 | Auth | **Done** | **Option A — personal credentials (POC)** | Your Confluence user/password (or PAT) + your ADO PAT in `.env`. Bot/service account later for production. |
| 5 | Manifest `category` | **Done** | **`Internal`** | Confluence + ADO wiki chunks; `tier=system-level`. Router/filter excludes or includes vs Transport / Structural/System. |
| 6 | Corpus partition | **Done** | **Single index**; default retrieval **standards only** | One `vectors.npy`; exclude `category=Internal` by default. `--category Internal` or `--include-internal` for wiki docs. |
| 7 | Incremental sync | **Done** | **Incremental discover; full re-embed; manual runs** | Skip unchanged pages on discover; full `embed.py` each pipeline run; no cron until Phase 3. |

### Decision 1 — Confluence host (locked)

| Field | Value |
|-------|--------|
| Deployment | Data Center / Server (self-hosted) |
| Base URL | `http://10.65.130.11:8090` |
| Context path | `/` (root; re-check if REST returns 404) |
| REST prefix | `http://10.65.130.11:8090/rest/api/` |

Ingest must run on a machine with route access to `10.65.130.11` (office LAN or VPN).

### Decision 2 — Confluence scope (locked: allowlist)

**Chosen:** **Option A** — ingest only the three spaces below.  
**Not chosen:** all spaces (B) or all spaces minus denylist (C).

**Rationale:** smaller corpus, clearer security story, less risk of drowning standards retrieval.

### Confluence space keys (confirmed)

| Space key | Display name (Confluence UI) |
|-----------|----------------------------|
| `NGGUI` | NextGen GUI |
| `VP` | VSPP Product |
| `PM` | Product Manager |

**Out of scope:** DevOps, QA Automation (removed from allowlist).

**Env:**

```bash
export CONFLUENCE_BASE_URL="http://10.65.130.11:8090"
export CONFLUENCE_SPACES="NGGUI,VP,PM"
```

Collector must only discover pages in these three keys. URLs often look like `/display/NGGUI/...`, `/display/VP/...`, `/display/PM/...`.

### Decision 3 — ADO scope (locked: single project)

From project URL `https://dev.azure.com/tm-vspp/MK-VSPP`:

| Field | Value |
|-------|--------|
| Organization | `tm-vspp` |
| Project(s) | `MK-VSPP` (only project in v1) |
| API base | `https://dev.azure.com/tm-vspp` |
| Wiki API example | `GET https://dev.azure.com/tm-vspp/MK-VSPP/_apis/wiki/wikis?api-version=7.1` |

**Scope:** project wiki pages only (not work items, repos, or other org projects unless added later).

**Planned env:**

```bash
export ADO_ORG="tm-vspp"
export ADO_WIKI_PROJECTS="MK-VSPP"
export ADO_PAT="..."   # Wiki (Read) + Project and team (Read)
```

### Decision 4 — Auth (locked: personal POC)

**Chosen:** **Option A** — use **your own** credentials for the first end-to-end test on a VPN-connected machine.

| System | POC (now) | Production (later) |
|--------|-----------|---------------------|
| Confluence | `CONFLUENCE_USER` + `CONFLUENCE_PASSWORD` (or PAT as password) | Dedicated read-only bot user |
| ADO | Personal PAT (`ADO_PAT`) with Wiki Read + Project Read | Service account PAT with same scopes |

**Rules:** store only in `.env` (gitignored); read-only PAT/scopes; token sees only what your user can read in the three Confluence spaces + MK-VSPP wiki.

**Not required for POC:** security sign-off, shared bot account, SSO integration.

### Decision 5 — Manifest category (locked)

**Chosen:** new category **`Internal`** (not mixed into `Structural/System`).

| Field | Value |
|-------|--------|
| `category` | `Internal` |
| `tier` | `system-level` (same as other system docs) |
| `source` | `confluence` or `ado_wiki` |

Collectors set this on manifest rows; `normalize` → `chunk` → `embed` propagate it.

### Decision 6 — Corpus & default retrieval (locked)

**Index:** single embedding index (same as today — one `vectors.npy` + metadata).

**Default retrieval — Option A:** search **standards only**; **exclude** chunks with `category=Internal` unless the user opts in.

| User intent | CLI (planned) |
|-------------|----------------|
| Normative / standards (default) | `python retrieve.py "DASH MPD"` — masks out `Internal` |
| Internal wiki only | `python retrieve.py "deploy MK-VSPP" --category Internal` |
| Both corpora | `python retrieve.py "..." --include-internal` |

Implementation: extend `RetrievalFilters` with `exclude_categories` or default denylist `Internal` when no `--include-internal`. Split vector DB deferred until corpus is large.

### Decision 7 — Incremental sync (locked)

| Aspect | POC choice |
|--------|------------|
| **Discover** | **Yes** — skip Confluence pages when `version.number` unchanged; track ADO page version/etag in manifest metadata |
| **Re-embed** | **Full rebuild** — run `embed.py` over all chunks after each ingest cycle (fine until internal corpus is large) |
| **Schedule** | **Manual** — run discover → ingest → normalize → sync → chunk → embed when you choose; automated cron in Phase 3 |

**Typical manual refresh:**

```bash
python discover.py          # includes ado_wiki + confluence when implemented
python ingest.py ...
python normalize.py && python sync_corpus.py --prune
python chunk.py && python embed.py
```

---

**Phase 0 complete.** All seven decisions locked; ready for Phase 1 (ADO wiki collector) implementation.

## Phase 1 — ADO wiki (recommended first)

**Why first:** Markdown bodies, simpler than Confluence storage format.

### Tasks

- [x] `discover_ado_wiki()` — list wikis and pages per configured project
- [x] Manifest rows: `source=ado_wiki`, `external_id=ado:{project}:{wikiId}:{path}`
- [x] Ingest: save `.md` under `data/ado_mk-vspp/raw/` (authority `ADO/MK-VSPP`)
- [x] Normalize: markdown → plain text
- [x] Chunk metadata: `ado_org`, `ado_project`, `wiki_path`
- [x] Eval: 3 queries in `data/eval/queries.jsonl` with `filter_hints` / prefix match
- [x] CLI: `retrieve.py --source ado_wiki` (or router keywords)

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
export CONFLUENCE_SPACES="NGGUI,VP,PM"

# Azure DevOps
export ADO_ORG="tm-vspp"
export ADO_PAT="..."                         # Wiki (Read) + Project and team (Read)
export ADO_WIKI_PROJECTS="MK-VSPP"
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
