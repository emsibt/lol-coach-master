"""The agent evaluation harness: golden scenarios, deterministic checks, report.

The agent is scored on the outcome the product actually promises, not on prose
quality: which tools it called and in what order, whether it resolved the board,
whether its answer stayed grounded in the tool results, and whether the pick it
narrated came from the deterministic engine. A scenario is a plain YAML record
(``evals/scenarios.yaml``); a check is a pure function over the execution result,
so scoring never needs a second model and never varies run to run.
"""
