from __future__ import annotations

import os
from urllib.parse import urlencode

from ..base import JobProvider, NormalizedJob, ProviderResult, clean_text, fetch_json, infer_tags, normalize_date, score_second_chance_job


class USAJobsProvider(JobProvider):
    name = "usajobs"
    required_env = ("USAJOBS_USER_AGENT", "USAJOBS_AUTH_KEY")

    def fetch(self, query: str, location: str, limit: int) -> ProviderResult:
        missing = self.missing_env()
        if missing:
            return ProviderResult(self.name, skipped=True, warning=f"Missing environment variables: {', '.join(missing)}")

        params = urlencode(
            {
                "Keyword": query or "entry level",
                "LocationName": location or "",
                "ResultsPerPage": max(1, min(limit, 100)),
            }
        )
        headers = {
            "User-Agent": os.getenv("USAJOBS_USER_AGENT", ""),
            "Authorization-Key": os.getenv("USAJOBS_AUTH_KEY", ""),
        }
        try:
            payload = fetch_json(f"https://data.usajobs.gov/api/search?{params}", headers=headers)
        except RuntimeError as exc:
            return ProviderResult(self.name, error=str(exc))

        items = (((payload or {}).get("SearchResult") or {}).get("SearchResultItems") or [])
        jobs = []
        for wrapper in items:
            item = wrapper.get("MatchedObjectDescriptor") or {}
            details = item.get("UserArea", {}).get("Details", {})
            title = clean_text(item.get("PositionTitle"))
            company = clean_text(item.get("OrganizationName") or item.get("DepartmentName") or "USAJOBS")
            locations = ", ".join(clean_text(loc.get("LocationName")) for loc in item.get("PositionLocation", []) if loc)
            description = clean_text(item.get("QualificationSummary") or details.get("JobSummary"))
            requirements = clean_text(details.get("Requirements") or details.get("Evaluations"))
            tags = infer_tags(title, company, description, requirements)
            jobs.append(
                NormalizedJob(
                    source=self.name,
                    external_id=clean_text(item.get("PositionID") or item.get("PositionURI")),
                    title=title,
                    company=company,
                    location=locations,
                    remote_type="Remote" if "remote" in f"{title} {description} {locations}".lower() else "",
                    employment_type=clean_text(details.get("JobGrade") or item.get("PositionSchedule", [{}])[0].get("Name")),
                    description=description,
                    requirements=requirements,
                    apply_url=clean_text(item.get("PositionURI")),
                    posted_at=normalize_date(item.get("PublicationStartDate")),
                    expires_at=normalize_date(item.get("ApplicationCloseDate")),
                    tags=tags,
                    second_chance_score=score_second_chance_job(title, company, description, requirements, " ".join(tags)),
                    raw=item,
                )
            )
        return ProviderResult(self.name, jobs=jobs)

