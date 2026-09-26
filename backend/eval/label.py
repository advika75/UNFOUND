"""Interactive terminal tool for labeling offline relevance query sets.

    python -m backend.eval.label search --queryset backend/eval/querysets/v1.json \\
        --id q001 --query "black oversized top"

    python -m backend.eval.label browse --queryset backend/eval/querysets/v1.json \\
        --id q001 --category "Women Tops" --type top --colour black

`search` runs the query through the exact same search seam the harness
itself scores (backend.app.encode_text -> extract_query_attributes ->
run_match_products_rpc) and shows the top 30 results one at a time: id,
product name, brand, category, audience, price, image URL. Press 2 / 1 / 0
to grade each (2 = exactly right, 1 = acceptable/adjacent, 0 = explicitly
not relevant -- which removes it from the map, since absence already means
irrelevant), s to skip without grading, q to stop early. Grades are written
back into the query set's `relevant` map for that query id after every
session.

*** Known bias in `search`: the top-30 candidate pool ***
This flow can only label what the CURRENT ranker chose to surface. If a
genuinely relevant product exists in the catalog but the ranker's
embedding/keyword scoring buried it outside the top 30, or missed it
entirely because of a colour/category misclassification (exactly the class
of bug the Stage 9 audit found), `search` will never show it to you, and it
can therefore never be marked relevant. A query set built ONLY this way
would silently define "relevant" as "whatever today's ranker already
returns" -- which makes recall against the ranker's actual blind spots
structurally unmeasurable: the harness would look artificially perfect at
recall while being completely blind to the ranker's worst failure mode
(missing a relevant product entirely, as opposed to ranking it too low).

`browse` exists specifically to counter this. It bypasses ranking and the
embedding search entirely, filtering the raw catalog by category substring,
taxonomy product-type, and/or colour substring, so you can find and label
products the ranker never returned in the first place. Use `browse` for any
query where you have a specific product in mind that `search`'s top 30
didn't surface -- that is precisely the case `search` cannot detect on its
own, since from inside the ranked list there is no signal that anything is
missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

GRADE_KEYS = {"2": 2, "1": 1, "0": 0}
SKIP_KEYS = {"s", "S", "\n", "\r", ""}
QUIT_KEYS = {"q", "Q"}

DEFAULT_SEARCH_TOP_N = 30
DEFAULT_BROWSE_LIMIT = 50

ReadKeyFn = Callable[[], str]
ShowFn = Callable[[str], None]


# --- query set file I/O (raw dict form, preserves fields this tool doesn't touch) --

def load_raw_queryset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_raw_queryset(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_entry(entries: list[dict[str, Any]], case_id: str) -> dict[str, Any] | None:
    return next((entry for entry in entries if entry.get("id") == case_id), None)


def upsert_relevant(
    entries: list[dict[str, Any]],
    case_id: str,
    relevant: dict[str, int],
    *,
    query: str | None = None,
    intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a new entries list with case_id's `relevant` map replaced.

    If case_id already exists, only `relevant` (and `query`, if a new value
    is given) is touched -- `intent`/`notes` are left exactly as they were.
    If case_id does not exist yet, `query` is required to create it.
    """
    updated = [dict(entry) for entry in entries]
    existing = find_entry(updated, case_id)
    if existing is None:
        if not query:
            raise ValueError(f"query id '{case_id}' does not exist in this query set yet -- pass --query to create it")
        updated.append({"id": case_id, "query": query, "intent": intent or {}, "relevant": dict(relevant)})
    else:
        existing["relevant"] = dict(relevant)
        if query:
            existing["query"] = query
    return updated


# --- grading -----------------------------------------------------------------------

def apply_grade(relevant: dict[str, int], product_id: str, key: str) -> dict[str, int]:
    """Return an updated copy of `relevant` after grading one product with one keypress.

    '2'/'1' set that grade. '0' explicitly marks not-relevant, which means
    REMOVING the entry (absence already means irrelevant in this schema, so
    there is nothing else to store). Any other key is a no-op.
    """
    updated = dict(relevant)
    if key == "2":
        updated[product_id] = 2
    elif key == "1":
        updated[product_id] = 1
    elif key == "0":
        updated.pop(product_id, None)
    return updated


