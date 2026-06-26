import argparse

from app import DB_PATH, init_db
from job_pipeline.sync import sync_jobs


def main():
    parser = argparse.ArgumentParser(description="Second Chance Careers management commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync_jobs", help="Import jobs from configured providers")
    sync_parser.add_argument("--query", default="entry level", help="Search keyword")
    sync_parser.add_argument("--location", default="United States", help="Search location")
    sync_parser.add_argument("--limit", type=int, default=25, help="Max jobs per provider")
    sync_parser.add_argument("--provider", action="append", help="Limit sync to one provider; can be repeated")
    sync_parser.add_argument("--dry-run", action="store_true", help="Check provider configuration without fetching")

    args = parser.parse_args()
    init_db()

    if args.command == "sync_jobs":
        results = sync_jobs(
            DB_PATH,
            query=args.query,
            location=args.location,
            limit=args.limit,
            provider_names=args.provider,
            dry_run=args.dry_run,
        )
        for result in results:
            warning = f" warning={result['warning']}" if result.get("warning") else ""
            error = f" error={result['error']}" if result.get("error") else ""
            print(
                f"{result['provider']}: {result['status']} "
                f"fetched={result['fetched']} upserted={result['upserted']} "
                f"deactivated={result['deactivated']}{warning}{error}"
            )


if __name__ == "__main__":
    main()
