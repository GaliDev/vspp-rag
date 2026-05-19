# Confluence — API notes for collectors

## Cloud vs Data Center

| | Cloud | Data Center / Server |
|---|--------|----------------------|
| Base | `https://{site}.atlassian.net/wiki` | `http(s)://{host}:{port}/` e.g. `http://10.65.130.11:8090` |
| Auth | Email + API token (Basic) | Username + password or PAT (Basic) |
| List content | REST `/rest/api/content/search` + CQL | Same under `/rest/api/` |
| Network | Public internet | Often VPN / internal IP only |

**This project (planned):** Data Center at `http://10.65.130.11:8090`, allowlisted space keys `NGGUI`, `VP`, `PM` (NextGen GUI, VSPP Product, Product Manager).

## Authentication (Cloud)

1. Create API token: Atlassian account → Security → API tokens.
2. Basic auth: `base64("{email}:{api_token}")`.

Env: `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, `CONFLUENCE_SPACES`.

## Discovery — list spaces

```http
GET {base}/rest/api/space?limit=100
```

## Discovery — pages in a space (CQL)

```http
GET {base}/rest/api/content/search?cql=type=page AND space={SPACE}&limit=50&start=0
```

Example CQL for incremental runs:

```text
type=page AND space=VSPP AND lastModified >= "2025-01-01"
```

Paginate with `start` until `_links.next` is absent.

## Ingest — page body

```http
GET {base}/rest/api/content/{id}?expand=body.storage,version,space,ancestors
```

| Field | Use |
|-------|-----|
| `body.storage.value` | XHTML-like storage format → normalize with BS4 |
| `version.number` | `metadata.content_version` for incremental skip |
| `space.key` | Filter + manifest authority |
| `_links.webui` | Build `remote_url` with site base |

## Attachments (optional)

```http
GET {base}/rest/api/content/{id}/child/attachment
```

Download PDF/DOCX and route through existing `normalize.py` PDF/docx paths.

## DiscoveryRecord mapping

```python
DiscoveryRecord(
    source="confluence",
    authority=f"Confluence/{space_key}",
    title=page_title,
    external_id=f"confluence:{space_key}:{content_id}",
    version=str(version_number),
    published=last_modified_iso,
    remote_url=web_url,
    file_type="html",
    category="Internal",
    tier="system-level",
    metadata={
        "space_key": space_key,
        "page_id": content_id,
        "content_version": version_number,
        "labels": [...],
    },
)
```

## Rate limits

Confluence Cloud returns **429** with `Retry-After`. Throttle to ~1–5 req/s; exponential backoff.

## Export alternative (bulk)

Space export (HTML/XML ZIP) from Confluence UI → unpack → ingest as local artifacts (`ingest.py --local-artifact`) for one-time migration without API crawl.

## Out of scope v1

- Comments, likes, page restrictions API (use token visibility only)
- Confluence whiteboards / databases
