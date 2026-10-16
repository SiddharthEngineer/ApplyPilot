"""scripts/eval_fit.py: sampling jobs to label and comparing model/effort combinations (synthetic jobs, fake replies)."""

import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

from strelitzia import database
from strelitzia.database import close_connection, init_db, job_key
from strelitzia.llm import record_usage
from fit_helpers import POSTING, add_extracted_job, reply

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_fit.py"
CATEGORIES = ("data_engineering", "software_engineering", "data_science")


@pytest.fixture(scope="module")
def eval_fit():
    spec = importlib.util.spec_from_file_location("eval_fit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "eval.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    c = init_db()
    for cat in CATEGORIES:
        for i in range(6):
            add_extracted_job(c, f"https://jobs.example.com/{cat}/{i}", title=f"{cat} {i}",
                              posting={**POSTING, "role_category": cat}, fit_score=i * 15)
    add_extracted_job(c, "https://jobs.example.com/unextracted", details_json=None)
    yield c
    close_connection(str(db))


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_sample_writes_header_and_rows_spread_across_categories(eval_fit, conn, capsys):
    assert eval_fit.main(["sample", "--n", "9"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "job_key,title,company,label"
    rows = _rows(out)
    assert len(rows) == 9
    assert all(r["label"] == "" and r["company"] == "Acme Robotics" for r in rows)
    assert len({r["job_key"] for r in rows}) == 9
    by_cat = {cat: [r for r in rows if r["title"].startswith(cat)] for cat in CATEGORIES}
    assert all(len(v) == 3 for v in by_cat.values())
    # Within a category, the picks span the score range: the lowest and highest scored job come first.
    assert {r["title"] for r in by_cat["data_science"]} >= {"data_science 0", "data_science 5"}
    assert "unextracted" not in out


def test_sample_caps_at_the_extracted_jobs(eval_fit, conn, capsys):
    assert eval_fit.main(["sample", "--n", "100"]) == 0
    assert len(_rows(capsys.readouterr().out)) == 18


def test_spearman_and_top_n(eval_fit):
    assert eval_fit.spearman([1, 2, 3, 4], [0, 1, 1, 2]) == pytest.approx(0.9486833)
    assert eval_fit.spearman([4, 3, 2, 1], [0, 1, 2, 2]) < 0
    assert eval_fit.spearman([5, 5, 5], [0, 1, 2]) is None
    assert eval_fit.good_in_top([90, 10, 80, 70], [2, 2, 0, 2], n=2) == 1


def _write_labels(path: Path, conn) -> dict[str, str]:
    """Label each job by its index: 0-1 bad, 2-3 ok, 4-5 good."""
    labels = {}
    for row in conn.execute("SELECT url, title FROM jobs WHERE details_json IS NOT NULL"):
        i = int(row["url"].rsplit("/", 1)[1])
        labels[job_key(row["url"])] = ("bad", "bad", "ok", "ok", "good", "good")[i]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("job_key", "title", "company", "label"))
        for key, label in labels.items():
            w.writerow((key, "t", "c", label))
        w.writerow(("unlabelled", "t", "c", ""))
    return labels


def test_run_prints_one_row_per_combination(eval_fit, conn, tmp_path):
    labels_path = tmp_path / "fit_labels.csv"
    _write_labels(labels_path, conn)
    calls = []

    def fake_assess(client, prefs, resume_text, job):
        calls.append((client.model, client.effort))
        i = int(job["url"].rsplit("/", 1)[1])
        # Haiku/low ranks the jobs in the labels' order; the others rank them backwards.
        rating = 1 + i * 4 // 5 if (client.model, client.effort) == ("claude-haiku-5-5", "low") else 5 - i * 4 // 5
        tokens = 2000 if client.model == "claude-sonnet-5-5" else 1000
        record_usage("scoring", client.model, {"input_tokens": tokens, "output_tokens": 100}, conn=conn,
                     job_url=job["url"])
        return reply(skills=rating, experience=rating, domain=rating)

    out = io.StringIO()
    before = conn.execute("SELECT fit_assessment, fit_score FROM jobs ORDER BY url").fetchall()
    assert eval_fit.cmd_run(conn, labels_path, ["claude-haiku-5-5", "claude-sonnet-5-5"], ["low", "medium"], out,
                            assess=fake_assess) == 0
    assert len(calls) == 4 * 18
    lines = out.getvalue().splitlines()
    assert lines[0].split() == ["model", "effort", "jobs", "errors", "spearman", "good@10", "$/1k", "jobs"]
    rows = {tuple(line.split()[:2]): line.split()[2:] for line in lines[1:5]}
    assert set(rows) == {(m, e) for m in ("claude-haiku-5-5", "claude-sonnet-5-5") for e in ("low", "medium")}
    jobs, errors, rho, good, usd = rows[("claude-haiku-5-5", "low")]
    assert (jobs, errors) == ("18", "0") and float(rho) > 0.9 and good == "6"
    assert float(rows[("claude-sonnet-5-5", "low")][2]) < 0 and rows[("claude-sonnet-5-5", "low")][3] == "0"
    # $/1k jobs comes from the llm_usage rows each combination wrote.
    assert float(usd) == pytest.approx((1000 * 0.10 + 100 * 0.50) / 1e6 * 1000, abs=0.005)
    assert float(rows[("claude-sonnet-5-5", "medium")][4]) == pytest.approx((2000 * 2.00 + 100 * 10.00) / 1e6 * 1000,
                                                                            abs=0.005)
    assert "Pick: claude-haiku-5-5, effort low" in out.getvalue()
    # The eval never touches the jobs table.
    assert conn.execute("SELECT fit_assessment, fit_score FROM jobs ORDER BY url").fetchall() == before


def test_run_counts_unparseable_replies_as_errors(eval_fit, conn, tmp_path):
    labels_path = tmp_path / "fit_labels.csv"
    _write_labels(labels_path, conn)
    out = io.StringIO()
    assert eval_fit.cmd_run(conn, labels_path, ["claude-haiku-5-5"], ["low"], out,
                            assess=lambda *a: "not json") == 0
    assert out.getvalue().splitlines()[1].split()[2:5] == ["0", "18", "n/a"]
    assert "Pick:" not in out.getvalue()


def test_run_needs_labels(eval_fit, conn, tmp_path, capsys):
    labels_path = tmp_path / "fit_labels.csv"
    labels_path.write_text("job_key,title,company,label\n" + json.dumps("x") + ",t,c,\n")
    assert eval_fit.cmd_run(conn, labels_path, ["claude-haiku-5-5"], ["low"], io.StringIO(),
                            assess=lambda *a: pytest.fail("no call expected")) == 1
    assert "fill in `label`" in capsys.readouterr().err
