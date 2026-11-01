from dataclasses import dataclass
from typing import Any


@dataclass
class Candidate:
    """One stage's proposed value for one journal field."""

    field: str
    value: Any
    confidence: float
    source: str  # filename | folder | ocr | vision
    evidence: str = ""


@dataclass
class Resolved:
    """The merged result for a field after combining every stage's candidates."""

    field: str
    value: Any
    confidence: float
    source: str
    evidence: str
    alternatives: list[Candidate]
    conflict: bool = False