def format_result_line(product: dict[str, Any], index: int, total: int, current_grade: int | None) -> str:
    grade_note = f" [current grade: {current_grade}]" if current_grade is not None else ""
    price = product.get("price")
    price_text = f"₹{price:.0f}" if price else "no price"
    return (
        f"[{index}/{total}]{grade_note} id={product.get('id')}\n"
        f"  {product.get('product_name')} | brand={product.get('brand_name')}"
        f" | category={product.get('category') or '(none)'}"
        f" | audience={product.get('audience') or '(none)'} | {price_text}\n"
        f"  image: {product.get('image_url') or '(none)'}\n"
        f"  grade? [2=exact 1=adjacent 0=not-relevant s=skip q=stop]"
    )


def label_results(
    relevant: dict[str, int],
    results: list[dict[str, Any]],
    read_key: ReadKeyFn,
    show: ShowFn = print,
) -> dict[str, int]:
    """Walk `results` one at a time, grading each via `read_key`. Returns the updated map.

    `read_key`/`show` are injected so this loop is testable with a scripted
    key sequence instead of a real terminal -- no I/O of its own.
    """
    updated = dict(relevant)
    total = len(results)
    for index, product in enumerate(results, start=1):
        product_id = str(product.get("id"))
        current = updated.get(product_id)
        show(format_result_line(product, index, total, current))
        while True:
            key = read_key()
            if key in QUIT_KEYS:
                return updated
            if key in SKIP_KEYS:
                break
            if key in GRADE_KEYS:
                updated = apply_grade(updated, product_id, key)
                break
            show(f"  unrecognized key {key!r} -- press 2, 1, 0, s (skip), or q (stop)")
    return updated


def _read_single_key() -> str:
    """Read exactly one keypress from the real terminal, no Enter required.

    Falls back to line-buffered input() when stdin isn't a real tty (piped
    input, some CI/non-interactive contexts) so the tool degrades instead of
    crashing -- the fallback just requires pressing Enter after the key.
    """
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception:
        return (input("> ").strip() or "s")[:1]


# --- catalog browsing (bypasses ranking; see module docstring for why) ------------

def filter_catalog_rows(
    rows: list[dict[str, Any]],
    *,
    category_contains: str | None = None,
    product_type: str | None = None,
    colour: str | None = None,
    limit: int = DEFAULT_BROWSE_LIMIT,
) -> list[dict[str, Any]]:
    """Pure filter over raw product rows (as fetch_all(supabase, "products") returns them).

    No ranking, no embeddings -- a plain substring/taxonomy match, so it can
    surface catalog products the ranker's search path never returns at all.
    """
    from backend.app import product_search_text
    from backend.product_taxonomy import type_match

    matches: list[dict[str, Any]] = []
    for row in rows:
        if row.get("catalog_status") == "NON_PRODUCT":
            continue
        text = product_search_text(row)
        if category_contains and category_contains.lower() not in (row.get("category") or "").lower():
            continue
        if product_type and not type_match(text, product_type):
            continue
        if colour and colour.lower() not in text:
            continue
        matches.append(row)
        if len(matches) >= limit:
            break
    return matches


def browse_catalog(
    supabase: Any,
    *,
    category_contains: str | None = None,
    product_type: str | None = None,
    colour: str | None = None,
    limit: int = DEFAULT_BROWSE_LIMIT,
) -> list[dict[str, Any]]:
    from backend.app import format_product
    from backend.category_quality import fetch_all

    rows = fetch_all(supabase, "products")
    filtered = filter_catalog_rows(
        rows, category_contains=category_contains, product_type=product_type, colour=colour, limit=limit,
    )
    return [format_product(row) for row in filtered]


# --- CLI wiring (DB/model-touching; not unit tested, mirrors cli.py's split) ------

