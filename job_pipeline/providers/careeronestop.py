from __future__ import annotations

import os
from urllib.parse import quote

from ..base import JobProvider, NormalizedJob, ProviderResult, clean_text, fetch_json, infer_tags, normalize_date, score_second_chance_job


class CareerOneStopProvider(JobProvider):
    name = "careeronestop"
    required_env = ("CAREERONESTOP_API_KEY",)

    def fetch(self, query: str, location: str, limit: int) -> ProviderResult:
        missing = self.missing_env()
        if missing:
            return ProviderResult(self.name, skipped=True, warning=f"Missing environment variables: {', '.join(missing)}")

        user_id = os.getenv("CAREERONESTOP_USER_ID", "").strip()
        if not user_id:
            return ProviderResult(
                self.name,
                skipped=True,
                warning="CAREERONESTOP_USER_ID is required by the CareerOneStop Web API in addition to CAREERONESTOP_API_KEY.",
            )

        keyword = quote(query or "entry level")
        place = quote(location or "United States")
        url = f"https://api.careeronestop.org/v1/jobsearch/{user_id}/{keyword}/{place}/25/0/{max(1, min(limit, 50))}"
        headers = {"Authorization": f"Bearer {os.getenv('CAREERONESTOP_API_KEY', '')}"}
        try:
            payload = fetch_json(url, headers=headers)
        except RuntimeError as exc:
            return ProviderResult(self.name, error=str(exc))

        items = (payload or {}).get("Jobs") or (payload or {}).get("jobs") or []
        jobs = []
        for item in items:
            title = clean_text(item.get("JobTitle") or item.get("title"))
            company = clean_text(item.get("Company") or item.get("company"))
            description = clean_text(item.get("Description") or item.get("description"))
            job_location = clean_text(item.get("Location") or item.get("location"))
            apply_url = clean_text(item.get("URL") or item.get("ApplyUrl") or item.get("apply_url"))
            tags = infer_tags(title, company, description)
            jobs.append(
                NormalizedJob(
                    source=self.name,
                    external_id=clean_text(item.get("JvId") or item.get("id") or apply_url),
                    title=title,
                    company=company,
                    location=job_location,
                    remote_type="Remote" if "remote" in f"{title} {description}".lower() else "",
                    description=description,
                    apply_url=apply_url,
                    posted_at=normalize_date(item.get("PostedDate") or item.get("posted_at")),
                    tags=tags,
                    second_chance_score=score_second_chance_job(title, company, description, " ".join(tags)),
                    raw=item,
                )
            )
        return ProviderResult(self.name, jobs=jobs)

