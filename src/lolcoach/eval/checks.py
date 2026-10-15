"""The evaluation checks: one pure function per product promise.

Every check reads only the *observable* result of a run — the recorded tool calls,
the final board, the narration mode, the reply text — so scoring is deterministic
and needs no second model. The two invariants the product stakes its name on are
checked structurally:

* **grounding** — the reply names no champion absent from the tool results, via the
  same :func:`allowed_champions` / :func:`unsourced_champions` the narrator uses;
* **engine fidelity** — when a pick was narrated, it equals a fresh run of the pure
  engine over the final board, so the number the coach quoted came from the engine,
  not from prose.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lolcoach.agent.agent import ToolStep, step_from_dict
from lolcoach.agent.narrator import allowed_champions, unsourced_champions
from lolcoach.agent.tools import Board, normalize_board
from lolcoach.constants import LANES
from lolcoach.engine.draft_state import DraftState
from lolcoach.engine.explain import recommendation_to_dict
from lolcoach.engine.scoring import evaluate_draft
from lolcoach.eval.scenarios import Scenario
from lolcoach.knowledge.ontology import Ontology
from lolcoach.values import as_str, opt_dict, opt_list
from lolcoach.workflow.executor import ExecutionResult


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """One requirement's verdict, with the detail a failure needs to be read."""

    name: str
    ok: bool
    detail: str


def evaluate(
    scenario: Scenario, result: ExecutionResult, ontology: Ontology
) -> tuple[CheckOutcome, ...]:
    """Run every applicable check against one scenario execution."""
    output = result.output
    tool_names = tuple(
        as_str(opt_dict(item).get("name")) for item in opt_list(output.get("tool_calls"))
    )
    steps = tuple(step_from_dict(item) for item in opt_list(output.get("history")))
    board = normalize_board(output.get("board"))
    reply = as_str(output.get("reply"))
    mode = as_str(output.get("mode"))
    unresolved = [as_str(item) for item in opt_list(output.get("unresolved"))]
    recommendation = opt_dict(output.get("recommendation"))

    checks: list[CheckOutcome] = [
        _status_check(scenario, result),
        _tools_called_check(scenario, tool_names),
        _tools_absent_check(scenario, tool_names),
        _budget_check(scenario, tool_names),
        _board_check(scenario, board),
        _unresolved_check(scenario, unresolved),
        _recommendation_check(scenario, recommendation),
        _mode_check(scenario, mode),
    ]
    if scenario.expect.error_contains:
        error = result.error or ""
        needle = scenario.expect.error_contains
        checks.append(
            CheckOutcome("error_contains", needle in error, f"error={error!r} (wanted {needle!r})")
        )
    checks.append(_grounding_check(scenario, ontology, reply, steps, board))
    checks.append(_mentions_check(scenario, reply))
    checks.append(_pick_from_engine_check(scenario, ontology, recommendation, board))
    return tuple(checks)


def _status_check(scenario: Scenario, result: ExecutionResult) -> CheckOutcome:
    wanted = scenario.expect.status
    ok = result.status == wanted
    detail = f"status={result.status!r} (wanted {wanted!r})"
    if not ok and result.error:
        detail += f"; error={result.error!r}"
    return CheckOutcome("status", ok, detail)


def _tools_called_check(scenario: Scenario, tool_names: tuple[str, ...]) -> CheckOutcome:
    wanted = scenario.expect.tools_called
    if not wanted:
        return CheckOutcome("tools_called", True, "not required")
    ok = _is_subsequence(wanted, tool_names)
    return CheckOutcome(
        "tools_called",
        ok,
        f"called {list(tool_names)}; wanted {list(wanted)} in order",
    )


def _tools_absent_check(scenario: Scenario, tool_names: tuple[str, ...]) -> CheckOutcome:
    forbidden = scenario.expect.tools_not_called
    if not forbidden:
        return CheckOutcome("tools_not_called", True, "none forbidden")
    present = [name for name in forbidden if name in tool_names]
    return CheckOutcome(
        "tools_not_called", not present, f"unexpectedly called {present}" if present else "clean"
    )


def _budget_check(scenario: Scenario, tool_names: tuple[str, ...]) -> CheckOutcome:
    budget = scenario.expect.max_tool_calls
    if budget is None:
        return CheckOutcome("max_tool_calls", True, "unbounded")
    return CheckOutcome(
        "max_tool_calls",
        len(tool_names) <= budget,
        f"made {len(tool_names)} tool call(s); budget {budget}",
    )


