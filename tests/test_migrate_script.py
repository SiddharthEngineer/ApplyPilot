"""scripts/migrate_to_strelitzia.sh (the post-merge VPS migration, M15) against a fake HOME, /srv and crontab."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_to_strelitzia.sh"
OLD_CRON = "*/5 * * * * {home}/src/ApplyPilot/scripts/agent/run.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def vps(tmp_path):
    home, srv, varlog = tmp_path / "home", tmp_path / "srv", tmp_path / "varlog"
    for d in (".applypilot/tailored_resumes", ".applypilot-preprod", ".config/applypilot-agent",
              ".local/state/applypilot-agent", ".ssh", ".claude/projects", "src"):
        (home / d).mkdir(parents=True)
    (home / ".applypilot/applypilot.db").write_text("db")
    (home / ".applypilot/tailored_resumes/a.pdf").write_text("pdf")
    (home / ".config/applypilot-agent/env").write_text("NTFY_TOPIC=x\n")
    (home / "applypilot-dashboard-credentials.txt").write_text("secret")
    (home / ".ssh/config").write_text(
        "Host github-applypilot\n  HostName github.com\n  IdentityFile ~/.ssh/key\nHost other\n  HostName example.com")
    memory = home / ".claude/projects" / (str(home).replace("/", "-") + "-src-ApplyPilot")
    memory.mkdir()
    (memory / "MEMORY.md").write_text("- note\n")
    repo = home / "src/ApplyPilot"
    git("init", "-q", str(repo))
    git("-C", str(repo), "remote", "add", "origin", "https://github.com/SiddharthEngineer/ApplyPilot.git")
    for d in ("ApplyPilot-prod", "ApplyPilot-preprod"):
        (srv / d).mkdir(parents=True)
    (varlog / "applypilot-agent").mkdir(parents=True)
    crontab_file = tmp_path / "crontab.txt"
    crontab_file.write_text(OLD_CRON.format(home=home) + "\n0 3 * * * /usr/bin/other\n")
    fake_crontab = tmp_path / "fake-crontab"
    fake_crontab.write_text(f'#!/bin/sh\nif [ "$1" = -l ]; then cat "{crontab_file}"; else cat > "{crontab_file}"; fi\n')
    fake_crontab.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.endswith("_AGENT_STATE")}
    env.update(HOME=str(home), MIGRATE_SRV=str(srv), MIGRATE_VARLOG=str(varlog), CRONTAB=str(fake_crontab),
               GIT_CONFIG_GLOBAL=os.devnull, PATH=f"{Path(sys.executable).parent}:{env['PATH']}")
    base_env = env

    def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        # A caller's env only overrides GIT_CONFIG_GLOBAL; everything else stays the fake VPS.
        run_env = {**base_env, **({"GIT_CONFIG_GLOBAL": env["GIT_CONFIG_GLOBAL"]} if env else {})}
        return subprocess.run(["bash", str(SCRIPT), *args], env=run_env, capture_output=True, text=True)

    run.home, run.srv, run.crontab, run.memory = home, srv, crontab_file, memory
    return run


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        rel = str(p.relative_to(root))
        out[rel] = f"link:{os.readlink(p)}" if p.is_symlink() else ("dir" if p.is_dir() else p.read_text())
    return out


def test_dry_run_is_the_default_and_changes_nothing(vps):
    before = snapshot(vps.home.parent)
    out = vps()
    assert out.returncode == 0, out.stderr
    assert "would: mv" in out.stdout and "Dry run" in out.stdout
    assert snapshot(vps.home.parent) == before
    assert git("-C", str(vps.home / "src/ApplyPilot"), "remote", "get-url", "origin").endswith("/ApplyPilot.git")


def test_apply_moves_everything_and_leaves_symlinks(vps):
    out = vps("--apply")
    assert out.returncode == 0, out.stdout + out.stderr
    home = vps.home
    for old, new in [(".applypilot", ".strelitzia"), (".applypilot-preprod", ".strelitzia-preprod"),
                     (".config/applypilot-agent", ".config/strelitzia-agent"),
                     (".local/state/applypilot-agent", ".local/state/strelitzia-agent"),
                     ("src/ApplyPilot", "src/Strelitzia")]:
        assert (home / old).is_symlink(), old
        assert (home / new).is_dir() and not (home / new).is_symlink(), new
        assert (home / old).resolve() == (home / new).resolve()
    # Absolute paths stored in DB rows still resolve through the symlink.
    assert (home / ".applypilot/tailored_resumes/a.pdf").read_text() == "pdf"

    repo = str(home / "src/Strelitzia")
    assert git("-C", repo, "remote", "get-url", "origin") == "https://github.com/SiddharthEngineer/Strelitzia.git"
    assert git("-C", repo, "remote", "get-url", "--push", "origin") == "git@github-strelitzia:SiddharthEngineer/Strelitzia.git"
    ssh = (home / ".ssh/config").read_text()
    assert "Host github-strelitzia\n  HostName github.com\n  IdentityFile ~/.ssh/key\n" in ssh
    assert ssh.count("Host other") == 1
    assert "  HostName example.com\nHost github-strelitzia\n" in ssh  # no trailing newline before: one added
    new_memory = vps.memory.parent / vps.memory.name.replace("-src-ApplyPilot", "-src-Strelitzia")
    assert (new_memory / "MEMORY.md").read_text() == "- note\n" and vps.memory.is_dir()
    cron = vps.crontab.read_text()
    assert f"{home}/src/Strelitzia/scripts/agent/run.sh" in cron and "src/ApplyPilot" not in cron
    assert "0 3 * * * /usr/bin/other" in cron
    assert (home / "strelitzia-dashboard-credentials.txt").read_text() == "secret"
    assert not (home / "applypilot-dashboard-credentials.txt").exists()
    # Root-owned paths are printed, never touched.
    assert f"sudo mv {vps.srv}/ApplyPilot-prod {vps.srv}/Strelitzia-prod" in out.stdout
    assert "applypilot-agent" in out.stdout.split("7. Root-owned")[1]
    assert (vps.srv / "ApplyPilot-prod").is_dir() and not (vps.srv / "Strelitzia-prod").exists()


def test_apply_twice_gives_the_same_result(vps):
    assert vps("--apply").returncode == 0
    first = snapshot(vps.home.parent)
    again = vps("--apply")
    assert again.returncode == 0, again.stdout + again.stderr
    assert snapshot(vps.home.parent) == first
    assert "Done: 0 change(s)" in again.stdout
    assert vps().returncode == 0 and "Dry run: 0 change(s)" in vps().stdout


def test_refuses_while_preprod_is_locked(vps):
    lock = vps.home / ".local/state/applypilot-agent/preprod.lock"
    lock.write_text("42\tslug\t2026-10-12T00:00:00Z\n")
    before = snapshot(vps.home.parent)
    out = vps("--apply")
    assert out.returncode == 1 and "PR #42" in out.stderr
    assert snapshot(vps.home.parent) == before
    dry = vps()  # a dry run still prints the plan, with a warning
    assert dry.returncode == 0 and "WARN: --apply would refuse now" in dry.stdout and "would: mv" in dry.stdout


def test_refuses_when_the_queue_cannot_be_read(vps):
    (vps.home / ".local/state/applypilot-agent/queue.json").write_text("{not json")
    out = vps("--apply")
    assert out.returncode == 1 and "can't read the build queue" in out.stderr
    assert not (vps.home / ".strelitzia").exists()


def test_refuses_while_a_build_is_running(vps):
    state = vps.home / ".local/state/applypilot-agent"
    agent = SCRIPT.parent / "agent" / "buildqueue.py"
    env = {k: v for k, v in os.environ.items() if not k.endswith("_AGENT_STATE")}
    env.update(HOME=str(vps.home), STRELITZIA_AGENT_STATE=str(state))
    subprocess.run([sys.executable, str(agent), "add", "busy"], env=env, check=True, capture_output=True)
    subprocess.run([sys.executable, str(agent), "start", "busy"], env=env, check=True, capture_output=True)
    out = vps("--apply")
    assert out.returncode == 1 and "a build is running" in out.stderr
    assert not (vps.home / ".strelitzia").exists()


def test_apply_adds_safe_directory_for_the_renamed_srv_checkouts(vps, tmp_path):
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[safe]\n\tdirectory = {vps.srv}/ApplyPilot-prod\n\tdirectory = {vps.srv}/ApplyPilot-preprod\n")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(gitconfig)}
    out = vps("--apply", env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    safe = git("config", "--file", str(gitconfig), "--get-all", "safe.directory").splitlines()
    assert f"{vps.srv}/Strelitzia-prod" in safe and f"{vps.srv}/Strelitzia-preprod" in safe
    assert f"{vps.srv}/ApplyPilot-prod" in safe  # old entries stay while the symlinks exist
    assert "Done: 0 change(s)" in vps("--apply", env=env).stdout
