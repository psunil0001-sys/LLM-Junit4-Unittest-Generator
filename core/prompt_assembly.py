# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Parses, retrieves, and assembles reusable prompt-rule documents.
from dataclasses import dataclass
import hashlib
import re
from typing import Callable, Mapping, Sequence

from UnitTest_gen.core.rag_context import ContextItem, RetrievalQuery, RankedContextItem, normalize_terms, rank_context_items


@dataclass(frozen=True)
class RuleDocument:
    rule_id: str
    source_name: str
    title: str
    text: str
    tags: frozenset[str]
    phases: frozenset[str]
    priority: int = 0


@dataclass(frozen=True)
class RetrievedRuleContext:
    documents: tuple[RuleDocument, ...]
    scores: tuple[float, ...]
    text: str


@dataclass(frozen=True)
class PromptSection:
    title: str
    content: str
    max_chars: int | None = None
    keep_if_empty: bool = False


@dataclass(frozen=True)
class PromptBuildSpec:
    system_prompt: str
    sections: Sequence[PromptSection]


@dataclass(frozen=True)
class PromptMessageBundle:
    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, str], ...]


def _stable_rule_id(source_name: str, title: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:48] or "rule"
    return f"{source_name.lower()}:{slug}:{digest}"


def _split_rule_text(source_name: str, text: str) -> list[tuple[str, str]]:
    lines = (text or "").strip().splitlines()
    sections = []
    current_title = ""
    current_lines = []
    for line in lines:
        match = re.match(r"^\s*\d+\.\s+(.+?):\s*$", line)
        if match:
            if current_lines:
                sections.append((current_title or source_name, "\n".join(current_lines).strip()))
            current_title = match.group(1).strip()
            current_lines = [line]
        elif current_title:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title or source_name, "\n".join(current_lines).strip()))
    if sections:
        split_sections = []
        for title, section_text in sections:
            section_lines = section_text.splitlines()
            chunks = []
            current = []
            for line in section_lines[1:]:
                if line.strip().startswith("- "):
                    if current:
                        chunks.append("\n".join(current).strip())
                    current = [line.strip()]
                elif current and line.strip():
                    current.append(line.rstrip())
            if current:
                chunks.append("\n".join(current).strip())
            if chunks:
                split_sections.extend((title, chunk) for chunk in chunks if chunk)
            else:
                split_sections.append((title, section_text.strip()))
        return split_sections

    bullets = [line.strip() for line in lines if line.strip().startswith("- ")]
    return [(f"{source_name} rule {index}", bullet) for index, bullet in enumerate(bullets, 1)] or [(source_name, text.strip())]


def build_rule_corpus(
    rule_sources: Mapping[str, str],
    metadata_callback: Callable[[str, str, str], tuple[set[str], set[str], int]],
) -> tuple[RuleDocument, ...]:
    documents = []
    for source_name, source_text in sorted(rule_sources.items()):
        for title, text in _split_rule_text(source_name, source_text):
            if not text:
                continue
            tags, phases, priority = metadata_callback(source_name, title, text)
            documents.append(
                RuleDocument(
                    _stable_rule_id(source_name, title, text),
                    source_name,
                    title,
                    text,
                    normalize_terms(tags),
                    normalize_terms(phases),
                    priority,
                )
            )
    return tuple(documents)


def retrieve_rule_context(
    corpus: tuple[RuleDocument, ...],
    query: RetrievalQuery,
    *,
    limit: int = 16,
    max_chars: int = 22000,
) -> RetrievedRuleContext:
    items = [ContextItem(doc.rule_id, doc.text, doc.tags, doc.phases, doc.priority, doc) for doc in corpus]
    query_tags = normalize_terms(query.tags)
    ranked: list[RankedContextItem] = rank_context_items(
        items,
        query,
        limit=limit,
        max_chars=max_chars,
        eligibility_fn=lambda item, _: bool("universal" in item.tags or query_tags & item.tags),
    )
    documents = tuple(value.item.payload for value in ranked)
    text = format_retrieved_rule_documents(documents)
    return RetrievedRuleContext(documents, tuple(value.score for value in ranked), text)


def format_retrieved_rule_documents(documents: tuple[RuleDocument, ...]) -> str:
    blocks: list[str] = []
    for index, document in enumerate(documents, start=1):
        title = "" if document.title == document.source_name or document.title.startswith(f"{document.source_name} rule ") else document.title
        title_line = f"Section: {title}\n" if title else ""
        blocks.append(f"{document.source_name} rule {index}:\n{title_line}{document.text.strip()}")
    return "\n\n".join(block for block in blocks if block.strip())


def assemble_prompt_sections(sections: list[PromptSection]) -> str:
    blocks = []
    seen = set()
    for section in sections:
        content = (section.content or "").strip()
        if not content and not section.keep_if_empty:
            continue
        if section.max_chars is not None and len(content) > section.max_chars:
            content = content[:section.max_chars].rstrip() + "\n... truncated ..."
        fingerprint = re.sub(r"\s+", " ", f"{section.title}\n{content}").strip().lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if section.title:
            blocks.append(f"### {section.title}\n{content}".strip())
        else:
            blocks.append(content)
    return "\n\n".join(blocks).strip()


def format_template(template: str, values: Mapping[str, object] | None = None, **kwargs) -> str:
    template_values = dict(values or {})
    template_values.update(kwargs)
    return (template or "").format(**template_values)


def build_prompt_bundle(spec: PromptBuildSpec) -> PromptMessageBundle:
    system_prompt = (spec.system_prompt or "").strip()
    user_prompt = assemble_prompt_sections(list(spec.sections))
    messages = (
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    )
    return PromptMessageBundle(system_prompt, user_prompt, messages)
