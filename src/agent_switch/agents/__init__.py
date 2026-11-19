from .claude import ClaudeAgent
from .kiro import KiroAgent

AGENTS = {a.name: a for a in (ClaudeAgent(), KiroAgent())}
