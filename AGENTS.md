# AGENTS.md

## Cursor Cloud specific instructions

This is a Python CLI pipeline (no web server, no database, no frontend). All three phases run as standalone scripts from the repo root.

### Running the pipeline

Commands are documented in `README.md`. The three phases must run in order:

1. `python discover.py` — scrapes live APIs/websites, produces `discovery_manifest.json` + `PM_Catalog.md`
2. `python ingest.py --all --limit N --max-mb M` — downloads raw assets into `data/`; requires `discovery_manifest.json`
3. `python normalize.py --limit N` — converts ingested assets to plain text; requires ingested rows in manifest

### Gotchas

- Discovery requires outbound internet (HTTP/HTTPS). Results vary between runs because live endpoints change.
- `data/` is gitignored — ingestion and normalization artifacts are local only.
- There is no lint, test framework, or build step configured in this repo. Validation is done by running the scripts and checking output.
- IETF records have `file_type: "html"` and are skipped by default during ingestion unless `--include-pages` is passed.
- The GitHub API is used unauthenticated; if rate-limited, set a `GITHUB_TOKEN` env var (the code does not read it automatically — this would require a code change).
