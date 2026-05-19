# Azure DevOps wiki — API notes for collectors

## Base URL

```text
https://dev.azure.com/{organization}
```

**This project (planned):** `https://dev.azure.com/tm-vspp`, project `MK-VSPP` ([project home](https://dev.azure.com/tm-vspp/MK-VSPP)).

## Authentication

Personal Access Token (PAT) with scopes:

- **Wiki** — Read
- **Project and team** — Read (list projects/wikis)

```http
Authorization: Basic {base64(":{PAT}")}
```

Env: `ADO_ORG`, `ADO_PAT`, `ADO_WIKI_PROJECTS` (comma-separated).

## Endpoints (api-version=7.1)

### List wikis in a project

```http
GET https://dev.azure.com/{org}/{project}/_apis/wiki/wikis?api-version=7.1
```

### Page tree (full recursion)

```http
GET https://dev.azure.com/{org}/{project}/_apis/wiki/wikis/{wikiIdentifier}/pages?recursionLevel=full&api-version=7.1
```

### Page content

```http
GET https://dev.azure.com/{org}/{project}/_apis/wiki/wikis/{wikiIdentifier}/pages?path={path}&includeContent=true&api-version=7.1
```

Response fields useful for manifest:

| Field | Use |
|-------|-----|
| `path` | Stable id segment |
| `content` | Markdown body |
| `url` | `remote_url` |
| `gitItemPath` | Optional; clone path for git-based ingest alt |

## Alternative: git clone

ADO wikis are backed by a git repo. For bulk one-shot ingest:

```bash
git clone https://{PAT}@dev.azure.com/{org}/{project}/_git/{wiki}.wiki
```

Treat like GitHub `repository` ingest (`ingest_kind: repository_archive`) if REST pagination is awkward.

## DiscoveryRecord mapping

```python
DiscoveryRecord(
    source="ado_wiki",
    authority=f"ADO/{project}",
    title=page_title,
    external_id=f"ado:{project}:{wiki_id}:{path}",
    version=None,  # or etag if returned
    published=last_modified,
    remote_url=page_url,
    file_type="markdown",
    category="Internal",
    tier="system-level",
    metadata={
        "ado_org": org,
        "ado_project": project,
        "wiki_id": wiki_id,
        "wiki_path": path,
    },
)
```

## Rate limits

ADO REST is generally permissive; use modest concurrency (5–10) and retry on 429/503.

## Out of scope v1

- Work items (`/_apis/wit/wiql`)
- Repos (use separate collector if needed)
- Test plans / pipelines
