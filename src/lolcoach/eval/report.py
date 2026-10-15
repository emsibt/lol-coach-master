"""Render a suite report as human-readable text or machine-readable JSON."""

from __future__ import annotations

import json
from pathlib import Path

from lolcoach.eval.runner import ScenarioResult, SuiteReport

_PASS = "PASS"
_FAIL = "FAIL"


def format_summary(report: SuiteReport) -> str:
    """A compact per-scenario verdict list plus a pass/fail tally."""
    lines = [f"Agent evaluation -- {report.provider}/{report.model}", ""]
    for result in report.results:
        lines.append(f"[{_PASS if result.passed else _FAIL}] {result.scenario_id} -- {result.name}")
        lines.append(
            f"       tools: {list(result.tool_names) or '(none)'}"
            f"  status: {result.status}  mode: {result.mode or '(none)'}"
        )
        for failure in result.failures:
            lines.append(f"       - {failure.name}: {failure.detail}")
    lines.append("")
    lines.append(f"{report.passed}/{report.total} scenarios passed")
    return "\n".join(lines)


def to_dict(report: SuiteReport) -> dict[str, object]:
    return {
        "provider": report.provider,
        "model": report.model,
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "scenarios": [_scenario_dict(result) for result in report.results],
    }


def _scenario_dict(result: ScenarioResult) -> dict[str, object]:
    return {
        "id": result.scenario_id,
        "name": result.name,
        "passed": result.passed,
        "execution_id": result.execution_id,
        "status": result.status,
        "mode": result.mode,
        "tools": list(result.tool_names),
        "board": result.board,
        "reply": result.reply,
        "error": result.error,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in result.checks
        ],
    }


def write_json(report: SuiteReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(report), indent=2, ensure_ascii=False), encoding="utf-8")
