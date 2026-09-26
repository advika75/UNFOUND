"""Pooled, blind relevance judging.

Why: labels made by reading one config's results bias every later comparison
toward that config (anything it never surfaced is silently "irrelevant").
Pooling fixes the candidate side: for each query, the judgment pool is the
UNION of every config's top-N. Blinding fixes the judge side: pool items are
shown in a seeded random order with no rank, score, or config information.

Files (all under backend/eval/):
  pools/<name>.pool.json        what the judge sees: query text + shuffled ids ONLY
  pools/<name>.provenance.json  which config surfaced what, at which rank (never shown by `label`)
  labels/<name>.labels.json     the judge's grades; 0 = judged irrelevant, 1 = acceptable, 2 = exact
  querysets/<name>.json         `export` output: relevant (grade >= 1) + judged lists

Commands:
  python -m backend.eval.pool build  --queryset querysets/v1.json --name v2_pooled [--seed S]
  python -m backend.eval.pool label  --name v2_pooled            (resumable; skip = leave unjudged)
  python -m backend.eval.pool stats  --name v2_pooled
  python -m backend.eval.pool export  --name v2_pooled --source querysets/v1.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable

EVAL_DIR = Path(__file__).parent
POOLS_DIR = EVAL_DIR / "pools"
LABELS_DIR = EVAL_DIR / "labels"
QUERYSETS_DIR = EVAL_DIR / "querysets"

POOL_DEPTH = 20
DEFAULT_SEED = "unfound-pool-v1"

CONFIGS: dict[str, dict[str, Any]] = {
    "vector": {"retrieval_mode": "vector", "rrf_weights": None, "rerank_mode": "off"},
    "hybrid_1_1_off": {"retrieval_mode": "hybrid", "rrf_weights": None, "rerank_mode": "off"},
    "hybrid_2_1_off": {"retrieval_mode": "hybrid", "rrf_weights": [2.0, 1.0], "rerank_mode": "off"},
    "hybrid_1_1_structured": {"retrieval_mode": "hybrid", "rrf_weights": None, "rerank_mode": "structured"},
}

# Added later via `augment` (gated returns off's survivors re-ordered, so its top-20 can hold items
# that no earlier config ranked in its top-20).
EXTRA_CONFIGS: dict[str, dict[str, Any]] = {
    "vector_gated": {"retrieval_mode": "vector", "rrf_weights": None, "rerank_mode": "gated"},
    "hybrid_1_1_gated": {"retrieval_mode": "hybrid", "rrf_weights": None, "rerank_mode": "gated"},
    "hybrid_2_1_gated": {"retrieval_mode": "hybrid", "rrf_weights": [2.0, 1.0], "rerank_mode": "gated"},
}

GRADE_KEYS = {"2": 2, "1": 1, "0": 0}


# --- pure logic (unit tested) --------------------------------------------------

def build_query_pool(qid: str, results_by_config: dict[str, list[str]], *, seed: str, depth: int = POOL_DEPTH) -> dict[str, Any]:
    """Union of each config's top-`depth`, shuffled deterministically; provenance kept separately."""
    provenance: dict[str, dict[str, int]] = {}
    for config, ranked in results_by_config.items():
        for rank, product_id in enumerate(ranked[:depth], start=1):
            provenance.setdefault(product_id, {})[config] = rank
    ordered = sorted(provenance)  # sorted first so the shuffle never depends on config iteration order
    random.Random(f"{seed}:{qid}").shuffle(ordered)
    return {
        "items": ordered,
        "provenance": provenance,
        "per_config_returned": {config: min(len(ranked), depth) for config, ranked in results_by_config.items()},
    }


def merge_pool(
    existing_items: list[str], judged_ids: set[str], new_results_by_config: dict[str, list[str]],
    *, seed: str, qid: str, depth: int = POOL_DEPTH,
) -> dict[str, Any]:
    """Add newly surfaced items to an existing pool without disturbing what was already judged.

    Already-judged items keep their positions. Every not-yet-judged item, old or new, is re-shuffled
    together so late additions cannot be told apart by where they appear.
    """
    added = build_query_pool(qid, new_results_by_config, seed=seed, depth=depth)
    known = set(existing_items)
    fresh = [pid for pid in added["items"] if pid not in known]
    judged_part = [pid for pid in existing_items if pid in judged_ids]
    rest = [pid for pid in existing_items if pid not in judged_ids] + fresh
    rest = sorted(rest)
    random.Random(f"{seed}:aug:{qid}").shuffle(rest)
    return {"items": judged_part + rest, "provenance": added["provenance"], "added": fresh,
            "per_config_returned": added["per_config_returned"]}


