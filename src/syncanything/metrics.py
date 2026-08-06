from __future__ import annotations

import re
from dataclasses import dataclass

# Ideographs, kana, and hangul each count as one word: that is what a Chinese or
# Japanese word count means, and it keeps the book comparison in honest units.
_CJK = re.compile(
    "[㐀-䶿一-鿿豈-﫿"  # ideographs and compatibility forms
    "぀-ゟ゠-ヿ"  # hiragana and katakana
    "가-힯]"  # hangul syllables
)
# Latin words, keeping internal apostrophes and hyphens together: "don't", "well-known".
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+(?:['’_-][A-Za-z0-9]+)*")
_WHITESPACE = re.compile(r"\s")

# Tokenisers split CJK roughly every 1.5 characters and Latin script roughly every
# 4. Estimating from those ratios keeps SyncAnything free of a tokeniser
# dependency; every surface that shows the number labels it an estimate.
_CJK_CHARS_PER_TOKEN = 1.5
_LATIN_CHARS_PER_TOKEN = 4.0


@dataclass(frozen=True, slots=True)
class TextMetrics:
    """Size of one piece of conversation text.

    `characters` excludes whitespace so that indented code does not inflate a
    word count, while `bytes` stays the true UTF-8 size of the text on disk.
    """

    characters: int = 0
    words: int = 0
    tokens: int = 0
    bytes: int = 0

    def __add__(self, other: "TextMetrics") -> "TextMetrics":
        return TextMetrics(
            self.characters + other.characters,
            self.words + other.words,
            self.tokens + other.tokens,
            self.bytes + other.bytes,
        )


def measure(text: str) -> TextMetrics:
    if not text:
        return TextMetrics()
    cjk = len(_CJK.findall(text))
    characters = len(text) - len(_WHITESPACE.findall(text))
    latin_characters = max(0, characters - cjk)
    tokens = round(
        cjk / _CJK_CHARS_PER_TOKEN + latin_characters / _LATIN_CHARS_PER_TOKEN
    )
    return TextMetrics(
        characters=characters,
        words=cjk + len(_LATIN_WORD.findall(text)),
        tokens=tokens,
        bytes=len(text.encode("utf-8")),
    )


@dataclass(frozen=True, slots=True)
class Book:
    title: str
    size: int
    unit: str  # "characters" for CJK works, "words" for English ones.


# Lengths are the widely cited figures for the standard edition of each work and
# vary by a few percent between editions; the comparison gives a sense of scale,
# not a measurement. Chinese works are compared by character count and English
# works by word count, so both sides of each ratio carry the same unit.
BOOK_YARDSTICKS: dict[str, tuple[Book, ...]] = {
    "zh": (
        Book("红楼梦", 730_000, "characters"),
        Book("三国演义", 750_000, "characters"),
        Book("西游记", 820_000, "characters"),
        Book("水浒传", 960_000, "characters"),
    ),
    "en": (
        Book("War and Peace", 587_287, "words"),
        Book("Moby-Dick", 206_052, "words"),
        Book("Ulysses", 264_448, "words"),
        Book("The Lord of the Rings", 481_103, "words"),
    ),
}


def book_equivalents(characters: int, words: int) -> dict[str, list[dict[str, object]]]:
    """Express a corpus size as a multiple of well-known books, per language."""
    totals = {"characters": characters, "words": words}
    return {
        language: [
            {
                "title": book.title,
                "size": book.size,
                "unit": book.unit,
                "equivalent": round(totals[book.unit] / book.size, 2),
            }
            for book in books
        ]
        for language, books in BOOK_YARDSTICKS.items()
    }
