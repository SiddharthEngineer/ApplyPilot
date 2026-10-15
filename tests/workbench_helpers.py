"""Synthetic inputs for the Claude workbench tests (no personal data)."""

import json

from strelitzia.scoring.content_library import ContentLibrary, Project, RoleSection

PROFILE = {
    "personal": {"full_name": "Alex Tester", "email": "alex@example.com", "city": "Springfield",
                 "province_state": "IL"},
    # The skills boundary lists Kubernetes, but nothing in the library or base resume backs it.
    "skills_boundary": {"devops": ["Docker", "Kubernetes"]},
}

FIXED = {
    "education": [{"heading": "State University, B.S. Statistics", "dates": "May 2022",
                   "bullets": ["Coursework: databases, statistics"]}],
    "roles": {},
    "header": None,
}

BASE_RESUME = """Alex Tester
Springfield, IL ▫ alex@example.com

WORK EXPERIENCE

Data Engineer at Acme Corp Jan 2024 – Present
• Built nightly pipelines in Airflow and Spark for the finance warehouse
• Ran data-quality checks for analysts

Data Analyst Intern at Globex Jun 2022 – Aug 2022
• Built a churn model in Python

EDUCATION

State University, B.S. Statistics May 2022

SKILLS

Languages: Python, SQL
Tools: Airflow, Spark, dbt, Superset
"""


def library() -> ContentLibrary:
    return ContentLibrary(roles=[
        RoleSection(title="Data Engineer, Acme", dates="Jan 2024–Present", projects=[
            Project(name="Orders Pipeline", role_header="## CURRENT ROLE", dates="Jan 2024–present",
                    context="Nightly orders pipeline feeding the finance warehouse.",
                    scope_scale="40 million rows per day across 12 source systems.",
                    tools_actions="Airflow, Spark, dbt; migrated jobs from cron to Airflow.",
                    outcome_metrics="Cut runtime from 6 hours to 45 minutes; failures dropped 80%.",
                    angles=["PIPELINE"]),
            Project(name="Quality Dashboard", role_header="## CURRENT ROLE", dates="Mar 2024–present",
                    context="Data-quality dashboard on the customer data platform.",
                    scope_scale="3 teams, 25 users.", tools_actions="Superset, SQL checks.",
                    outcome_metrics="Caught 2 schema regressions before release.", angles=["DATA-QUALITY"]),
        ]),
        RoleSection(title="Data Analyst Intern, Globex", dates="Jun 2022–Aug 2022", projects=[
            Project(name="Churn Model", role_header="## PRIOR ROLE", dates="Jun 2022–Aug 2022",
                    context="Customer churn prediction with ML.", scope_scale="1.2M customer records.",
                    tools_actions="Python, scikit-learn.", outcome_metrics="AUC 0.83 on holdout.", angles=["ML"]),
        ]),
    ]).ensure_keys()


POSTING = {
    "skills_required": ["Airflow", "Kubernetes", "Machine Learning", "CDP"],
    "required_qualifications": ["3+ years building data pipelines with Spark"],
    "skills_preferred": ["dbt", "Terraform"],
    "preferred_qualifications": [],
}

DESCRIPTION = ("We need a data engineer to own Airflow pipelines on our Customer Data Platform (CDP). "
               "Kubernetes and Machine Learning are required. dbt and Terraform are nice to have.")


def job(**overrides) -> dict:
    out = {
        "url": "https://example.com/jobs/42", "job_key": "abc123def456", "title": "Senior Data Engineer",
        "company": "Initech", "site": "initech", "location": "Remote",
        "full_description": DESCRIPTION, "details_json": json.dumps(POSTING),
    }
    out.update(overrides)
    return out


def good_result(**resume_overrides) -> dict:
    resume = {
        "headline": "Data engineer building Airflow and Spark pipelines",
        "roles": [
            {"role_key": "data-engineer-acme", "bullets": [
                {"text": "Migrated the nightly orders pipeline from cron to Airflow and Spark, cutting runtime "
                         "from 6 hours to 45 minutes", "project_ids": ["orders-pipeline"]},
                {"text": "Built a Superset data-quality dashboard on the customer data platform (CDP) used by "
                         "3 teams", "project_ids": ["quality-dashboard"]},
            ]},
            {"role_key": "data-analyst-intern-globex", "bullets": [
                {"text": "Trained a machine learning (ML) churn model in Python with scikit-learn, reaching "
                         "AUC 0.83 on holdout", "project_ids": ["churn-model"]},
            ]},
        ],
        "skills": [{"category": "Languages", "items": "Python, SQL"},
                   {"category": "Tools", "items": "Airflow, Spark, dbt, Superset"}],
        "dropped_roles": [],
    }
    resume.update(resume_overrides)
    return {
        "strelitzia": 1,
        "resume": resume,
        "cover_letter": {
            "paragraphs": [
                "I moved a nightly orders pipeline from cron to Airflow and cut its runtime from 6 hours to "
                "45 minutes, the kind of work your data team describes.",
                "At Acme the pipeline handles 40 million rows per day across 12 source systems, and failures "
                "dropped 80% after the move. I also built a Superset dashboard that caught 2 schema regressions "
                "before release.",
                "Your Customer Data Platform is the center of the role, and I have built on one before. Happy to "
                "walk through any of this in more detail.",
            ],
            "evidence_slugs": ["orders-pipeline", "quality-dashboard"],
        },
        "notes_for_user": "Kubernetes and Terraform are not in your library, so I left them out.",
    }


def inputs():
    from strelitzia.scoring.workbench import Inputs

    return Inputs(library=library(), base_resume_text=BASE_RESUME, profile=PROFILE, fixed=FIXED)


def add_job_row(conn, **overrides) -> dict:
    """Insert job() into the jobs table and return the row as a dict."""
    row = {k: v for k, v in job(**overrides).items()}
    names = ", ".join(row)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (row["url"],)).fetchone())


def fake_render_pdf(html, output_path):
    """Stand-in for pdf.render_pdf: writes the HTML as the 'PDF' and reports one page."""
    from pathlib import Path

    Path(output_path).write_text(html, encoding="utf-8")
    return {"overflow": False, "content_height_pt": 500.0, "usable_height_pt": 741.6, "pages": 1}


def reply(data: dict, prose: str = "Here is the tailored resume and letter.") -> str:
    """A chat reply as Claude writes it: prose, then the fenced JSON block."""
    return f"{prose}\n\n```json\n{json.dumps(data, indent=2)}\n```\n"