def apply_judgment(judged: dict[str, int], product_id: str, key: str) -> dict[str, int]:
    """Record 0/1/2 explicitly (0 means 'looked and irrelevant', unlike label.py). Other keys change nothing."""
    if key not in GRADE_KEYS:
        return dict(judged)
    return {**judged, product_id: GRADE_KEYS[key]}


def judge_items(
    judged: dict[str, int],
    items: list[str],
    details: dict[str, dict[str, Any]],
    read_key: Callable[[], str],
    show: Callable[[str], None] = print,
) -> tuple[dict[str, int], bool]:
    """Walk not-yet-judged items in the given order. Returns (judged, quit_requested)."""
    updated = dict(judged)
    todo = [pid for pid in items if pid not in updated]
    for position, product_id in enumerate(todo, start=1):
        show(format_item(details.get(product_id, {"id": product_id}), position, len(todo)))
        while True:
            key = read_key()
            if key in ("q", "Q"):
                return updated, True
            if key in ("s", "S", "\n", "\r", ""):
                break
            if key in GRADE_KEYS:
                updated = apply_judgment(updated, product_id, key)
                break
            show(f"  unrecognized key {key!r} -- press 2, 1, 0, s (skip), or q (save and quit)")
    return updated, False


def format_item(product: dict[str, Any], position: int, total: int) -> str:
    price = product.get("price")
    price_text = f"₹{price:.0f}" if price else "no price"
    return (
        f"[{position}/{total}] {product.get('product_name')}\n"
        f"  brand={product.get('brand_name')} | category={product.get('category') or '(none)'}"
        f" | audience={product.get('audience') or product.get('category_audience') or '(none)'} | {price_text}\n"
        f"  {(product.get('description') or '')[:160]!r}\n"
        f"  image: {product.get('image_url') or '(none)'}\n"
        f"  [2=exactly right  1=acceptable  0=not relevant  s=skip  q=save & quit]"
    )


