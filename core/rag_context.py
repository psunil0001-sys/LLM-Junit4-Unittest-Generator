# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Provides deterministic reusable ranking and formatting for retrieved context.
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Iterable


def normalize_terms(values: Iterable[str] | str | None) -> frozenset[str]:
    if isinstance(values, str):
        values = [values]
    return frozenset(str(value).strip().lower() for value in (values or []) if str(value).strip())


def tokenize(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z][a-z0-9_]{2,}", (text or "").lower()))


@dataclass(frozen=True)
class RetrievalQuery:
    tags: frozenset[str] = field(default_factory=frozenset)
    terms: frozenset[str] = field(default_factory=frozenset)
    phase: str = ""


@dataclass(frozen=True)
class ContextItem:
    key: str
    text: str
    tags: frozenset[str] = field(default_factory=frozenset)
    phases: frozenset[str] = field(default_factory=frozenset)
    priority: int = 0
    payload: Any = None


@dataclass(frozen=True)
class RankedContextItem:
    item: ContextItem
    score: float
    matched_tags: frozenset[str]
    matched_terms: frozenset[str]


def rank_context_items(
    items: Iterable[ContextItem],
    query: RetrievalQuery,
    *,
    limit: int = 5,
    max_chars: int | None = None,
    eligibility_fn: Callable[[ContextItem, RetrievalQuery], bool] | None = None,
    extra_score_fn: Callable[[ContextItem, RetrievalQuery], float] | None = None,
) -> list[RankedContextItem]:
    query_tags = normalize_terms(query.tags)
    query_terms = normalize_terms(query.terms)
    phase = (query.phase or "").lower()
    ranked = []
    seen_text = set()

    for item in items:
        item_phases = normalize_terms(item.phases)
        if phase and item_phases and phase not in item_phases:
            continue
        explicitly_eligible = eligibility_fn(item, query) if eligibility_fn else False
        if eligibility_fn and not explicitly_eligible:
            continue
        fingerprint = re.sub(r"\s+", " ", item.text).strip().lower()
        if not fingerprint or fingerprint in seen_text:
            continue
        seen_text.add(fingerprint)

        item_tags = normalize_terms(item.tags)
        item_terms = tokenize(f"{item.key}\n{item.text}")
        matched_tags = query_tags & item_tags
        matched_terms = query_terms & item_terms
        universal = "universal" in item_tags
        score = float(item.priority + len(matched_tags) * 100 + len(matched_terms) * 4 + (50 if universal else 0))
        if extra_score_fn:
            score += float(extra_score_fn(item, query))
        if not explicitly_eligible and not universal and not matched_tags and not matched_terms:
            continue
        ranked.append(RankedContextItem(item, score, matched_tags, matched_terms))

    ranked.sort(key=lambda value: (-value.score, value.item.key))
    selected = []
    used_chars = 0
    for value in ranked:
        if len(selected) >= max(0, limit):
            break
        item_chars = len(value.item.text)
        if max_chars is not None and selected and used_chars + item_chars > max_chars:
            continue
        selected.append(value)
        used_chars += item_chars
    return selected


def format_context_blocks(
    blocks: Iterable[str],
    *,
    heading: str = "",
    max_chars: int | None = None,
) -> str:
    text_blocks = [heading] if heading else []
    text_blocks.extend(str(block).strip() for block in blocks if str(block).strip())
    text = "\n\n".join(text_blocks).strip()
    if max_chars is not None:
        return text[:max_chars].rstrip()
    return text
