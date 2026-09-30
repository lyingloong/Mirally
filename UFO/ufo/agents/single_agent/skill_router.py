"""Keyword-only skill routing for the Windows single agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ufo.agents.single_agent.schemas import DesktopObservation


class SkillDefinition(BaseModel):
    """A compact, trusted instruction bundle loaded from local YAML."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    keywords: List[str] = Field(min_length=1, max_length=40)
    applications: List[str] = Field(default_factory=list, max_length=20)
    instructions: List[str] = Field(min_length=1, max_length=12)
    success_criteria: List[str] = Field(default_factory=list, max_length=8)
    avoid: List[str] = Field(default_factory=list, max_length=8)
    priority: int = Field(default=0, ge=-100, le=100)


class SkillMatch(BaseModel):
    """Selected skill and the deterministic reasons for its selection."""

    skill: SkillDefinition
    score: int
    matched_keywords: List[str] = Field(default_factory=list)
    matched_applications: List[str] = Field(default_factory=list)

    def prompt_value(self) -> Dict[str, Any]:
        return {
            "id": self.skill.id,
            "name": self.skill.name,
            "description": self.skill.description,
            "instructions": self.skill.instructions,
            "success_criteria": self.skill.success_criteria,
            "avoid": self.skill.avoid,
        }

    def log_value(self) -> Dict[str, Any]:
        return {
            "id": self.skill.id,
            "score": self.score,
            "matched_keywords": self.matched_keywords,
            "matched_applications": self.matched_applications,
        }


class SkillRouter:
    """Select local skills with substring matching; no LLM is involved."""

    def __init__(self, skill_path: Path | str, *, top_k: int = 2) -> None:
        self.skill_path = Path(skill_path)
        self.top_k = max(0, top_k)
        self.skills = self._load_skills(self.skill_path)

    def select(
        self,
        request: str,
        observation: DesktopObservation,
    ) -> List[SkillMatch]:
        if not self.top_k:
            return []
        request_text = self._normalize(request)
        desktop_text = self._desktop_text(observation)
        matches: List[SkillMatch] = []

        for skill in self.skills:
            keywords = self._matching_terms(skill.keywords, request_text)
            applications = self._matching_terms(
                skill.applications, f"{request_text} {desktop_text}"
            )
            if not keywords:
                continue
            score = len(keywords) * 10 + len(applications) * 4 + skill.priority
            matches.append(
                SkillMatch(
                    skill=skill,
                    score=score,
                    matched_keywords=keywords,
                    matched_applications=applications,
                )
            )

        matches.sort(key=lambda match: (-match.score, match.skill.id))
        return matches[: self.top_k]

    @staticmethod
    def _load_skills(skill_path: Path) -> List[SkillDefinition]:
        if not skill_path.exists():
            return []
        skills = []
        seen_ids = set()
        for path in sorted(skill_path.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as stream:
                skill = SkillDefinition.model_validate(yaml.safe_load(stream))
            if skill.id in seen_ids:
                raise ValueError(f"Duplicate single-agent skill id: {skill.id}")
            seen_ids.add(skill.id)
            skills.append(skill)
        return skills

    @classmethod
    def _matching_terms(cls, candidates: Iterable[str], text: str) -> List[str]:
        return [
            candidate
            for candidate in candidates
            if cls._normalize(candidate) in text
        ]

    @staticmethod
    def _desktop_text(observation: DesktopObservation) -> str:
        values: List[str] = []
        active = observation.active_window or {}
        values.extend([str(active.get("name", "")), str(active.get("process", ""))])
        for window in observation.windows:
            values.extend(
                [str(window.get("name", "")), str(window.get("process", ""))]
            )
        return SkillRouter._normalize(" ".join(values))

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(str(value).casefold().split())