def queries_to_queryset(
    pool: dict[str, Any], labels: dict[str, Any], source: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Turn labels into queryset entries. Queries with nothing judged yet are omitted."""
    entries = []
    for qid, meta in pool["queries"].items():
        judged = labels.get("queries", {}).get(qid, {}).get("judged", {})
        if not judged:
            continue
        origin = source.get(qid, {})
        entry = {
            "id": qid,
            "query": meta["query"],
            "intent": origin.get("intent", {}),
            "relevant": {pid: grade for pid, grade in judged.items() if grade >= 1},
            "judged": sorted(judged),
        }
        if origin.get("notes"):
            entry["notes"] = origin["notes"]
        entries.append(entry)
    return entries


def pool_stats(pool: dict[str, Any], provenance: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    rows, judged_total, pool_total = [], 0, 0
    for qid, meta in pool["queries"].items():
        judged = labels.get("queries", {}).get(qid, {}).get("judged", {})
        done = sum(1 for pid in meta["items"] if pid in judged)
        judged_total += done
        pool_total += len(meta["items"])
        rows.append({"id": qid, "query": meta["query"], "pool_size": len(meta["items"]), "judged": done,
                     "relevant": sum(1 for g in judged.values() if g >= 1),
                     "per_config_returned": provenance.get(qid, {}).get("per_config_returned", {})})
    sizes = [r["pool_size"] for r in rows]
    return {
        "queries": len(rows), "pool_items_total": pool_total, "judged_total": judged_total,
        "mean_pool_size": sum(sizes) / len(sizes) if sizes else 0, "max_pool_size": max(sizes) if sizes else 0,
        "rows": rows,
    }


# --- file I/O -----------------------------------------------------------------

def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pool_paths(name: str) -> tuple[Path, Path, Path]:
    return POOLS_DIR / f"{name}.pool.json", POOLS_DIR / f"{name}.provenance.json", LABELS_DIR / f"{name}.labels.json"


# --- commands (network / terminal; not unit tested) ---------------------------

def _connect() -> tuple[Any, Any]:
    import os

    from dotenv import load_dotenv
    from sentence_transformers import SentenceTransformer

    load_dotenv("backend/.env", override=True)
    from backend.app import CLIP_MODEL_NAME, _get_required_env
    from backend.supabase_compat import create_supabase_client

    supabase = create_supabase_client(_get_required_env("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY") or _get_required_env("SUPABASE_KEY"))
    return supabase, SentenceTransformer(CLIP_MODEL_NAME)


def _build_command(args: argparse.Namespace) -> int:
    from backend import app

    supabase, model = _connect()
    source = json.loads(Path(args.queryset).read_text(encoding="utf-8"))
    pool_path, prov_path, _ = pool_paths(args.name)
    pool: dict[str, Any] = {"seed": args.seed, "depth": POOL_DEPTH, "configs": list(CONFIGS), "queries": {}}
    prov: dict[str, Any] = {}
    errors: list[str] = []

    for index, entry in enumerate(source, start=1):
        qid, text = entry["id"], entry["query"]
        embedding = app.encode_text(model, text)
        attributes = app.extract_query_attributes(text)
        by_config: dict[str, list[str]] = {}
        for config, options in CONFIGS.items():
            for attempt in (1, 2, 3):
                try:
                    app.SEARCH_CACHE.clear()
                    rows = app.run_match_products_rpc(
                        supabase, embedding, category_id=None, search_mode="text", query_attributes=attributes,
                        match_count=POOL_DEPTH, result_limit=POOL_DEPTH, query_text=text, **options,
                    )
                    by_config[config] = [str(r["id"]) for r in rows]
                    break
                except Exception as exc:  # transient network failures happened repeatedly in earlier runs
                    if attempt == 3:
                        errors.append(f"{qid}/{config}: {exc!r}")
                        by_config[config] = []
        built = build_query_pool(qid, by_config, seed=args.seed)
        pool["queries"][qid] = {"query": text, "items": built["items"]}
        prov[qid] = {"provenance": built["provenance"], "per_config_returned": built["per_config_returned"]}
        print(f"[{index}/{len(source)}] {qid} {text!r}: pool={len(built['items'])} {built['per_config_returned']}", flush=True)
        _write(pool_path, pool)
        _write(prov_path, {"errors": errors, "queries": prov})
    print(f"\nWrote {pool_path} and {prov_path} ({len(errors)} errors)")
    return 0


def _augment_command(args: argparse.Namespace) -> int:
    from backend import app

    wanted = args.configs.split(",")
    unknown = [c for c in wanted if c not in EXTRA_CONFIGS and c not in CONFIGS]
    if unknown:
        raise SystemExit(f"unknown configs: {unknown}")
    supabase, model = _connect()
    pool_path, prov_path, labels_path = pool_paths(args.name)
    pool, prov_file = _read(pool_path, None), _read(prov_path, {"errors": [], "queries": {}})
    labels = _read(labels_path, {"queries": {}})
    total_added = 0
    for qid, meta in pool["queries"].items():
        text = meta["query"]
        embedding, attributes = app.encode_text(model, text), app.extract_query_attributes(text)
        by_config: dict[str, list[str]] = {}
        for config in wanted:
            options = EXTRA_CONFIGS.get(config) or CONFIGS[config]
            for attempt in (1, 2, 3):
                try:
                    app.SEARCH_CACHE.clear()
                    rows = app.run_match_products_rpc(
                        supabase, embedding, category_id=None, search_mode="text", query_attributes=attributes,
                        match_count=POOL_DEPTH, result_limit=POOL_DEPTH, query_text=text, **options)
                    by_config[config] = [str(r["id"]) for r in rows]
                    break
                except Exception as exc:
                    if attempt == 3:
                        prov_file["errors"].append(f"{qid}/{config}: {exc!r}")
                        by_config[config] = []
        judged_ids = set(labels["queries"].get(qid, {}).get("judged", {}))
        merged = merge_pool(meta["items"], judged_ids, by_config, seed=pool["seed"], qid=qid)
        meta["items"] = merged["items"]
        entry = prov_file["queries"].setdefault(qid, {"provenance": {}, "per_config_returned": {}})
        for pid, ranks in merged["provenance"].items():
            entry["provenance"].setdefault(pid, {}).update(ranks)
        entry["per_config_returned"].update(merged["per_config_returned"])
        total_added += len(merged["added"])
        print(f"{qid}: +{len(merged['added'])} new items (pool now {len(meta['items'])})", flush=True)
    pool["configs"] = sorted(set(pool.get("configs", [])) | set(wanted))
    _write(pool_path, pool)
    _write(prov_path, prov_file)
    print(f"\nAdded {total_added} items across {len(pool['queries'])} queries")
    return 0


def _fetch_details(supabase: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    from backend.app import format_product
    from backend.category_quality import fetch_all

    names = {row["id"]: row for row in fetch_all(supabase, "categories")}
    details = {}
    for start in range(0, len(ids), 40):
        chunk = ids[start:start + 40]
        for row in supabase.table("products").select("*").in_("id", chunk).execute().data or []:
            category = names.get(row.get("category_id")) or {}
            details[str(row["id"])] = format_product({**row, "category_name": category.get("name"), "category_audience": category.get("audience")})
    return details


def _label_command(args: argparse.Namespace) -> int:
    from backend.eval.label import _read_single_key

    pool_path, _, labels_path = pool_paths(args.name)  # provenance file is deliberately never opened here
    pool = _read(pool_path, None)
    if pool is None:
        raise SystemExit(f"no pool at {pool_path}; run `build` first")
    labels = _read(labels_path, {"queries": {}})
    supabase, _model = None, None
    from dotenv import load_dotenv

    load_dotenv("backend/.env", override=True)
    import os

    from backend.app import _get_required_env
    from backend.supabase_compat import create_supabase_client

    supabase = create_supabase_client(_get_required_env("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY") or _get_required_env("SUPABASE_KEY"))

    only = set(args.only.split(",")) if args.only else None
    for qid, meta in pool["queries"].items():
        if only is not None and qid not in only:
            continue
        judged = labels["queries"].get(qid, {}).get("judged", {})
        remaining = [pid for pid in meta["items"] if pid not in judged]
        if not remaining:
            continue
        print(f"\n=== {qid}: \"{meta['query']}\"  ({len(remaining)} of {len(meta['items'])} left) ===\n")
        details = _fetch_details(supabase, remaining)
        judged, quit_now = judge_items(judged, meta["items"], details, _read_single_key)
        labels["queries"][qid] = {"query": meta["query"], "judged": judged}
        _write(labels_path, labels)
        if quit_now:
            break
    print(f"\nSaved to {labels_path}")
    return 0


def _stats_command(args: argparse.Namespace) -> int:
    pool_path, prov_path, labels_path = pool_paths(args.name)
    stats = pool_stats(_read(pool_path, {"queries": {}}), _read(prov_path, {"queries": {}})["queries"], _read(labels_path, {"queries": {}}))
    print(f"queries={stats['queries']} pool_items={stats['pool_items_total']} judged={stats['judged_total']} "
          f"mean_pool={stats['mean_pool_size']:.1f} max_pool={stats['max_pool_size']}")
    for row in stats["rows"]:
        print(f"  {row['id']:<6}{row['query'][:34]:<36}pool={row['pool_size']:<4}judged={row['judged']:<4}relevant={row['relevant']}")
    return 0


def _export_command(args: argparse.Namespace) -> int:
    pool_path, _, labels_path = pool_paths(args.name)
    source = {e["id"]: e for e in _read(Path(args.source), [])}
    entries = queries_to_queryset(_read(pool_path, {"queries": {}}), _read(labels_path, {"queries": {}}), source)
    out = QUERYSETS_DIR / f"{args.name}.json"
    _write(out, entries)
    print(f"Wrote {len(entries)} judged queries to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.eval.pool")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Run every config, write the blind pool + hidden provenance.")
    build.add_argument("--queryset", required=True)
    build.add_argument("--name", required=True)
    build.add_argument("--seed", default=DEFAULT_SEED)
    build.set_defaults(func=_build_command)
    augment = sub.add_parser("augment", help="Add more configs' top-N to an existing pool (judged items untouched).")
    augment.add_argument("--name", required=True)
    augment.add_argument("--configs", default=",".join(EXTRA_CONFIGS))
    augment.set_defaults(func=_augment_command)
    label = sub.add_parser("label", help="Blind, randomized, resumable judging.")
    label.add_argument("--name", required=True)
    label.add_argument("--only", default=None, help="Comma-separated query ids to judge (default: all).")
    label.set_defaults(func=_label_command)
    stats = sub.add_parser("stats", help="Pool depth and judged counts per query.")
    stats.add_argument("--name", required=True)
    stats.set_defaults(func=_stats_command)
    export = sub.add_parser("export", help="Write labels as a harness queryset.")
    export.add_argument("--name", required=True)
    export.add_argument("--source", required=True, help="Original queryset (for intent/notes).")
    export.set_defaults(func=_export_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
