"""Small offline NLP: turn alarm text, notes and outage facts into sparse feature vectors.

English and Arabic words are both understood as tokens (Unicode letters/digits). Words and word pairs are
hashed into a fixed number of slots, so any new word works without a vocabulary file.
"""
from __future__ import annotations

import math
import re
import zlib

DIM = 1 << 12                     # 4096 hashed slots
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STOP = {"the", "a", "an", "of", "to", "in", "on", "and", "or", "is", "are", "for", "due", "with", "at",
         "من", "في", "على", "الى", "إلى", "عن", "و", "او", "أو"}
_DIGITS = re.compile(r"\d")


def tokens(text: str) -> list:
    """Lower-case words; digits inside site ids are folded (GD1234 -> gd####) so similar ids match."""
    out = []
    for w in _WORD.findall((text or "").lower()):
        if w in _STOP or len(w) < 2:
            continue
        out.append(_DIGITS.sub("#", w) if any(c.isdigit() for c in w) else w)
    return out


def _slot(key: str) -> int:
    return zlib.crc32(key.encode("utf-8")) % DIM


def text_features(text: str, prefix: str, weight: float = 1.0) -> dict:
    """Words + word pairs of a text, under a prefix (so 'power' in a summary differs from 'power' in a note)."""
    toks = tokens(text)
    feats: dict = {}
    for t in toks:
        k = _slot(f"{prefix}:{t}")
        feats[k] = feats.get(k, 0.0) + weight
    for a, b in zip(toks, toks[1:]):
        k = _slot(f"{prefix}:{a}_{b}")
        feats[k] = feats.get(k, 0.0) + weight * 0.5
    return feats


def value_feature(name: str, value) -> dict:
    """A categorical fact, e.g. area=Haram, evidence=CHAIN."""
    return {_slot(f"{name}={str(value).lower()}"): 1.0} if value not in (None, "") else {}


def merge(*parts: dict) -> dict:
    out: dict = {}
    for p in parts:
        for k, v in p.items():
            out[k] = out.get(k, 0.0) + v
    return out


def normalize(vec: dict) -> dict:
    n = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / n for k, v in vec.items()}


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())
