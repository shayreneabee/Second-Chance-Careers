from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


SECOND_CHANCE_KEYWORDS = {
    "fair chance": 16,
    "second chance": 18,
    "background friendly": 14,
    "felony friendly": 20,
    "record considered": 14,
    "justice impacted": 16,
    "reentry": 16,
    "open to all backgrounds": 14,
    "veteran-friendly": 10,
    "veteran friendly": 10,
    "no background check": 18,
    "no degree required": 8,
    "paid training": 8,
    "entry level": 5,
    "warehouse": 4,
    "cdl": 4,
    "skilled trades": 4,
}


@dataclass
class NormalizedJob:
    source: str
    external_id: str
    title: str
    company: str
    location: str = ""
    remote_type: str = ""
    employment_type: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    description: str = ""
    requirements: str = ""
    apply_url: str = ""
    posted_at: str = ""
    expires_at: str = ""
    tags: list[str] = field(default_factory=list)
    second_chance_score: int = 0
    is_active: bool = True
    raw: dict = field(default_factory=dict)

    @property
    def fallback_hash(self) -> str:
        return stable_job_hash(self.title, self.company, self.location, self.apply_url)

    def as_record(self) -> dict:
        data = asdict(self)
        data["tags"] = ", ".join(self.tags)
        data["is_active"] = 1 if self.is_active else 0
        data["fallback_hash"] = self.fallback_hash
        data["raw_json"] = json.dumps(self.raw or {}, ensure_ascii=True)
        return data


@dataclass
class ProviderResult:
    provider: str
    jobs: list[NormalizedJob] = field(default_factory=list)
    skipped: bool = False
    warning: str = ""
    error: str = ""


class JobProvider:
    name = "provider"
    required_env: tuple[str, ...] = ()

    def missing_env(self) -> list[str]:
        return [key for key in self.required_env if not os.getenv(key)]

    def fetch(self, query: str, location: str, limit: int) -> ProviderResult:
        missing = self.missing_env()
        if missing:
            return ProviderResult(
                provider=self.name,
                skipped=True,
                warning=f"Missing environment variables: {', '.join(missing)}",
            )
        raise NotImplementedError


def clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_date(value: object) -> str:
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return ""
    for parser in (
        lambda item: datetime.fromisoformat(item.replace("Z", "+00:00")),
        parsedate_to_datetime,
    ):
        try:
            return parser(text).isoformat()
        except (TypeError, ValueError, IndexError):
            continue
    return text[:64]


def stable_job_hash(title: str, company: str, location: str, apply_url: str) -> str:
    key = "|".join(
        re.sub(r"\s+", " ", item or "").strip().lower()
        for item in (title, company, location, apply_url)
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def score_second_chance_job(*parts: object) -> int:
    haystack = " ".join(clean_text(part).lower() for part in parts if part)
    score = 0
    for keyword, points in SECOND_CHANCE_KEYWORDS.items():
        if keyword in haystack:
            score += points
    return min(score, 100)


def infer_tags(*parts: object) -> list[str]:
    haystack = " ".join(clean_text(part).lower() for part in parts if part)
    tag_rules = {
        "Second-chance friendly": ["second chance", "fair chance", "felony friendly", "reentry"],
        "Veteran friendly": ["veteran-friendly", "veteran friendly", "veterans"],
        "No degree required": ["no degree", "high school", "ged"],
        "Paid training": ["paid training", "training provided"],
        "Entry-level": ["entry level", "entry-level", "no experience"],
        "CDL": ["cdl", "commercial driver"],
        "Warehouse": ["warehouse", "forklift", "distribution"],
        "Healthcare": ["healthcare", "patient", "medical", "clinic"],
        "Tech": ["software", "technology", "it support", "developer"],
        "Customer service": ["customer service", "call center", "support"],
        "Food service": ["restaurant", "food service", "cook", "kitchen"],
        "Skilled trades": ["electrician", "plumber", "hvac", "welder", "construction"],
    }
    tags = []
    for tag, needles in tag_rules.items():
        if any(needle in haystack for needle in needles):
            tags.append(tag)
    return tags


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SecondChanceCareers/1.0",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from provider") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Provider network error: {exc.reason}") from exc

