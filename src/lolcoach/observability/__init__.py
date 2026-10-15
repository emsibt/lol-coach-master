"""Optional AI observability for the agent's model calls.

Every model consultation in LoL Coach — the agent's ``Think`` decisions and its
final narration — goes through one seam, :class:`lolcoach.agent.llm.LlmClient`.
Langfuse is attached there, so a whole agent run is recorded as a single trace
holding each call's prompt, completion, tool calls, token usage, and cost.

The integration is optional and lazy: with no Langfuse credentials
:func:`trace` is a no-op, the SDK is never imported, and the app runs unchanged
without the ``langfuse`` package installed. Enabling it is pure configuration —
see :mod:`lolcoach.observability.tracing`.
"""
