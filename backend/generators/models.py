from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedArtifact:
    path: str
    content: str


__all__ = ["GeneratedArtifact"]
