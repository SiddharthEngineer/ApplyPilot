"""Helpers shared by the dashboard tests."""


def add_job(conn, url="https://example.com/jobs/1", **cols):
    """Insert a job row with sensible defaults (title, company, discovered_at, a description)."""
    row = {"url": url, "title": "Data Scientist", "company": "Acme", "site": "indeed",
           "discovered_at": "2026-10-01T12:00:00+00:00", "full_description": "Build models.", **cols}
    names = ", ".join(row)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    return url
