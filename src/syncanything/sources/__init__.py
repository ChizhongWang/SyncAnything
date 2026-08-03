from syncanything.sources.base import SourceAdapter
from syncanything.sources.citeanything import CiteAnythingAdapter
from syncanything.sources.claude import ClaudeAdapter
from syncanything.sources.codex import CodexAdapter
from syncanything.sources.kimi import KimiAdapter
from syncanything.sources.pi import PiAdapter


def default_adapters() -> list[SourceAdapter]:
    return [ClaudeAdapter(), CodexAdapter(), KimiAdapter(), PiAdapter(), CiteAnythingAdapter()]


def local_adapters() -> list[SourceAdapter]:
    """Adapters that only touch the local filesystem, safe to run on every read."""
    return [adapter for adapter in default_adapters() if not adapter.is_remote]


__all__ = [
    "SourceAdapter",
    "local_adapters",
    "CiteAnythingAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "KimiAdapter",
    "PiAdapter",
    "default_adapters",
]
