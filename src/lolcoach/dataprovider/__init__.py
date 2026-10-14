"""Data provider: the only layer that talks to the network.

Everything OP.GG-related lives here — the MCP client, the parser for its output,
the throttled/resumable fetch, the SQLite snapshot it writes, and the build step
that turns the snapshot into the YAML knowledge graph.
"""

from __future__ import annotations
