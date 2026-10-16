"""Wipe the preprod database and seed it with 18 fake jobs spread over every pipeline stage.

Runs on every preprod deploy (scripts/agent/preprod.sh, via PREPROD_SEED_CMD) in a one-off container of the preprod
image with this checkout mounted at /seed. It refuses any database except `applypilot_preprod`, since it deletes rows.

    python scripts/seed_preprod.py                 # target: STRELITZIA_DATABASE_URL (must be .../applypilot_preprod)
    python scripts/seed_preprod.py --sqlite PATH   # seed a SQLite file instead (tests)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from strelitzia import tracking, users
from strelitzia.config import env
from strelitzia.database import init_db, job_key

PREPROD_DB = "applypilot_preprod"
DEFAULT_POSTINGS = Path(__file__).resolve().parent.parent / "tests" / "data" / "postings"

# (title, noun for "Example <Noun> Co", location, salary)
SYNTHETIC = [
    ("Backend Engineer", "Widget", "Remote - USA", "$140,000 - $170,000"),
    ("Senior Data Engineer", "Harbor", "Seattle, WA", None),
    ("Machine Learning Engineer", "Lantern", "San Francisco, CA", "$180,000 - $220,000"),
    ("Frontend Engineer", "Maple", "New York, NY", None),
    ("Site Reliability Engineer", "Granite", "Austin, TX", "$150,000 - $175,000"),
    ("Platform Engineer", "Orbit", "Remote - USA", None),
    ("Data Analyst", "Meadow", "Chicago, IL", "USD45-USD55/hourly"),
    ("Security Engineer", "Beacon", "Boston, MA", None),
    ("Full Stack Developer", "Cedar", "Denver, CO", "$120,000 - $150,000"),
    ("Staff Software Engineer", "Summit", "Remote - Canada", None),
    ("Applied Scientist", "Prism", "Seattle, WA", "$190,000 - $240,000"),
    ("DevOps Engineer", "Falcon", "Atlanta, GA", None),
]

# One entry per seeded job, in order: (stage, user status). Stages are cumulative:
# discovered < enriched < scored < tailored < covered.
LAYOUT = [
    ("discovered", None), ("discovered", None),
    ("enriched", None), ("enriched", None), ("enriched", None),
    ("scored", None), ("scored", None), ("scored", None), ("scored", None),
    ("tailored", None), ("tailored", None),
    ("covered", None), ("covered", "in_progress"), ("covered", "in_progress"),
    ("covered", "submitted"), ("covered", "submitted"), ("covered", "rejected"), ("covered", "heard_back"),
]
STAGES = ("discovered", "enriched", "scored", "tailored", "covered")


def _synthetic_description(title: str, company: str, location: str) -> str:
    return (
        f"{company} is hiring a {title} ({location}).\n\n"
        "This is a fake posting seeded into preprod for testing. It is not a real job.\n\n"
        "What you'll do:\n- Build and run services our customers depend on.\n- Work with a small, friendly team.\n\n"
        "What we're looking for:\n- A few years of relevant experience.\n- Clear writing and good judgement.\n"
    )


def _base_jobs(postings_dir: Path) -> list[dict]:
    """Fixture postings first (they have real descriptions), then the synthetic ones."""
    jobs = []
    for meta_path in sorted(postings_dir.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text())
        text = meta_path.with_name(meta_path.name.removesuffix(".meta.json") + ".txt").read_text()
        jobs.append({
            "url": meta["url"], "title": meta["title"], "company": meta.get("company"), "site": meta["site"],
            "location": meta.get("location"), "salary": meta.get("salary"), "strategy": meta.get("strategy"),
            "full_description": text,
        })
    for title, noun, location, salary in SYNTHETIC:
        company = f"Example {noun} Co"
        slug = f"{noun}-{title}".lower().replace(" ", "-")
        jobs.append({
            "url": f"https://jobs.example.com/{slug}", "title": title, "company": company, "site": "example",
            "location": location, "salary": salary, "strategy": "seed",
            "full_description": _synthetic_description(title, company, location),
        })
    return jobs


def build_jobs(postings_dir: Path, now: datetime) -> list[dict]:
    """18 job rows (fixtures + synthetic), deterministic for a given `now`. Each has a `_user_status` (or None).

    Fixtures go to the later stages so the jobs with the most detail show real descriptions.
    """
    base = _base_jobs(postings_dir)
    n_fixtures = len(base) - len(SYNTHETIC)
    # Synthetic jobs fill the early slots, fixtures the last ones.
    ordered = base[n_fixtures:] + base[:n_fixtures]
    if len(ordered) != len(LAYOUT):
        raise ValueError(f"expected {len(LAYOUT) - len(SYNTHETIC)} postings in {postings_dir}, found {n_fixtures}")

    jobs = []
    for i, (job, (stage, status)) in enumerate(zip(ordered, LAYOUT, strict=True)):
        level = STAGES.index(stage)
        found = now - timedelta(days=30 - i * 1.5, hours=i)

        def at(days: float, found=found) -> str:
            return (found + timedelta(days=days)).isoformat()

        row = {
            "url": job["url"], "title": job["title"], "company": job["company"], "site": job["site"],
            "location": job["location"], "salary": job["salary"], "strategy": job["strategy"],
            "description": job["full_description"][:300], "discovered_at": at(0),
        }
        if level >= STAGES.index("enriched"):
            row.update(full_description=job["full_description"], application_url=job["url"],
                       detail_scraped_at=at(0.1))
        if level >= STAGES.index("scored"):
            score = 30 + (i * 7) % 8 * 10  # 30..100
            row.update(fit_score=score, scored_at=at(0.2),
                       score_reasoning=f"Seeded score {score}/100: a fake assessment of how well the profile fits.")
        if level >= STAGES.index("tailored"):
            row.update(tailored_at=at(0.3))  # no files exist, so the paths stay NULL
        if level >= STAGES.index("covered"):
            row.update(cover_letter_at=at(0.4))
        row["_user_status"] = status
        if status in ("submitted", "rejected", "heard_back"):
            row["_submitted_at"] = at(0.5)
        jobs.append(row)
    return jobs


def seed(conn, jobs: list[dict]) -> int:
    """Delete every job, status event and dashboard task, then insert `jobs` (the owner's) and apply their statuses."""
    owner_id = users.owner(conn).id
    for table in ("dashboard_tasks", "status_events", "jobs"):
        conn.execute(f"DELETE FROM {table}")
    for job in jobs:
        row = {k: v for k, v in job.items() if not k.startswith("_")}
        row["job_key"] = job_key(row["url"])
        row["user_id"] = owner_id
        cols = ", ".join(row)
        conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    conn.commit()
    for job in jobs:
        status = job.get("_user_status")
        if status is None:
            continue
        if status in tracking.RESPONSE_STATUSES:  # a response follows a submission
            tracking.set_status(conn, job["url"], "submitted", submitted_at=job["_submitted_at"], source="seed")
            tracking.set_status(conn, job["url"], status, source="seed")
        else:
            tracking.set_status(conn, job["url"], status, submitted_at=job.get("_submitted_at"), source="seed")
    conn.commit()
    return len(jobs)


def _preprod_db_name(url: str | None) -> str | None:
    """The database name if `url` is a postgres URL for exactly PREPROD_DB, else None."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        return None
    name = parsed.path.lstrip("/")
    return name if name == PREPROD_DB else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--postings", type=Path, default=DEFAULT_POSTINGS, help="fixture postings directory")
    parser.add_argument("--sqlite", type=Path, help="seed this SQLite file instead of STRELITZIA_DATABASE_URL")
    args = parser.parse_args(argv)

    if args.sqlite:
        target, name = str(args.sqlite), args.sqlite.name
    else:
        url = env("DATABASE_URL")
        name = _preprod_db_name(url)
        if name is None:
            # Never echo the URL: it holds the password.
            print(f"refusing to seed: STRELITZIA_DATABASE_URL (or APPLYPILOT_DATABASE_URL) must be a postgres URL for the database "
                  f"'{PREPROD_DB}' (this script deletes every job)", file=sys.stderr)
            return 2
        target = url

    jobs = build_jobs(args.postings, datetime.now(UTC))
    conn = init_db(target)
    count = seed(conn, jobs)
    print(f"seeded {count} jobs into {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
