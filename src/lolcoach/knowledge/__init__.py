"""The knowledge layer: load and validate the YAML knowledge graph.

The graph is regenerated from the OP.GG snapshot by
``lolcoach.dataprovider.build_kg`` and checked in under ``knowledge/seed``.
This layer turns those files into typed objects and refuses to load an invalid
graph (schema violation or dangling relation endpoint).
"""

from __future__ import annotations
