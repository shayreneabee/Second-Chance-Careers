from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .providers import default_providers


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_job_pipeline_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT DEFAULT '',
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            remote_type TEXT DEFAULT '',
            employment_type TEXT DEFAULT '',
            salary_min REAL,
            salary_max REAL,
            description TEXT DEFAULT '',
            requirements TEXT DEFAULT '',
            apply_url TEXT DEFAULT '',
            posted_at TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            second_chance_score INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            approval_status TEXT DEFAULT 'approved',
            is_hidden INTEGER DEFAULT 0,
            fallback_hash TEXT DEFAULT '',
            raw_json TEXT DEFAULT '{}',
            last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_source_external ON jobs(source, external_id) WHERE external_id != ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_fallback_hash ON jobs(fallback_hash) WHERE fallback_hash != ''")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            fetched_count INTEGER DEFAULT 0,
            upserted_count INTEGER DEFAULT 0,
            deactivated_count INTEGER DEFAULT 0,
            warning TEXT DEFAULT '',
            error TEXT DEFAULT '',
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for column, definition in {
        "source": "TEXT DEFAULT 'manual'",
        "external_id": "TEXT DEFAULT ''",
        "salary_min": "REAL",
        "salary_max": "REAL",
        "posted_at": "TEXT DEFAULT ''",
        "expires_at": "TEXT DEFAULT ''",
        "second_chance_score": "INTEGER DEFAULT 0",
        "is_active": "INTEGER DEFAULT 1",
        "import_hash": "TEXT DEFAULT ''",
        "last_seen_at": "TEXT DEFAULT ''",
        "provider_error": "TEXT DEFAULT ''",
    }.items():
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(second_chance_jobs)").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE second_chance_jobs ADD COLUMN {column} {definition}")


