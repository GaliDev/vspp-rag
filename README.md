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
```

Ingestion behavior:

- Downloads to `data/[authority]/raw/`
- For 3GPP zip archives, extracts contents and tracks `.docx` files in metadata
- Updates manifest records to `status: "ingested"` with `local_path` and `sha256`

## PM Workflow

1. Run `python discover.py` before PM reporting.
2. Share `PM_Catalog.md` as the current standards menu.
3. Trigger `ingest.py` only for requested authorities/specs.

