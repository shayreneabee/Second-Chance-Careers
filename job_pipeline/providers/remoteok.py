from __future__ import annotations

from ..base import JobProvider, NormalizedJob, ProviderResult, clean_text, fetch_json, infer_tags, normalize_date, score_second_chance_job


class RemoteOKProvider(JobProvider):
    name = "remoteok"
    required_env = ()

    def fetch(self, query: str, location: str, limit: int) -> ProviderResult:
        try:
            payload = fetch_json("https://remoteok.com/api")
        except RuntimeError as exc:
            return ProviderResult(self.name, error=str(exc))

        query_text = (query or "").lower().strip()
        jobs = []
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            title = clean_text(item.get("position"))
            company = clean_text(item.get("company"))
            description = clean_text(item.get("description"))
            if query_text and query_text not in f"{title} {company} {description} {' '.join(item.get('tags') or [])}".lower():
                continue
            tags = infer_tags(title, company, description, " ".join(item.get("tags") or []))
            jobs.append(
                NormalizedJob(
                    source=self.name,
                    external_id=str(item.get("id") or item.get("slug") or ""),
                    title=title,
                    company=company,
                    location="Remote",
                    remote_type="Remote",
                    employment_type="Remote",
                    salary_min=item.get("salary_min"),
                    salary_max=item.get("salary_max"),
                    description=description,
                    apply_url=clean_text(item.get("url") or item.get("apply_url")),
                    posted_at=normalize_date(item.get("date") or item.get("epoch")),
                    tags=tags,
                    second_chance_score=score_second_chance_job(title, company, description, " ".join(tags)),
                    raw=item,
                )
            )
            if len(jobs) >= limit:
                break
        return ProviderResult(self.name, jobs=jobs)