def location_parts(location: str) -> tuple[str, str]:
    parts = [part.strip() for part in (location or "").split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return (parts[0], "") if parts else ("", "")


def boolean_from_tags(tags: str, *needles: str) -> int:
    haystack = (tags or "").lower()
    return 1 if any(needle.lower() in haystack for needle in needles) else 0


def upsert_job(conn: sqlite3.Connection, job) -> bool:
    record = job.as_record()
    existing = conn.execute(
        "SELECT id FROM jobs WHERE source = ? AND external_id = ?",
        (record["source"], record["external_id"]),
    ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id FROM jobs WHERE fallback_hash = ?",
            (record["fallback_hash"],),
        ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE jobs
            SET title = ?, company = ?, location = ?, remote_type = ?, employment_type = ?,
                salary_min = ?, salary_max = ?, description = ?, requirements = ?, apply_url = ?,
                posted_at = ?, expires_at = ?, tags = ?, second_chance_score = ?,
                is_active = 1, raw_json = ?, last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                record["title"], record["company"], record["location"], record["remote_type"],
                record["employment_type"], record["salary_min"], record["salary_max"],
                record["description"], record["requirements"], record["apply_url"],
                record["posted_at"], record["expires_at"], record["tags"],
                record["second_chance_score"], record["raw_json"], existing["id"],
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO jobs (
                source, external_id, title, company, location, remote_type, employment_type,
                salary_min, salary_max, description, requirements, apply_url, posted_at,
                expires_at, tags, second_chance_score, is_active, fallback_hash, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                record["source"], record["external_id"], record["title"], record["company"],
                record["location"], record["remote_type"], record["employment_type"],
                record["salary_min"], record["salary_max"], record["description"],
                record["requirements"], record["apply_url"], record["posted_at"],
                record["expires_at"], record["tags"], record["second_chance_score"],
                record["fallback_hash"], record["raw_json"],
            ),
        )

    city, state = location_parts(record["location"])
    public_existing = conn.execute(
        "SELECT id FROM second_chance_jobs WHERE source = ? AND external_id = ?",
        (record["source"], record["external_id"]),
    ).fetchone()
    if not public_existing:
        public_existing = conn.execute(
            "SELECT id FROM second_chance_jobs WHERE import_hash = ?",
            (record["fallback_hash"],),
        ).fetchone()

    pay_range = ""
    if record["salary_min"] and record["salary_max"]:
        pay_range = f"${record['salary_min']:,.0f}-${record['salary_max']:,.0f}"
    elif record["salary_min"]:
        pay_range = f"From ${record['salary_min']:,.0f}"
    elif record["salary_max"]:
        pay_range = f"Up to ${record['salary_max']:,.0f}"

    public_values = (
        record["title"],
        record["company"],
        first_matching_industry(record["tags"], record["description"]),
        city,
        state,
        record["location"],
        record["remote_type"],
        pay_range,
        record["employment_type"],
        record["description"],
        record["requirements"],
        score_note(record["second_chance_score"]),
        record["apply_url"],
        record["tags"],
        boolean_from_tags(record["tags"], "Entry-level"),
        boolean_from_tags(record["tags"], "Second-chance", "Felony friendly"),
        boolean_from_tags(record["tags"], "Veteran"),
        boolean_from_tags(record["tags"], "No degree"),
        "approved",
        record["source"],
        record["external_id"],
        record["salary_min"],
        record["salary_max"],
        record["posted_at"],
        record["expires_at"],
        record["second_chance_score"],
        record["fallback_hash"],
    )
    if public_existing:
        conn.execute(
            """
            UPDATE second_chance_jobs
            SET job_title = ?, company_name = ?, industry = ?, city = ?, state = ?,
                location = ?, work_mode = ?, pay_range = ?, employment_type = ?,
                description = ?, requirements = ?, background_notes = ?, apply_link = ?,
                tags_csv = ?, is_entry_level = ?, is_felony_friendly = ?,
                is_veteran_friendly = ?, no_degree_required = ?, status = ?,
                source = ?, external_id = ?, salary_min = ?, salary_max = ?,
                posted_at = ?, expires_at = ?, second_chance_score = ?,
                is_active = 1, import_hash = ?, last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*public_values, public_existing["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO second_chance_jobs (
                job_title, company_name, industry, city, state, location, work_mode,
                pay_range, employment_type, description, requirements, background_notes,
                apply_link, tags_csv, is_entry_level, is_felony_friendly,
                is_veteran_friendly, no_degree_required, status, source, external_id,
                salary_min, salary_max, posted_at, expires_at, second_chance_score,
                is_active, import_hash, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
            """,
            public_values,
        )
    return True


def first_matching_industry(tags: str, description: str) -> str:
    haystack = f"{tags} {description}".lower()
    for label in ("Warehouse", "Healthcare", "Tech", "Customer service", "Food service", "Skilled trades", "CDL"):
        if label.lower() in haystack:
            return label
    return "General"


def score_note(score: int) -> str:
    if score >= 70:
        return "Strong second-chance language detected in this listing."
    if score >= 35:
        return "Some second-chance-friendly signals detected. Review the posting for details."
    return "No explicit second-chance language detected; review employer requirements before applying."


def sync_jobs(db_path, query="entry level", location="United States", limit=25, provider_names=None, dry_run=False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    summary = []
    try:
        ensure_job_pipeline_schema(conn)
        selected = {name.lower() for name in provider_names or []}
        for provider in default_providers():
            if selected and provider.name not in selected:
                continue
            started_at = utc_now()
            if dry_run:
                missing = provider.missing_env()
                result = {
                    "provider": provider.name,
                    "status": "dry-run",
                    "fetched": 0,
                    "upserted": 0,
                    "deactivated": 0,
                    "warning": f"Missing environment variables: {', '.join(missing)}" if missing else "",
                    "error": "",
                }
                summary.append(result)
                continue
            provider_result = provider.fetch(query=query, location=location, limit=limit)
            status = "skipped" if provider_result.skipped else "success"
            if provider_result.error:
                status = "failed"
            upserted = 0
            seen_hashes = []
            if provider_result.jobs:
                for job in provider_result.jobs:
                    upsert_job(conn, job)
                    upserted += 1
                    seen_hashes.append(job.fallback_hash)
            deactivated = 0
            if seen_hashes and status == "success":
                placeholders = ",".join("?" for _ in seen_hashes)
                cursor = conn.execute(
                    f"""
                    UPDATE jobs
                    SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE source = ? AND fallback_hash NOT IN ({placeholders})
                    """,
                    (provider.name, *seen_hashes),
                )
                deactivated = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            conn.execute(
                """
                INSERT INTO job_sync_runs (
                    provider, status, fetched_count, upserted_count, deactivated_count,
                    warning, error, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    provider.name,
                    status,
                    len(provider_result.jobs),
                    upserted,
                    deactivated,
                    provider_result.warning,
                    provider_result.error,
                    started_at,
                ),
            )
            summary.append(
                {
                    "provider": provider.name,
                    "status": status,
                    "fetched": len(provider_result.jobs),
                    "upserted": upserted,
                    "deactivated": deactivated,
                    "warning": provider_result.warning,
                    "error": provider_result.error,
                }
            )
        conn.commit()
        return summary
    finally:
        conn.close()
