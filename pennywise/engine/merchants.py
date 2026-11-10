"""Merchant normalisation: turn raw card descriptors into stable merchant names.

Deterministic and versioned. Bump NORMALISER_VERSION whenever clean() changes, so
outputs that report the version (with the alias-table hash) can be reproduced.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

NORMALISER_VERSION = 1

_SPACES = re.compile(r"\s+")
# A trailing reference after * or #, only when it contains a digit: "AMZN MKTP UK*AB12CD34"
# drops its reference, while "PAYPAL *NETFLIX" keeps the merchant after the star.
_TRAILING_REF = re.compile(r"\s*[*#]\s*(?=\S*\d)\S+$")
_LONG_DIGITS = re.compile(r"\d{4,}")
# Trailing location words that card networks append. Tune on real statements, then bump
# NORMALISER_VERSION.
_LOCATION_SUFFIXES = frozenset(
    {
        "GB",
        "GBR",
        "UK",
        "LONDON",
        "MANCHESTER",
        "BIRMINGHAM",
        "LEEDS",
        "GLASGOW",
        "EDINBURGH",
        "BRISTOL",
        "LIVERPOOL",
        "CARDIFF",
    }
)


@dataclass(frozen=True, slots=True, order=True)
class Alias:
    pattern: str  # prefix patterns are stored as desc_key() text
    match_type: str  # prefix | regex
    merchant: str


def desc_key(raw: str) -> str:
    """Fixed key for a raw description: uppercase, whitespace collapsed.

    Stored at import and never recomputed, so it must not change between versions.
    """
    return _SPACES.sub(" ", raw.upper()).strip()


def clean(raw: str) -> str:
    """Strip references, long digit runs and trailing locations from a descriptor."""
    text = desc_key(raw)
    text = _TRAILING_REF.sub("", text)
    text = _SPACES.sub(" ", _LONG_DIGITS.sub(" ", text)).strip()
    words = text.split(" ")
    while len(words) > 1 and words[-1] in _LOCATION_SUFFIXES:
        words.pop()
    return " ".join(words) or desc_key(raw)


@lru_cache(maxsize=256)
def _ordered(
    aliases: tuple[Alias, ...],
) -> tuple[tuple[Alias, ...], tuple[tuple[re.Pattern[str], Alias], ...]]:
    prefixes = sorted(
        (alias for alias in aliases if alias.match_type == "prefix"),
        key=lambda alias: (-len(alias.pattern), alias.pattern),
    )
    regexes = sorted(alias for alias in aliases if alias.match_type == "regex")
    return tuple(prefixes), tuple((re.compile(a.pattern, re.IGNORECASE), a) for a in regexes)


@lru_cache(maxsize=65536)
def normalise(raw: str, aliases: tuple[Alias, ...] = ()) -> str:
    """The merchant for a raw description.

    Aliases win over the cleaned descriptor: the longest matching prefix first, then
    regexes in pattern order. Per-transaction merchant overrides are applied by the caller
    and win over both.
    """
    cleaned = clean(raw)
    prefixes, regexes = _ordered(aliases)
    for alias in prefixes:
        if cleaned.startswith(alias.pattern):
            return alias.merchant
    for pattern, alias in regexes:
        if pattern.search(cleaned):
            return alias.merchant
    return cleaned


def alias_table_hash(aliases: Iterable[Alias]) -> str:
    """Short, order-independent fingerprint of an alias table, reported with outputs."""
    digest = hashlib.sha256()
    for alias in sorted(aliases):
        digest.update(f"{alias.pattern}\t{alias.match_type}\t{alias.merchant}\n".encode())
    return digest.hexdigest()[:16]


# Payment processors whose descriptors name the real merchant after them: "PAYPAL *NETFLIX"
# is not a variant of a rule for "PAYPAL".
_PROCESSORS = frozenset({"PAYPAL", "SUMUP", "SQ", "SP", "ZETTLE", "CLR", "SHOP PAY", "PAYMENT"})


def suffix_variants(names: Iterable[str], known: Iterable[str]) -> dict[str, str]:
    """Names that are a known merchant plus trailing words, such as the town older card
    statements append ("THE RED LION BRISTOL" for "THE RED LION"), each mapped to the
    longest known merchant it extends. Case is ignored; processor descriptors are skipped."""
    by_folded = {name.casefold(): name for name in known if name.upper() not in _PROCESSORS}
    longest_first = sorted(by_folded, key=len, reverse=True)
    variants: dict[str, str] = {}
    for name in names:
        folded = name.casefold()
        if folded in by_folded:
            continue
        for base in longest_first:
            rest = folded[len(base) :]
            if (
                folded.startswith(base)
                and rest.startswith(" ")
                and not rest.lstrip().startswith("*")
            ):
                variants[name] = by_folded[base]
                break
    return variants
