"""Public platform facts, independent of any account's participation result."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DrawFacts:
    business_id: str = ""
    business_type: int = 0
    reward: str = ""
    deadline: datetime | None = None
    state: str = "unverified"
    reason: str = ""

    @property
    def verified(self):
        return self.state in ("active", "ended")
