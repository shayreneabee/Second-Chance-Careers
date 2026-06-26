from __future__ import annotations

import os
from urllib.parse import urlencode

from ..base import JobProvider, NormalizedJob, ProviderResult, clean_text, fetch_json, infer_tags, normalize_date, score_second_chance_job


class AdzunaProvider(JobProvider):
    name = "adzuna"
    required_env = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")

    def fetch(self, query: str, location: str, limit: int) -> ProviderResult:
        missing = self.missing_env()
        if missing:
            return ProviderResult(self.name, skipped=True, warning=f"Missing environment variables: {', '.join(missing)}")

        params = urlencode(
            {
                "app_id": os.getenv("ADZUNA_APP_ID", ""),
                "app_key": os.getenv("ADZUNA_APP_KEY", ""),
                "results_per_page": max(1, min(limit, 50)),
                "what": query,
                "where": location,
                "content-type": "application/json",
            }
        )
        try:
            payload = fetch_json(f"https://api.adzuna.com/v1/api/jobs/us/search/1?{params}")
        except RuntimeError as exc:
            return ProviderResult(self.name, error=str(exc))

        jobs = []
        for item in (payload or {}).get("results", []):
            description = clean_text(item.get("description"))
            title = clean_text(item.get("title"))
            company = clean_text((item.get("company") or {}).get("display_name"))
            area = (item.get("location") or {}).get("area") or []
            job_location = ", ".join(area[-3:]) if area else clean_text((item.get("location") or {}).get("display_name"))
            tags = infer_tags(title, company, description, item.get("category", {}).get("label"))
            score = score_second_chance_job(title, company, description, " ".join(tags))
            jobs.append(
                NormalizedJob(
                    source=self.name,
                    external_id=str(item.get("id") or ""),
                    title=title,
                    company=company,
                    location=job_location,
                    remote_type="Remote" if "remote" in f"{title} {description}".lower() else "",
                    employment_type=clean_text((item.get("contract_type") or "").replace("_", " ").title()),
                    salary_min=item.get("salary_min"),
                    salary_max=item.get("salary_max"),
                    description=description,
                    apply_url=clean_text(item.get("redirect_url")),
                    posted_at=normalize_date(item.get("created")),
                    tags=tags,
                    second_chance_score=score,
                    raw=item,
                )
            )
        return ProviderResult(self.name, jobs=jobs)

