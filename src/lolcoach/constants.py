"""Shared constants for LoL Coach."""

from __future__ import annotations

# The five roles the draft board supports. Order is display order (top -> support).
LANES: tuple[str, ...] = ("top", "jungle", "mid", "adc", "support")

# Champion role *classes* OP.GG reports inside `positions[].roles[]`.
ROLE_CLASSES: tuple[str, ...] = (
    "MAGE",
    "TANK",
    "CONTROLLER",
    "FIGHTER",
    "MARKSMAN",
    "ASSASSIN",
    "SUPPORT",
)

# The two relation kinds the knowledge graph stores.
RELATION_TYPES: tuple[str, ...] = ("counter", "synergy")

# A champion must have at least this many games for an edge to be emitted, and the
# win rate must differ from even by at least this much. Keeps noise out of the graph.
MIN_EDGE_GAMES = 200
MIN_EDGE_DELTA = 0.01

# Sample size at which an edge is considered fully confident (confidence = min(1, games/this)).
SAMPLE_FULL_CONFIDENCE = 2000

# Neutral win rate. A counter beats its target when its win rate is above this.
EVEN_WIN_RATE = 0.5
