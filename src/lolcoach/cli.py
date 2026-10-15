"""Command-line entrypoint.

    python -m lolcoach.cli serve
    python -m lolcoach.cli fetch [--limit N]
    python -m lolcoach.cli build-kg [--patch X]
    python -m lolcoach.cli kg-report [--out PATH]
    python -m lolcoach.cli reset (--all | --executions | --chat)
    python -m lolcoach.cli demo
    python -m lolcoach.cli benchmark --n 200
    python -m lolcoach.cli scan-executions

This is an entrypoint, so it configures logging itself (mirroring ``main.py``).
Commands whose milestone has not landed exit with a clear message rather than
failing obscurely.
"""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import Sequence
from pathlib import Path

from lolcoach.config import get_settings, load_env
from lolcoach.logging_setup import configure_logging

logger = logging.getLogger(__name__)

_EXIT_OK = 0
_EXIT_NOT_IMPLEMENTED = 2
_SEED_DIR = Path(__file__).parent / "knowledge" / "seed"


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    logger.info(
        "Starting API server",
        extra={"data": {"host": args.host, "port": args.port, "reload": args.reload}},
    )
    uvicorn.run("lolcoach.main:app", host=args.host, port=args.port, reload=args.reload)
    return _EXIT_OK


def _cmd_fetch(args: argparse.Namespace) -> int:
    from lolcoach.dataprovider import store
    from lolcoach.dataprovider.fetch import fetch_snapshot
    from lolcoach.dataprovider.opgg_mcp import OpggMcpClient

    settings = get_settings()
    conn = store.connect(settings.snapshot_db)
    try:
        store.init_db(conn)
        logger.info(
            "Fetching OP.GG snapshot",
            extra={"data": {"limit": args.limit, "interval_s": settings.fetch_interval}},
        )
        with OpggMcpClient(settings.opgg_mcp_url) as client:
            report = fetch_snapshot(
                client, conn, interval=settings.fetch_interval, limit=args.limit
            )
        print(
            "fetched: "
            f"{report.champions} champions, {report.stat_rows} stat rows, "
            f"{report.matchup_rows} matchups, {report.synergy_rows} synergies "
            f"({report.analyzed_pairs} pairs analyzed, {report.skipped_pairs} skipped, "
            f"{report.errors} errors), "
            f"{report.reference_rows} reference rows, {report.build_rows} build rows, "
            f"{report.rune_rows} rune rows, {report.item_rows} items"
        )
    finally:
        conn.close()
    return _EXIT_OK


def _cmd_build_kg(args: argparse.Namespace) -> int:
    from lolcoach.dataprovider import store
    from lolcoach.dataprovider.build_kg import build_knowledge_graph

    settings = get_settings()
    if not settings.snapshot_db.exists():
        print(f"snapshot not found at {settings.snapshot_db}; run `fetch` first")
        return 1
    conn = store.connect(settings.snapshot_db)
    try:
        counts = build_knowledge_graph(
            conn, _SEED_DIR, patch=args.patch, ddragon_version=_ddragon_version(args)
        )
    finally:
        conn.close()
    print(
        f"knowledge graph written to {_SEED_DIR}: "
        f"{counts['champion_count']} champions, {counts['relation_count']} relations "
        f"({counts['counter_count']} counters, {counts['synergy_count']} synergies)"
    )
    return _EXIT_OK


def _ddragon_version(args: argparse.Namespace) -> str:
    """Data Dragon CDN version for champion portraits.

    The KG patch label (``14.20``) is not a CDN version (``14.20.1``); default to
    the patch's first build and allow an explicit override.
    """
    if args.ddragon:
        return str(args.ddragon)
    if re.fullmatch(r"\d+\.\d+", args.patch):
        return f"{args.patch}.1"
    return ""


def _cmd_kg_report(args: argparse.Namespace) -> int:
    from lolcoach.knowledge.ontology import load_ontology
    from lolcoach.knowledge.report import write_report

    ontology = load_ontology()
    out = Path(args.out) if args.out else get_settings().data_dir / "kg-report.html"
    write_report(ontology, out)
    print(
        f"knowledge-graph report written to {out}: "
        f"{len(ontology.champions)} champions, {len(ontology.relations)} relations "
        "-- open it in a browser"
    )
    return _EXIT_OK


def _cmd_reset(args: argparse.Namespace) -> int:
    settings = get_settings()
    data_dir = settings.data_dir.resolve()
    targets = []
    if args.all:
        targets = [settings.snapshot_db, settings.executions_db, settings.chat_db]
    elif args.executions:
        targets = [settings.executions_db]
    elif args.chat:
        targets = [settings.chat_db]

    for base in targets:
        resolved = base.resolve()
        if data_dir not in resolved.parents and resolved != data_dir:
            raise SystemExit(f"refusing to remove path outside data dir: {resolved}")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(resolved) + suffix)
            if candidate.exists():
                candidate.unlink()
                logger.info("Removed runtime file", extra={"data": {"path": str(candidate)}})
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"reset complete: removed {len(targets)} database(s)")
    return _EXIT_OK


def _not_implemented(command: str) -> int:
    logger.warning("Command not implemented yet", extra={"data": {"command": command}})
    print(f"'{command}' is not implemented yet (planned in a later milestone).")
    return _EXIT_NOT_IMPLEMENTED


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lolcoach", description="LoL Coach CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the FastAPI server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=_cmd_serve)

    p_fetch = sub.add_parser("fetch", help="pull OP.GG data into the snapshot")
    p_fetch.add_argument("--limit", type=int, default=None, help="max champions to analyze")
    p_fetch.set_defaults(func=_cmd_fetch)

    p_kg = sub.add_parser("build-kg", help="build the YAML knowledge graph from the snapshot")
    p_kg.add_argument("--patch", default="unknown", help="patch label recorded in patches.yaml")
    p_kg.add_argument(
        "--ddragon",
        default="",
        help="Data Dragon CDN version for portraits (default: <patch>.1 when patch is X.Y)",
    )
    p_kg.set_defaults(func=_cmd_build_kg)

    p_report = sub.add_parser(
        "kg-report", help="render the knowledge graph as a static HTML review page"
    )
    p_report.add_argument("--out", default=None, help="output path (default data/kg-report.html)")
    p_report.set_defaults(func=_cmd_kg_report)

    p_reset = sub.add_parser("reset", help="delete runtime data (snapshot, executions, chat)")
    group = p_reset.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--executions", action="store_true")
    group.add_argument("--chat", action="store_true")
    p_reset.set_defaults(func=_cmd_reset)

    p_demo = sub.add_parser("demo", help="run the flagship draft scenario in the terminal")
    p_demo.set_defaults(func=lambda _a: _not_implemented("demo"))

    p_bench = sub.add_parser("benchmark", help="run the performance benchmark suite")
    p_bench.add_argument("--n", type=int, default=200)
    p_bench.set_defaults(func=lambda _a: _not_implemented("benchmark"))

    p_scan = sub.add_parser("scan-executions", help="check execution history for gaps/orphans")
    p_scan.set_defaults(func=lambda _a: _not_implemented("scan-executions"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    load_env()
    settings = get_settings()
    configure_logging(settings.service, settings.log_level)

    parser = _build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
