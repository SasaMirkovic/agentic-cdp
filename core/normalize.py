"""Canonicalization of names, domains, phones, addresses, plus PII tagging and
the token-containment name-agreement check used by the Stage-1 guard."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "llc", "llp", "ltd", "limited",
    "gmbh", "sa", "srl", "plc", "co", "company", "ag", "bv", "oy", "ab", "kk",
    "pty", "sarl", "group", "holdings",
}

FREEMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com",
}

_ADDRESS_ABBREVIATIONS = {
    "street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd",
    "drive": "dr", "lane": "ln", "suite": "ste", "floor": "fl", "building": "bldg",
    "north": "n", "south": "s", "east": "e", "west": "w",
}

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def name_tokens(name: str | None) -> list[str]:
    """Lowercased tokens of a company name with legal suffixes removed."""
    if not name:
        return []
    text = name.lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    tokens = [t for t in _WS.split(text) if t]
    return [t for t in tokens if t not in LEGAL_SUFFIXES]


def normalize_name(name: str | None) -> str | None:
    tokens = name_tokens(name)
    return " ".join(tokens) or None


def normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    d = domain.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    return d or None


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else None


def normalize_address(address: str | None) -> str | None:
    if not address:
        return None
    text = _PUNCT.sub(" ", address.lower())
    tokens = [_ADDRESS_ABBREVIATIONS.get(t, t) for t in _WS.split(text) if t]
    return " ".join(tokens) or None


def pii_flags(phone: str | None, address: str | None, notes: str | None) -> list[str]:
    flags = []
    if phone:
        flags.append("phone")
    if address:
        flags.append("address")
    if notes:
        flags.append("notes")
    return flags


def tokens_match_one_to_one(name_a: str | None, name_b: str | None, ratio_threshold: int = 85) -> bool:
    """True iff the two names' token sets (suffix-stripped) match one-to-one with
    per-token fuzzy tolerance and no leftover distinguishing tokens on either side.

    A scalar full-string ratio is deliberately avoided: a long shared brand prefix
    dominates such ratios, so "Northwind" vs "Northwind Labs" would pass a
    threshold that "Acme" vs "Acme Media" fails — the guard must not depend on
    brand-name length. Leftover tokens ("labs") always fail here.
    """
    a, b = name_tokens(name_a), name_tokens(name_b)
    if not a or not b or len(a) != len(b):
        return False
    remaining = list(b)
    for token in a:
        scores = [(fuzz.ratio(token, cand), i) for i, cand in enumerate(remaining)]
        best_score, best_i = max(scores)
        if best_score < ratio_threshold:
            return False
        remaining.pop(best_i)
    return True
