"""The draft state the engine scores against.

A thin, immutable description of the board: what each side has picked and banned,
and which roles our side still has to fill. Validation is explicit so the
workflow's ``ValidateDraft`` state can reject a malformed board before scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lolcoach.constants import LANES


@dataclass(frozen=True, slots=True)
class DraftState:
    allies: tuple[str, ...] = ()
    enemies: tuple[str, ...] = ()
    ally_bans: tuple[str, ...] = ()
    enemy_bans: tuple[str, ...] = ()
    open_roles: tuple[str, ...] = field(default=LANES)

    def unavailable(self) -> frozenset[str]:
        """Every champion slug already committed (picked or banned by either side)."""
        return frozenset((*self.allies, *self.enemies, *self.ally_bans, *self.enemy_bans))

    def problems(self) -> list[str]:
        """Human-readable reasons the board is invalid; empty when valid."""
        problems: list[str] = []

        for role in self.open_roles:
            if role not in LANES:
                problems.append(f"unknown role {role!r}")
        if len(set(self.open_roles)) != len(self.open_roles):
            problems.append("open_roles contains duplicates")

        seen: dict[str, str] = {}
        groups = (
            ("ally pick", self.allies),
            ("enemy pick", self.enemies),
            ("ally ban", self.ally_bans),
            ("enemy ban", self.enemy_bans),
        )
        for label, slugs in groups:
            for slug in slugs:
                if not slug:
                    problems.append(f"{label} contains an empty slug")
                elif slug in seen:
                    problems.append(f"{slug!r} appears as both {seen[slug]} and {label}")
                else:
                    seen[slug] = label

        if len(self.allies) > len(LANES) or len(self.enemies) > len(LANES):
            problems.append("a team may have at most 5 picks")
        if not self.open_roles:
            problems.append("at least one role must be open")

        return problems

    def is_valid(self) -> bool:
        return not self.problems()
