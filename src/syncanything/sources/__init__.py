from syncanything.sources.base import SourceAdapter
from syncanything.sources.citeanything import CiteAnythingAdapter
from syncanything.sources.claude import ClaudeAdapter
from syncanything.sources.codex import CodexAdapter
from syncanything.sources.kimi import KimiAdapter
from syncanything.sources.pi import PiAdapter


def default_adapters() -> list[SourceAdapter]:
    return [ClaudeAdapter(), CodexAdapter(), KimiAdapter(), PiAdapter(), CiteAnythingAdapter()]


__all__ = [
    "SourceAdapter",
    "CiteAnythingAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "KimiAdapter",
    "PiAdapter",
    "default_adapters",
]
