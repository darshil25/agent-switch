"""The interface every agent adapter implements. Adding an agent means adding one file."""
from __future__ import annotations

from typing import List, Optional, Sequence

from ..util import Parsed, Session


class Agent:
    name = ""  # command name, e.g. "claude"
    label = ""  # display name, e.g. "Claude Code"
    binary = ""  # executable looked up on PATH

    def sessions(self, cwd: str) -> List[Session]:
        """All sessions recorded for this folder, newest first."""
        raise NotImplementedError

    def parse(self, session: Session) -> Parsed:
        """Read a session into condensed turns."""
        raise NotImplementedError

    def describe(self, session: Session) -> Optional[str]:
        """A short title for menus, or None if the session holds no real conversation."""
        raise NotImplementedError

    def latest_folder(self) -> Optional[str]:
        """Folder of the newest session anywhere, used only to help when nothing is found."""
        return None

    def argv(self, prompt: str, resume_id: Optional[str], extra: Sequence[str]) -> List[str]:
        """Command line that starts (or resumes) a session. prompt may be empty."""
        raise NotImplementedError
