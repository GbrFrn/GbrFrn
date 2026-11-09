#!/usr/bin/env python3
"""Measure authored code from local clones and write assets/code-stats.json.

Languages use changed lines from authored commits and external pull requests
instead of repository-wide language bytes, so the profile shows languages the
account actively worked in. Private repos need local clones, so this runs on a
workstation; tools/render_profile.py turns the JSON into the profile SVGs and
also runs in CI. No repo names are written out.

Run: GITHUB_LOCAL_REPO_ROOT=~ GITHUB_LANG_OWNERS=GbrFrn,PlanetaryStudios \
     GITHUB_LANG_EXCLUDE=<forks> python3 tools/build_language_chart.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATS_PATH = ROOT / "assets" / "code-stats.json"
LOGIN = os.environ.get("GITHUB_LOGIN", "GbrFrn")
# Owners whose repos count (comma-separated): the login plus any orgs.
OWNERS = [
    owner.strip()
    for owner in os.environ.get("GITHUB_LANG_OWNERS", LOGIN).split(",")
    if owner.strip()
]
# ponytail: commits above this many classified lines are imports or vendored
# trees (an OpenKore seed was 360k), not authored work. Real commits peak ~11k.
# Repo names to skip entirely, e.g. forks of someone else's project that
# GitHub doesn't mark as forks. Env-only so private names stay out of this file.
EXCLUDED_REPOS = {
    name.strip().casefold()
    for name in os.environ.get("GITHUB_LANG_EXCLUDE", "").split(",")
    if name.strip()
}
MAX_COMMIT_LINES = int(os.environ.get("GITHUB_LANG_MAX_COMMIT_LINES", "20000"))
LOCAL_REPO_ROOT = Path(os.environ.get("GITHUB_LOCAL_REPO_ROOT", ROOT.parent))
SINCE = os.environ.get("GITHUB_LANG_SINCE", "")



def _git_identity() -> str:
    result = subprocess.run(
        ["git", "config", "--global", "user.email"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


# Default: the login plus the local git email, so commits signed with a real
# name instead of the login still count without the email living in this file.
AUTHOR_PATTERNS = [
    pattern.strip().casefold()
    for pattern in os.environ.get(
        "GITHUB_LANG_AUTHORS",
        f"{LOGIN},{_git_identity()}",
    ).split(",")
    if pattern.strip()
]

LANGUAGE_BY_EXTENSION = {
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".scss": "CSS",
    ".sass": "CSS",
    ".less": "CSS",
    ".html": "HTML",
    ".htm": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".pl": "Perl",
    ".pm": "Perl",
    ".t": "Perl",
    ".php": "PHP",
    ".py": "Python",
    ".pyw": "Python",
    ".r": "R",
    ".rs": "Rust",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    ".sql": "SQL",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".s": "Assembly",
    ".asm": "Assembly",
}

LANGUAGE_BY_FILENAME = {
    "bashrc": "Shell",
    "zshrc": "Shell",
}

EXCLUDED_EXTENSIONS = {
    ".csv",
    ".gif",
    ".ico",
    ".ipynb",
    ".jpeg",
    ".jpg",
    ".json",
    ".lock",
    ".log",
    ".map",
    ".md",
    ".mdx",
    ".pdf",
    ".png",
    ".rst",
    ".svg",
    ".toml",
    ".tsv",
    ".txt",
    ".webp",
    ".xml",
    ".yaml",
    ".yml",
}

EXCLUDED_FILENAMES = {
    "cargo.lock",
    "composer.lock",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "yarn.lock",
}

EXCLUDED_PATH_PARTS = {
    ".git",
    ".github",
    ".next",
    ".nuxt",
    "assets",
    "build",
    "coverage",
    "dist",
    "docs",
    "node_modules",
    "out",
    "public",
    "target",
    "vendor",
    "vendors",
}

SCAN_PRUNE_DIRS = {
    ".cache",
    ".claude",
    ".config",
    ".local",
    ".npm",
    ".steam",
    "Imagens",
    "Downloads",
    "Games",
    "M\u00fasicas",
    "V\u00eddeos",
    "node_modules",
}


def gh(args: list[str]) -> object:
    result = subprocess.run(
        ["gh", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def gh_text(args: list[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def git_text(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return result.stdout


def changed_lines(file_info: dict[str, object]) -> int:
    additions = int(file_info.get("additions") or 0)
    deletions = int(file_info.get("deletions") or 0)
    return additions + deletions


def classify(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    parts = {part.lower() for part in normalized.split("/")[:-1]}
    if parts & EXCLUDED_PATH_PARTS:
        return None

    basename = normalized.rsplit("/", 1)[-1]
    lower_name = basename.lower()
    if lower_name in EXCLUDED_FILENAMES:
        return None
    if lower_name.endswith(".min.js") or lower_name.endswith(".min.css"):
        return None

    if lower_name in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[lower_name]

    suffixes = [suffix.lower() for suffix in Path(lower_name).suffixes]
    if suffixes:
        extension = suffixes[-1]
        if extension in EXCLUDED_EXTENSIONS:
            return None
        return LANGUAGE_BY_EXTENSION.get(extension)

    return None


def add_file(counter: Counter[str], file_info: dict[str, object]) -> None:
    path = str(file_info.get("filename") or file_info.get("path") or "")
    language = classify(path)
    if not language:
        return

    lines = changed_lines(file_info)
    if lines > 0:
        counter[language] += lines


def repos_owned_by_login() -> list[dict[str, object]]:
    repos: list[dict[str, object]] = []
    for owner in OWNERS:
        listed = gh(
            [
                "repo",
                "list",
                owner,
                "--limit",
                "200",
                "--json",
                "nameWithOwner,isFork,isArchived",
            ]
        )
        if isinstance(listed, list):
            repos.extend(listed)

    return [
        repo
        for repo in repos
        if not repo.get("isFork")
        and not repo.get("isArchived")
        and str(repo["nameWithOwner"]).rsplit("/", 1)[-1].casefold() not in EXCLUDED_REPOS
    ]


def repo_key_from_remote(url: str) -> str | None:
    match = re.search(
        r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$",
        url.strip(),
        re.IGNORECASE,
    )
    if not match:
        return None
    owner, repo = match.groups()
    return f"{owner}/{repo}"


def local_git_repos(scan_root: Path) -> list[Path]:
    repos: list[Path] = []
    scan_root = scan_root.resolve()
    max_depth = 4

    for current, dirs, _ in os.walk(scan_root):
        current_path = Path(current)
        depth = len(current_path.relative_to(scan_root).parts)
        dirs[:] = [
            name
            for name in dirs
            if name not in SCAN_PRUNE_DIRS and not name.startswith(".cache")
        ]

        if ".git" in dirs:
            repos.append(current_path)
            dirs.remove(".git")

        if depth >= max_depth:
            dirs[:] = []

    return sorted(repos)


def local_owned_repo_paths(owned_repos: list[dict[str, object]]) -> dict[str, Path]:
    owned_keys = {str(repo["nameWithOwner"]) for repo in owned_repos}
    selected: dict[str, Path] = {}

    for repo_path in local_git_repos(LOCAL_REPO_ROOT):
        try:
            remotes = git_text(repo_path, ["remote", "-v"])
        except subprocess.CalledProcessError:
            continue

        keys = {
            key
            for line in remotes.splitlines()
            if (key := repo_key_from_remote(line.split()[1] if len(line.split()) > 1 else ""))
        }
        matching = sorted(keys & owned_keys)
        if not matching:
            continue

        for key in matching:
            current = selected.get(key)
            repo_name = key.rsplit("/", 1)[-1].casefold()
            exact_name = repo_path.name.casefold() == repo_name
            current_exact = bool(current and current.name.casefold() == repo_name)
            if current is None or (exact_name and not current_exact):
                selected[key] = repo_path

    return selected


def author_matches(name: str, email: str) -> bool:
    identity = f"{name} {email}".casefold()
    return any(pattern in identity for pattern in AUTHOR_PATTERNS)


# A repo that absorbed another's history (or a mirror) shares commit SHAs;
# count each commit once across every clone.
SEEN_COMMITS: set[str] = set()
AUTHORED_COMMITS = 0


def authored_local_changes(repo: Path) -> list[dict[str, object]]:
    args = [
        "log",
        "--all",
        "--no-merges",
        "--numstat",
        "--format=@@commit\t%H\t%an\t%ae\t%aI",
    ]
    if SINCE:
        args.insert(1, f"--since={SINCE}")

    try:
        output = git_text(repo, args)
    except subprocess.CalledProcessError:
        return []

    include_commit = False
    files: list[dict[str, object]] = []
    commit: list[dict[str, object]] = []

    def flush() -> None:
        counted = sum(changed_lines(f) for f in commit if classify(str(f["path"])))
        if counted <= MAX_COMMIT_LINES:
            files.extend(commit)
        commit.clear()

    for line in output.splitlines():
        if line.startswith("@@commit\t"):
            flush()
            parts = line.split("\t")
            include_commit = (
                len(parts) >= 4
                and parts[1] not in SEEN_COMMITS
                and author_matches(parts[2], parts[3])
            )
            if len(parts) >= 2:
                SEEN_COMMITS.add(parts[1])
            if include_commit:
                global AUTHORED_COMMITS
                AUTHORED_COMMITS += 1
            continue

        if not include_commit or not line.strip():
            continue

        parts = line.split("\t", 2)
        if len(parts) != 3 or "-" in parts[:2]:
            continue

        additions, deletions, path = parts
        if additions.isdigit() and deletions.isdigit():
            commit.append(
                {
                    "additions": int(additions),
                    "deletions": int(deletions),
                    "path": path,
                }
            )

    flush()
    return files


MERGED_PR_REPOS: Counter[str] = Counter()


def external_pull_request_files() -> list[dict[str, object]]:
    prs = gh(
        [
            "search",
            "prs",
            "--author",
            LOGIN,
            "--limit",
            "1000",
            "--json",
            "repository,number,state",
        ]
    )
    if not isinstance(prs, list):
        return []

    files: list[dict[str, object]] = []
    for pr in prs:
        state = str(pr.get("state") or "").lower()
        if state not in {"open", "merged"}:
            continue

        repo = pr.get("repository") or {}
        repo_name = str(repo.get("nameWithOwner") or "")
        if repo_name.split("/", 1)[0] in OWNERS:
            continue

        number = pr.get("number")
        if not repo_name or not number:
            continue
        if state == "merged":
            MERGED_PR_REPOS[repo_name] += 1

        details = gh(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repo_name,
                "--json",
                "files",
            ]
        )
        if isinstance(details, dict):
            files.extend(details.get("files") or [])

    return files


def rounded_rows(counter: Counter[str]) -> list[tuple[str, int, float]]:
    total = sum(counter.values())
    if total <= 0:
        return []

    rows = [
        (language, lines, (lines / total) * 100)
        for language, lines in counter.most_common()
    ]
    top = rows[:8]
    rest = rows[8:]
    if rest:
        other_lines = sum(lines for _, lines, _ in rest)
        top.append(("Other", other_lines, (other_lines / total) * 100))
    return top


def main() -> int:
    counter: Counter[str] = Counter()
    owned_repos = repos_owned_by_login()
    local_repos = local_owned_repo_paths(owned_repos)

    # Two remotes (a rename) can point at one clone; count each clone once.
    for repo_path in sorted(set(local_repos.values())):
        for file_info in authored_local_changes(repo_path):
            add_file(counter, file_info)

    for file_info in external_pull_request_files():
        add_file(counter, file_info)

    if not counter:
        print("No language data found.", file=sys.stderr)
        return 1

    stats = {
        "generated": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "authored_commits": AUTHORED_COMMITS,
        "merged_prs": sum(MERGED_PR_REPOS.values()),
        "merged_pr_repos": len(MERGED_PR_REPOS),
        "languages": [
            {"name": name, "lines": lines, "percent": round(percent, 1)}
            for name, lines, percent in rounded_rows(counter)
        ],
    }
    STATS_PATH.parent.mkdir(exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"local repos: {len(set(local_repos.values()))}")
    print(json.dumps(stats, indent=2))

    import render_profile  # same folder; redraw the SVGs with the new numbers

    return render_profile.main()


if __name__ == "__main__":
    raise SystemExit(main())
