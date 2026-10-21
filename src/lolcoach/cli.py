"""Command-line entrypoint.

    python -m lolcoach.cli serve
    python -m lolcoach.cli fetch [--limit N]
    python -m lolcoach.cli build-kg [--patch X]
    python -m lolcoach.cli kg-report [--out PATH]
    python -m lolcoach.cli reset (--all | --executions | --chat)
    python -m lolcoach.cli eval [--suite PATH] [--json PATH] [--live]
    python -m lolcoach.cli eval-agents [--suite PATH] [--json PATH]
    python -m lolcoach.cli agent-fix (--issue N | --all) [--repo OWNER/NAME] [--workdir PATH]
    python -m lolcoach.cli agent-review --pr N [--repo OWNER/NAME] [--workdir PATH]
    python -m lolcoach.cli demo
    python -m lolcoach.cli benchmark --n 200
    python -m lolcoach.cli scan-executions

This is an entrypoint, so it configures logging itself (mirroring ``main.py``).
Commands whose milestone has not landed exit with a clear message rather than
failing obscurely.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from lolcoach.config import Settings, get_settings, load_env
from lolcoach.logging_setup import configure_logging

if TYPE_CHECKING:
    from lolcoach.github.client import GitHubClient
    from lolcoach.github.types import RepoRef
    from lolcoach.workflow.executor import ExecutionResult

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
        targets = [
            settings.snapshot_db,
            settings.executions_db,
            settings.chat_db,
            settings.eval_db,
        ]
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


def _cmd_eval(args: argparse.Namespace) -> int:
    from lolcoach.agent.llm import build_client
    from lolcoach.eval.report import format_summary, to_dict, write_json
    from lolcoach.eval.runner import EvalError, run_suite
    from lolcoach.eval.scenarios import ScenarioError, load_scenarios
    from lolcoach.knowledge.ontology import OntologyError, load_ontology
    from lolcoach.knowledge.reference import get_reference

    settings = get_settings()
    if build_client(settings) is None:
        print("no LLM is configured: set LOL_COACH_LLM_PROVIDER and its API key")
        return 1
    try:
        scenarios = load_scenarios(Path(args.suite) if args.suite else None)
        ontology = load_ontology()
    except (ScenarioError, OntologyError) as exc:
        print(str(exc))
        return 1

    live = None
    if args.live:
        from lolcoach.dataprovider.live import build_live_provider

        live = build_live_provider()

    logger.info(
        "Running agent evaluation",
        extra={"data": {"scenarios": len(scenarios), "provider": settings.llm_provider}},
    )
    try:
        report = run_suite(
            scenarios,
            ontology=ontology,
            client_factory=lambda: build_client(settings),
            store_path=settings.eval_db,
            settings=settings,
            reference=get_reference(),
            live=live,
        )
    except EvalError as exc:
        print(str(exc))
        return 1

    print(format_summary(report))
    _record_eval_run(settings.eval_db, kind="chat", provider=report.provider,
                     model=report.model, report=to_dict(report))
    if args.json_out:
        out = Path(args.json_out)
        write_json(report, out)
        print(f"report written to {out}")
    print(f"executions persisted to {settings.eval_db} (Trace Inspector)")
    return _EXIT_OK if report.ok else 1


def _cmd_eval_agents(args: argparse.Namespace) -> int:
    """Run the hermetic maintenance-agent suite (no model, no network, no disk)."""
    from lolcoach.eval.agent_runner import format_agent_summary, run_agent_suite
    from lolcoach.eval.agent_scenarios import AgentScenarioError, load_agent_scenarios

    settings = get_settings()
    try:
        scenarios = load_agent_scenarios(Path(args.suite) if args.suite else None)
    except AgentScenarioError as exc:
        print(str(exc))
        return 1

    logger.info(
        "Running maintenance-agent evaluation", extra={"data": {"scenarios": len(scenarios)}}
    )
    report = run_agent_suite(scenarios, store_path=settings.eval_db, settings=settings)
    print(format_agent_summary(report))
    _record_eval_run(
        settings.eval_db,
        kind="agent",
        provider=report.provider,
        model=report.model,
        report=report.to_dict(),
    )
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"report written to {out}")
    return _EXIT_OK if report.ok else 1


def _record_eval_run(
    eval_db: Path, *, kind: str, provider: str, model: str, report: dict[str, object]
) -> None:
    """Persist a suite verdict to the evaluation history (``data/eval.db``)."""
    from lolcoach.eval.store import EvalRunStore

    history = EvalRunStore.connect(eval_db)
    try:
        run_id = history.record(kind=kind, provider=provider, model=model, report=report)
    finally:
        history.close()
    print(f"recorded as eval run {run_id} (Evaluation view)")


def _resolve_repo(args: argparse.Namespace, settings: Settings) -> RepoRef:
    """The repository to act on: ``--repo``, else ``LOL_COACH_GITHUB_REPO``."""
    from lolcoach.github.types import RepoRef

    slug = args.repo or settings.github_repo
    owner, _, name = slug.partition("/")
    if not owner or not name:
        raise SystemExit(
            "no repository configured; pass --repo owner/name or set LOL_COACH_GITHUB_REPO"
        )
    return RepoRef(owner=owner, name=name)


def _worktree(args: argparse.Namespace) -> Path:
    root = Path(args.workdir).resolve() if args.workdir else Path.cwd()
    if not (root / ".git").exists():
        raise SystemExit(f"{root} is not a git checkout; the agent needs a working tree")
    return root


def _issue_numbers(
    args: argparse.Namespace, github: GitHubClient, settings: Settings
) -> list[int]:
    """The issues to act on: the one named, or every open one carrying the label."""
    if args.issue:
        return [args.issue]
    numbers = [issue.number for issue in github.list_issues(label=settings.agent_issue_label)]
    return numbers[: args.limit] if args.limit is not None else numbers


def _print_agent_run(workflow: str, target: int, result: ExecutionResult) -> None:
    outcome = "ok" if result.status == "succeeded" else "stopped"
    print(f"{workflow} #{target}: {outcome} ({result.status}) execution {result.execution_id}")
    if result.error:
        print(f"  {result.error}")


def _cmd_agent_fix(args: argparse.Namespace) -> int:
    from lolcoach.agent.fixer import LlmCoder
    from lolcoach.agent.llm import build_client
    from lolcoach.agent.publisher import GitPublisher
    from lolcoach.agent.workspace import RepoWorkspace
    from lolcoach.github.client import GhCliClient
    from lolcoach.observability import tracing
    from lolcoach.workflow.executor import WorkflowExecutor
    from lolcoach.workflow.store import ExecutionStore
    from lolcoach.workflow.workflows.issue_fix import build_issue_fix

    settings = get_settings()
    ref = _resolve_repo(args, settings)
    root = _worktree(args)
    github = GhCliClient(ref)
    client = build_client(settings)
    if client is None:
        print("no LLM is configured: set LOL_COACH_LLM_PROVIDER and its API key")
        return 1

    numbers = _issue_numbers(args, github, settings)
    if not numbers:
        print(f"no open issues labelled {settings.agent_issue_label!r}")
        return _EXIT_OK

    built = build_issue_fix(
        coder=LlmCoder(client, settings),
        github=github,
        workspace=RepoWorkspace(root),
        publisher=GitPublisher(root),
        settings=settings,
    )
    store = ExecutionStore.connect(settings.executions_db)
    failures = 0
    try:
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        for number in numbers:
            with tracing.trace(
                settings,
                name="issue-fix",
                scope="agent",
                tags=("agent", "issue-fix", settings.llm_provider),
                metadata={"issue": number, "repo": ref.slug},
            ):
                result = executor.run({"issue_number": number})
            _print_agent_run("issue-fix", number, result)
            if result.status != "succeeded":
                failures += 1
    finally:
        store.close()
    if failures:
        print(f"{failures} issue(s) were not fixed; see the Agent runs view")
    return _EXIT_OK if failures == 0 else 1


def _cmd_agent_review(args: argparse.Namespace) -> int:
    from lolcoach.agent.llm import build_client
    from lolcoach.agent.reviewer import LlmReviewer
    from lolcoach.agent.workspace import RepoWorkspace
    from lolcoach.github.client import GhCliClient
    from lolcoach.observability import tracing
    from lolcoach.workflow.executor import WorkflowExecutor
    from lolcoach.workflow.store import ExecutionStore
    from lolcoach.workflow.workflows.pr_review import build_pr_review

    settings = get_settings()
    ref = _resolve_repo(args, settings)
    root = _worktree(args)
    github = GhCliClient(ref)

    client = build_client(settings)
    if client is None:
        print("no LLM is configured: set LOL_COACH_LLM_PROVIDER and its API key")
        return 1

    built = build_pr_review(
        reviewer=LlmReviewer(client, settings),
        github=github,
        workspace=RepoWorkspace(root),
        settings=settings,
    )
    store = ExecutionStore.connect(settings.executions_db)
    try:
        executor = WorkflowExecutor(
            built.workflow, tasks=built.tasks, predicates=built.predicates, store=store
        )
        with tracing.trace(
            settings,
            name="pr-review",
            scope="agent",
            tags=("agent", "pr-review", settings.llm_provider),
            metadata={"pr": args.pr, "repo": ref.slug, "auto_merge": settings.agent_auto_merge},
        ):
            result = executor.run({"pull_request_number": args.pr})
    finally:
        store.close()

    _print_agent_run("pr-review", args.pr, result)
    if result.status == "succeeded" and not settings.agent_auto_merge:
        print("  approved; a human merges (set LOL_COACH_AGENT_AUTO_MERGE=true to opt in)")
    return _EXIT_OK if result.status == "succeeded" else 1

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

    p_reset = sub.add_parser(
        "reset", help="delete runtime data (snapshot, executions, chat, eval)"
    )
    group = p_reset.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--executions", action="store_true")
    group.add_argument("--chat", action="store_true")
    p_reset.set_defaults(func=_cmd_reset)

    p_eval = sub.add_parser("eval", help="run the agent evaluation suite against a model")
    p_eval.add_argument(
        "--suite", default=None, help="scenario YAML (default evals/scenarios.yaml)"
    )
    p_eval.add_argument("--json", dest="json_out", default=None, help="write the report as JSON")
    p_eval.add_argument("--live", action="store_true", help="enable the live (network) tools")
    p_eval.set_defaults(func=_cmd_eval)

    p_eval_agents = sub.add_parser(
        "eval-agents", help="run the hermetic maintenance-agent evaluation (no model needed)"
    )
    p_eval_agents.add_argument(
        "--suite", default=None, help="scenario YAML (default evals/agent_scenarios.yaml)"
    )
    p_eval_agents.add_argument(
        "--json", dest="json_out", default=None, help="write the report as JSON"
    )
    p_eval_agents.set_defaults(func=_cmd_eval_agents)

    p_fix = sub.add_parser(
        "agent-fix", help="fix labelled GitHub issues and open a pull request for each"
    )
    fix_target = p_fix.add_mutually_exclusive_group(required=True)
    fix_target.add_argument("--issue", type=int, help="the issue number to fix")
    fix_target.add_argument(
        "--all", action="store_true", help="every open issue carrying the configured label"
    )
    p_fix.add_argument("--repo", default=None, help="owner/name (default LOL_COACH_GITHUB_REPO)")
    p_fix.add_argument("--workdir", default=None, help="the checkout to work in (default: cwd)")
    p_fix.add_argument("--limit", type=int, default=None, help="cap the issues handled by --all")
    p_fix.set_defaults(func=_cmd_agent_fix)

    p_review = sub.add_parser(
        "agent-review", help="review a pull request and approve or request changes"
    )
    p_review.add_argument("--pr", type=int, required=True, help="the pull request number")
    p_review.add_argument("--repo", default=None, help="owner/name (default LOL_COACH_GITHUB_REPO)")
    p_review.add_argument("--workdir", default=None, help="the checkout to read (default: cwd)")
    p_review.set_defaults(func=_cmd_agent_review)

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
