"""Skill retrieval.

Injecting every skill into every request does not scale, so retrieval selects
the few that matter for the current task.  The interface is deliberately
narrow (:class:`SkillRetriever`) so a semantic/embedding retriever can be
dropped in later without touching the agent.

The shipped implementation is keyword/BM25-ish scoring, which is accurate
enough for a library of tens to low hundreds of skills and costs nothing.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from lema.skills.model import STOPWORDS, Skill, SkillScope
from lema.skills.registry import SkillRegistry

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.#_-]{1,}")


def tokenize(text: str) -> list[str]:
    tokens = [t.lower() for t in TOKEN_RE.findall(text or "")]
    out: list[str] = []
    for token in tokens:
        if token in STOPWORDS or len(token) < 3:
            continue
        out.append(token)
        # Split composite identifiers so "docker-compose" also matches "compose".
        for part in re.split(r"[-_.]", token):
            if len(part) >= 3 and part not in STOPWORDS and part != token:
                out.append(part)
    return out


@dataclass
class ScoredSkill:
    skill: Skill
    score: float
    reasons: list[str]


class SkillRetriever(ABC):
    """Strategy interface for selecting relevant skills."""

    name = "base"

    @abstractmethod
    def retrieve(
        self, query: str, registry: SkillRegistry, *, limit: int = 4, threshold: float = 0.0
    ) -> list[ScoredSkill]:
        """Return the most relevant skills for ``query``, best first."""


class KeywordRetriever(SkillRetriever):
    """TF-IDF-ish keyword matcher with field weighting."""

    name = "keyword"

    #: Field weights - a hit in the name is worth far more than one in prose.
    W_NAME = 8.0
    W_KEYWORD = 3.0
    W_TITLE = 2.5
    W_DESCRIPTION = 2.0
    W_SECTION = 1.2
    W_BODY = 0.35

    #: Raw score considered "clearly relevant". Scores are squashed through
    #: ``raw / (raw + SATURATION)`` so that the returned 0..1 relevance is an
    #: ABSOLUTE measure. Normalising by the best match instead would force the
    #: top-ranked skill to 1.0 even for a query that matches nothing, which
    #: makes `relevance_threshold` useless and injects noise into the prompt.
    SATURATION = 12.0

    def retrieve(
        self, query: str, registry: SkillRegistry, *, limit: int = 4, threshold: float = 0.0
    ) -> list[ScoredSkill]:
        skills = registry.all()
        if not skills or not query.strip():
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        query_set = set(query_tokens)

        # Document frequency, so ubiquitous words ("file", "run") count less.
        doc_freq: dict[str, int] = {}
        doc_tokens: dict[str, set[str]] = {}
        for skill in skills:
            tokens = set(tokenize(skill.search_text() + " " + " ".join(skill.sections)))
            doc_tokens[skill.name] = tokens
            for token in tokens:
                doc_freq[token] = doc_freq.get(token, 0) + 1

        total_docs = max(1, len(skills))
        scored: list[ScoredSkill] = []

        for skill in skills:
            score = 0.0
            reasons: list[str] = []
            name_tokens = set(tokenize(skill.name.replace("-", " ")))
            keyword_tokens = set(tokenize(" ".join(skill.keywords)))
            title_tokens = set(tokenize(skill.title))
            description_tokens = set(tokenize(skill.description))
            section_tokens = set(tokenize(" ".join(skill.sections)))
            body_tokens = set(tokenize(skill.content[:8000]))

            for token in query_set:
                idf = math.log(1 + total_docs / (1 + doc_freq.get(token, 0)))
                hit = 0.0
                if token in name_tokens:
                    hit += self.W_NAME
                    reasons.append(f"name:{token}")
                if token in keyword_tokens:
                    hit += self.W_KEYWORD
                if token in title_tokens:
                    hit += self.W_TITLE
                if token in description_tokens:
                    hit += self.W_DESCRIPTION
                if token in section_tokens:
                    hit += self.W_SECTION
                if token in body_tokens:
                    hit += self.W_BODY
                score += hit * idf

            # Exact name mention in the query is decisive.
            lowered_query = query.lower()
            if skill.name in lowered_query or skill.name.replace("-", " ") in lowered_query:
                score += 40.0
                reasons.append("exact-name")

            # Project skills outrank equally-relevant global/builtin ones.
            if skill.scope is SkillScope.PROJECT:
                score *= 1.25
            elif skill.scope is SkillScope.GLOBAL:
                score *= 1.1

            if score <= 0:
                continue
            scored.append(ScoredSkill(skill=skill, score=score, reasons=reasons[:4]))

        if not scored:
            return []

        scored.sort(key=lambda s: -s.score)
        # Squash to 0..1 while preserving the ranking. Monotonic, so ordering
        # is unchanged, but the value means "how relevant" rather than
        # "how relevant compared to the best thing I happen to have".
        normalized = [
            ScoredSkill(
                skill=s.skill,
                score=s.score / (s.score + self.SATURATION),
                reasons=s.reasons,
            )
            for s in scored
        ]
        return [s for s in normalized if s.score >= threshold][:limit]


_RETRIEVERS: dict[str, type[SkillRetriever]] = {"keyword": KeywordRetriever}


def register_retriever(name: str, cls: type[SkillRetriever]) -> None:
    _RETRIEVERS[name] = cls


def get_retriever(name: str = "keyword") -> SkillRetriever:
    cls = _RETRIEVERS.get(name, KeywordRetriever)
    return cls()


def select_skills(
    query: str,
    registry: SkillRegistry,
    *,
    limit: int = 4,
    threshold: float = 0.15,
    strategy: str = "keyword",
    always_include: Sequence[str] = (),
) -> list[Skill]:
    """Convenience wrapper used by the context builder."""
    retriever = get_retriever(strategy)
    selected = [s.skill for s in retriever.retrieve(query, registry, limit=limit, threshold=threshold)]
    seen = {s.name for s in selected}
    for name in always_include:
        skill = registry.get(name)
        if skill is not None and skill.name not in seen:
            selected.insert(0, skill)
            seen.add(skill.name)
    return selected[: max(limit, len(always_include))]
