from __future__ import annotations

import argparse
from pathlib import Path

from src.core.retrieval import RetrievalFilters, load_index, route_query, search

ROOT = Path(__file__).parent
EMBED_DIR = ROOT / "data" / "embeddings"
CHUNKS_PATH = ROOT / "data" / "chunks" / "chunks.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Metadata-aware retrieval over chunk embeddings.")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--embed-dir", type=Path, default=EMBED_DIR)
    parser.add_argument("--chunks", type=Path, default=CHUNKS_PATH)
    parser.add_argument("--no-router", action="store_true", help="Disable keyword router")
    parser.add_argument("--core-structural-only", action="store_true")
    parser.add_argument("--authority", action="append", default=[], dest="authorities")
    parser.add_argument("--external-id", action="append", default=[], dest="external_ids")
    parser.add_argument("--category", action="append", default=[], dest="categories")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        dest="sources",
        help="Restrict to manifest source(s), e.g. ado_wiki",
    )
    parser.add_argument(
        "--exclude-external-id",
        action="append",
        default=[],
        dest="exclude_external_ids",
    )
    args = parser.parse_args()

    index = load_index(args.embed_dir, args.chunks)

    if args.no_router:
        filters = RetrievalFilters(
            authorities=frozenset(args.authorities),
            external_ids=frozenset(args.external_ids),
            categories=frozenset(args.categories),
            sources=frozenset(args.sources),
            core_structural_only=args.core_structural_only,
            exclude_external_ids=frozenset(args.exclude_external_ids),
        )
    else:
        filters = route_query(args.query)
        if args.authorities:
            filters = RetrievalFilters(
                authorities=frozenset(set(filters.authorities) | set(args.authorities)),
                external_ids=filters.external_ids,
                categories=filters.categories,
                sources=filters.sources,
                core_structural_only=filters.core_structural_only or args.core_structural_only,
                exclude_external_ids=filters.exclude_external_ids,
                exclude_categories=filters.exclude_categories,
            )
        if args.external_ids:
            filters = RetrievalFilters(
                authorities=filters.authorities,
                external_ids=frozenset(set(filters.external_ids) | set(args.external_ids)),
                categories=filters.categories,
                sources=filters.sources,
                core_structural_only=filters.core_structural_only or args.core_structural_only,
                exclude_external_ids=filters.exclude_external_ids,
                exclude_categories=filters.exclude_categories,
            )
        if args.categories:
            filters = RetrievalFilters(
                authorities=filters.authorities,
                external_ids=filters.external_ids,
                categories=frozenset(set(filters.categories) | set(args.categories)),
                sources=filters.sources,
                core_structural_only=filters.core_structural_only or args.core_structural_only,
                exclude_external_ids=filters.exclude_external_ids,
                exclude_categories=filters.exclude_categories,
            )
        if args.sources:
            filters = RetrievalFilters(
                authorities=filters.authorities,
                external_ids=filters.external_ids,
                categories=filters.categories,
                sources=frozenset(set(filters.sources) | set(args.sources)),
                core_structural_only=filters.core_structural_only or args.core_structural_only,
                exclude_external_ids=filters.exclude_external_ids,
                exclude_categories=filters.exclude_categories,
            )
        if args.exclude_external_ids:
            filters = RetrievalFilters(
                authorities=filters.authorities,
                external_ids=filters.external_ids,
                categories=filters.categories,
                sources=filters.sources,
                core_structural_only=filters.core_structural_only or args.core_structural_only,
                exclude_external_ids=frozenset(
                    set(filters.exclude_external_ids) | set(args.exclude_external_ids)
                ),
                exclude_categories=filters.exclude_categories,
            )
        if args.core_structural_only:
            filters = RetrievalFilters(
                authorities=filters.authorities,
                external_ids=filters.external_ids,
                categories=filters.categories,
                sources=filters.sources,
                core_structural_only=True,
                exclude_external_ids=filters.exclude_external_ids,
                exclude_categories=filters.exclude_categories,
            )

    hits = search(index, args.query, top_k=args.top_k, filters=filters)
    if not hits:
        print("No hits (empty filter mask).")
        return

    print(f"Query: {args.query}")
    if not filters.is_empty():
        print(f"Filters: {filters}")
    print()
    for n, hit in enumerate(hits, 1):
        preview = hit.text[:200].replace("\n", " ")
        cite = ""
        if hit.char_start is not None and hit.char_end is not None:
            cite = f" chars {hit.char_start}-{hit.char_end}"
        print(f"{n}. score={hit.score:.4f} {hit.external_id} ({hit.authority}){cite}")
        print(f"   {hit.title or ''}")
        print(f"   {preview}...\n")


if __name__ == "__main__":
    main()