def _search_command(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv("backend/.env", override=True)

    from sentence_transformers import SentenceTransformer

    from backend.app import CLIP_MODEL_NAME, _get_required_env, encode_text, extract_query_attributes, run_match_products_rpc
    from backend.supabase_compat import create_supabase_client

    queryset_path = Path(args.queryset)
    entries = load_raw_queryset(queryset_path)
    existing = find_entry(entries, args.id)
    query_text = args.query or (existing.get("query") if existing else None)
    if not query_text:
        raise SystemExit(f"query id '{args.id}' does not exist yet -- pass --query to create it")

    supabase = create_supabase_client(
        _get_required_env("SUPABASE_URL"),
        os.getenv("SUPABASE_ANON_KEY") or _get_required_env("SUPABASE_KEY"),
    )
    clip_model = SentenceTransformer(CLIP_MODEL_NAME)

    query_embedding = encode_text(clip_model, query_text)
    query_attributes = extract_query_attributes(query_text)
    results = run_match_products_rpc(
        supabase,
        query_embedding,
        category_id=None,
        search_mode="text",
        query_attributes=query_attributes,
        match_count=max(args.top_n, DEFAULT_SEARCH_TOP_N),
        result_limit=args.top_n,
    )

    print(f"\nLabeling '{query_text}' -- top {len(results)} current search results.\n")
    current_relevant = dict(existing.get("relevant", {})) if existing else {}
    updated_relevant = label_results(current_relevant, results, _read_single_key)

    entries = upsert_relevant(entries, args.id, updated_relevant, query=query_text)
    save_raw_queryset(queryset_path, entries)
    print(f"\nSaved {len(updated_relevant)} graded product(s) for '{args.id}' to {queryset_path}")
    return 0


def _browse_command(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv("backend/.env", override=True)

    from backend.app import _get_required_env
    from backend.supabase_compat import create_supabase_client

    queryset_path = Path(args.queryset)
    entries = load_raw_queryset(queryset_path)
    existing = find_entry(entries, args.id)
    if existing is None:
        raise SystemExit(f"query id '{args.id}' does not exist yet -- run `search` first to create it")

    supabase = create_supabase_client(
        _get_required_env("SUPABASE_URL"),
        os.getenv("SUPABASE_ANON_KEY") or _get_required_env("SUPABASE_KEY"),
    )
    results = browse_catalog(
        supabase,
        category_contains=args.category,
        product_type=args.type,
        colour=args.colour,
        limit=args.limit,
    )
    if not results:
        print("No catalog products matched those filters.")
        return 0

    print(f"\nBrowsing catalog for '{args.id}' -- {len(results)} match(es) found directly (ranking bypassed).\n")
    current_relevant = dict(existing.get("relevant", {}))
    updated_relevant = label_results(current_relevant, results, _read_single_key)

    entries = upsert_relevant(entries, args.id, updated_relevant)
    save_raw_queryset(queryset_path, entries)
    print(f"\nSaved {len(updated_relevant)} graded product(s) for '{args.id}' to {queryset_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.eval.label")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Label the top-N current search results for a query.")
    search_parser.add_argument("--queryset", required=True)
    search_parser.add_argument("--id", required=True, help="Query id, e.g. q001.")
    search_parser.add_argument("--query", default=None, help="Query text (required to create a new id).")
    search_parser.add_argument("--top-n", type=int, default=DEFAULT_SEARCH_TOP_N, dest="top_n")
    search_parser.set_defaults(func=_search_command)

    browse_parser = subparsers.add_parser("browse", help="Filter the catalog directly, bypassing ranking, to find products search misses.")
    browse_parser.add_argument("--queryset", required=True)
    browse_parser.add_argument("--id", required=True, help="Existing query id to attach labels to.")
    browse_parser.add_argument("--category", default=None, help="Category substring filter.")
    browse_parser.add_argument("--type", default=None, help="Taxonomy product-type filter, e.g. 'kurti'.")
    browse_parser.add_argument("--colour", default=None, help="Colour substring filter.")
    browse_parser.add_argument("--limit", type=int, default=DEFAULT_BROWSE_LIMIT)
    browse_parser.set_defaults(func=_browse_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