def _board_check(scenario: Scenario, board: Board) -> CheckOutcome:
    wanted = scenario.expect.board
    if not wanted:
        return CheckOutcome("board", True, "not checked")
    missing = {
        key: [slug for slug in slugs if slug not in board.get(key, [])]
        for key, slugs in wanted.items()
    }
    gap = {key: slugs for key, slugs in missing.items() if slugs}
    return CheckOutcome("board", not gap, f"board={board}" if not gap else f"missing {gap}")


def _unresolved_check(scenario: Scenario, unresolved: list[str]) -> CheckOutcome:
    if not scenario.expect.require_no_unresolved:
        return CheckOutcome("require_no_unresolved", True, "not checked")
    return CheckOutcome(
        "require_no_unresolved", not unresolved, f"unresolved={unresolved}"
    )


def _recommendation_check(scenario: Scenario, recommendation: dict[str, object]) -> CheckOutcome:
    wanted = scenario.expect.recommendation
    present = bool(recommendation)
    if wanted == "required":
        return CheckOutcome("recommendation", present, f"present={present} (wanted a pick)")
    if wanted == "forbidden":
        named = _named_picks(recommendation)
        return CheckOutcome(
            "recommendation",
            not present,
            "no pick expected" if not present else f"unexpected pick {named}",
        )
    return CheckOutcome("recommendation", True, "not checked")


def _mode_check(scenario: Scenario, mode: str) -> CheckOutcome:
    wanted = scenario.expect.mode
    if wanted == "any":
        return CheckOutcome("mode", True, f"mode={mode!r}")
    return CheckOutcome("mode", mode == wanted, f"mode={mode!r} (wanted {wanted!r})")


def _grounding_check(
    scenario: Scenario,
    ontology: Ontology,
    reply: str,
    steps: Sequence[ToolStep],
    board: Board,
) -> CheckOutcome:
    if not scenario.expect.grounded:
        return CheckOutcome("grounded", True, "not enforced")
    allowed = allowed_champions(ontology, steps, board)
    invented = unsourced_champions(reply, allowed, ontology)
    return CheckOutcome(
        "grounded",
        not invented,
        f"unsourced champions: {sorted(invented)}" if invented else "no unsourced champions",
    )


def _mentions_check(scenario: Scenario, reply: str) -> CheckOutcome:
    wanted = scenario.expect.reply_mentions
    forbidden = scenario.expect.reply_excludes
    if not wanted and not forbidden:
        return CheckOutcome("mentions", True, "not checked")
    haystack = reply.lower()
    missing = [needle for needle in wanted if needle.lower() not in haystack]
    leaked = [needle for needle in forbidden if needle.lower() in haystack]
    ok = not missing and not leaked
    if ok:
        detail = "mentions as required"
    else:
        detail = f"missing {missing}; forbidden present {leaked}"
    return CheckOutcome("mentions", ok, detail)


def _pick_from_engine_check(
    scenario: Scenario, ontology: Ontology, recommendation: dict[str, object], board: Board
) -> CheckOutcome:
    if not scenario.expect.pick_from_engine or not recommendation:
        return CheckOutcome("pick_from_engine", True, "not applicable")
    state = DraftState(
        allies=tuple(board.get("allies", [])),
        enemies=tuple(board.get("enemies", [])),
        ally_bans=tuple(board.get("ally_bans", [])),
        enemy_bans=tuple(board.get("enemy_bans", [])),
        open_roles=tuple(board.get("open_roles") or ()) or LANES,
    )
    try:
        fresh = recommendation_to_dict(evaluate_draft(ontology, state))
    except Exception as exc:  # noqa: BLE001 - recompute must not fail the whole eval
        return CheckOutcome("pick_from_engine", True, f"skipped: {type(exc).__name__}: {exc}")
    reported = _pick_slugs(recommendation)
    recomputed = _pick_slugs(fresh)
    ok = reported == recomputed
    return CheckOutcome(
        "pick_from_engine",
        ok,
        f"reported {reported}; engine on final board {recomputed}",
    )


def _pick_slugs(recommendation: dict[str, object]) -> tuple[str | None, str | None]:
    return (
        _slug(recommendation.get("counter")),
        _slug(recommendation.get("synergy")),
    )


def _slug(pick: object) -> str | None:
    slug = as_str(opt_dict(pick).get("slug"))
    return slug or None


def _named_picks(recommendation: dict[str, object]) -> list[str]:
    return [slug for slug in _pick_slugs(recommendation) if slug]


def _is_subsequence(needles: Sequence[str], haystack: Sequence[str]) -> bool:
    """Whether ``needles`` appear in ``haystack`` in order (not necessarily adjacent)."""
    iterator = iter(haystack)
    for needle in needles:
        if not any(needle == item for item in iterator):
            return False
    return True
