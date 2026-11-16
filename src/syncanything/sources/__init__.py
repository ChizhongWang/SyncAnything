from syncanything.sources.agy import AgyAdapter
from syncanything.sources.base import SourceAdapter
from syncanything.sources.citeanything import CiteAnythingAdapter
from syncanything.sources.claude import ClaudeAdapter
from syncanything.sources.codex import CodexAdapter
from syncanything.sources.cursor import CursorAdapter
from syncanything.sources.kimi import KimiAdapter
from syncanything.sources.pi import PiAdapter

SOURCE_NAMES = ["claude", "codex", "cursor", "kimi", "pi", "agy", "citeanything"]


def default_adapters() -> list[SourceAdapter]:
    return [
        ClaudeAdapter(),
        CodexAdapter(),
        CursorAdapter(),
        KimiAdapter(),
        PiAdapter(),
        AgyAdapter(),
        CiteAnythingAdapter(),
    ]


def local_adapters() -> list[SourceAdapter]:
    """Adapters that only touch the local filesystem, safe to run on every read."""
    return [adapter for adapter in default_adapters() if not adapter.is_remote]


__all__ = [
    "SOURCE_NAMES",
    "SourceAdapter",
    "local_adapters",
    "CiteAnythingAdapter",
    "AgyAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "KimiAdapter",
    "PiAdapter",
    "default_adapters",
]
