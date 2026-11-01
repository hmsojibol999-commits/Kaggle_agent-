"""The `state` branch: used/bad word lists, sessions, uploads, audio cache, reports.

Plain git against any remote (the Actions token URL in CI, a local bare repo in
tests). The branch is created automatically if it does not exist.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import errors as E

BRANCH = "state"
KEEP_SESSIONS = 10


def _git(args: list, cwd: str | Path, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise E.WCError(E.STATE_FAILED, f"git {' '.join(args[:2])}: {p.stderr.strip()[:300]}", "state")
    return p


def default_remote() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    tok = os.environ.get("GITHUB_TOKEN", "")
    if repo and tok:
        return f"https://x-access-token:{tok}@github.com/{repo}.git"
    raise E.WCError(E.STATE_FAILED, "no GITHUB_REPOSITORY/GITHUB_TOKEN and no remote given", "state")


class State:
    def __init__(self, path: str | Path = "state", remote: str | None = None,
                 branch: str = BRANCH):
        self.path = Path(path)
        self.branch = branch
        self.remote = remote
        self.created = False

    # -- lifecycle
    def init(self) -> "State":
        remote = self.remote or default_remote()
        if (self.path / ".git").exists():
            _git(["fetch", "origin", self.branch], self.path, check=False)
            _git(["reset", "--hard", f"origin/{self.branch}"], self.path, check=False)
            return self
        self.path.mkdir(parents=True, exist_ok=True)
        probe = subprocess.run(["git", "ls-remote", "--heads", remote, self.branch],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            raise E.WCError(E.STATE_FAILED, f"cannot reach remote: {probe.stderr.strip()[:200]}", "state")
        if self.branch in probe.stdout:
            subprocess.run(["git", "clone", "--branch", self.branch, "--single-branch",
                            "--depth", "1", remote, str(self.path)], check=True,
                           capture_output=True)
        else:
            _git(["init", "-q"], self.path)
            _git(["remote", "add", "origin", remote], self.path)
            _git(["checkout", "-q", "--orphan", self.branch], self.path)
            (self.path / "used_words.txt").write_text("", encoding="utf-8")
            (self.path / "bad_words.txt").write_text("", encoding="utf-8")
            (self.path / "README.md").write_text(
                "WordClip Studio state branch. Written by workflows; do not edit by hand.\n",
                encoding="utf-8")
            self.created = True
        _git(["config", "user.name", "wordclip-bot"], self.path)
        _git(["config", "user.email", "wordclip-bot@users.noreply.github.com"], self.path)
        if self.created:
            self.commit_push("Create state branch")
        return self

    def commit_push(self, message: str, attempts: int = 5) -> bool:
        _git(["add", "-A"], self.path)
        if _git(["status", "--porcelain"], self.path).stdout.strip() == "":
            return False
        _git(["commit", "-q", "-m", message], self.path)
        last = ""
        for i in range(attempts):
            p = _git(["push", "origin", f"HEAD:{self.branch}"], self.path, check=False)
            if p.returncode == 0:
                return True
            last = p.stderr.strip()
            _git(["pull", "--rebase", "origin", self.branch], self.path, check=False)
            time.sleep(1 + i)
        raise E.WCError(E.STATE_FAILED, f"push failed: {last[:300]}", "state")

    # -- files
    def p(self, rel: str) -> Path:
        return self.path / rel

    def read_lines(self, rel: str) -> list:
        f = self.p(rel)
        if not f.exists():
            return []
        return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def append_lines(self, rel: str, lines: list) -> None:
        seen = set(self.read_lines(rel))
        new = []
        for ln in lines:
            if ln and ln not in seen:
                seen.add(ln)
                new.append(ln)
        if new:
            f = self.p(rel)
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a", encoding="utf-8") as fh:
                fh.write("\n".join(new) + "\n")

    def write_json(self, rel: str, data) -> None:
        f = self.p(rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def read_json(self, rel: str, default=None):
        f = self.p(rel)
        if not f.exists():
            return default
        return json.loads(f.read_text(encoding="utf-8"))

    # -- sessions
    def prune_sessions(self, keep: int = KEEP_SESSIONS) -> list:
        root = self.p("sessions")
        if not root.exists():
            return []
        dirs = sorted([d for d in root.iterdir() if d.is_dir()], key=lambda d: d.name)
        removed = []
        for d in dirs[:-keep] if len(dirs) > keep else []:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
        return removed


def cli() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cmd == "init":
        st = State(os.environ.get("STATE_DIR", "state")).init()
        print(f"state ready at {st.path} (created={st.created})")
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    cli()
