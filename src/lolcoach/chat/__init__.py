"""Persistent chat sessions — the coach's conversation history and agent memory.

A *session* is one conversation (ChatGPT/Gemini style): an ordered transcript of
user questions and coach replies, the draft board it is working from, the memory
notes the agent has recorded, and a hard cap on how many questions it may hold.
The transcript *is* the agent's memory: the server replays it as prior turns so
the agent remembers what was already discussed.
"""

from lolcoach.chat.memory import SessionMemory
from lolcoach.chat.store import ChatStore, Message, Session, SessionLimitReached

__all__ = [
    "ChatStore",
    "Message",
    "Session",
    "SessionLimitReached",
    "SessionMemory",
]
