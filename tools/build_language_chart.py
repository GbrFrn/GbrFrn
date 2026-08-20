#!/usr/bin/env python3
"""Build the profile language chart from authored GitHub changes.

The chart intentionally uses changed lines from authored commits and external
pull requests instead of repository-wide language bytes. That makes the profile
show languages the account actively worked in, while avoiding private repo names
in the public README.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_PATH = ROOT / "assets" / "languages.svg"
LOGIN = os.environ.get("GITHUB_LOGIN", "GbrFrn")
LOCAL_REPO_ROOT = Path(os.environ.get("GITHUB_LOCAL_REPO_ROOT", ROOT.parent))
SINCE = os.environ.get("GITHUB_LANG_SINCE", "")
AUTHOR_PATTERNS = [
    pattern.casefold()
    for pattern in os.environ.get(
        "GITHUB_LANG_AUTHORS",
        "GbrFrn",
    ).split(",")
    if pattern.strip()
]

LANGUAGE_COLORS = {
    "Assembly": "#6E4C13",
    "C": "#555555",
    "C#": "#178600",
    "C++": "#f34b7d",
    "CSS": "#663399",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Perl": "#0298c3",
    "PHP": "#4F5D95",
    "Python": "#3572A5",
    "R": "#198CE7",
    "Rust": "#dea584",
    "Shell": "#89e051",
    "SQL": "#336790",
    "TypeScript": "#3178c6",
    "Vue": "#41b883",
}

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
    repos = gh(
        [
            "repo",
            "list",
            LOGIN,
            "--limit",
            "200",
            "--json",
            "nameWithOwner,isFork,isArchived",
        ]
    )
    if not isinstance(repos, list):
        return []

    return [
        repo
        for repo in repos
        if not repo.get("isFork") and not repo.get("isArchived")
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
    for line in output.splitlines():
        if line.startswith("@@commit\t"):
            parts = line.split("\t")
            include_commit = len(parts) >= 4 and author_matches(parts[2], parts[3])
            continue

        if not include_commit or not line.strip():
            continue

        parts = line.split("\t", 2)
        if len(parts) != 3 or "-" in parts[:2]:
            continue

        additions, deletions, path = parts
        if additions.isdigit() and deletions.isdigit():
            files.append(
                {
                    "additions": int(additions),
                    "deletions": int(deletions),
                    "path": path,
                }
            )

    return files


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
        if repo_name.startswith(f"{LOGIN}/"):
            continue

        number = pr.get("number")
        if not repo_name or not number:
            continue

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


def format_count(value: int) -> str:
    return f"{value:,}"


def bar_segments(rows: list[tuple[str, int, float]], x: int, y: int, width: int) -> str:
    total_lines = sum(lines for _, lines, _ in rows)
    cursor = x
    parts: list[str] = []
    for index, (language, lines, _) in enumerate(rows):
        segment_width = round((lines / total_lines) * width) if total_lines else 0
        if index == len(rows) - 1:
            segment_width = x + width - cursor
        if segment_width <= 0:
            continue

        color = LANGUAGE_COLORS.get(language, "#8c959f")
        parts.append(
            f'<rect x="{cursor}" y="{y}" width="{segment_width}" height="14" '
            f'rx="3" fill="{color}"><title>{html.escape(language)}: '
            f'{format_count(lines)} changed lines</title></rect>'
        )
        cursor += segment_width

    return "\n  ".join(parts)


def render_svg(counter: Counter[str]) -> str:
    rows = rounded_rows(counter)
    generated = dt.datetime.now(dt.timezone.utc).date().isoformat()
    row_height = 28
    height = 104 + (len(rows) * row_height)

    legend = []
    for index, (language, lines, percent) in enumerate(rows):
        y = 86 + index * row_height
        color = LANGUAGE_COLORS.get(language, "#8c959f")
        label = html.escape(language)
        legend.append(
            f'<circle cx="22" cy="{y + 5}" r="5" fill="{color}" />\n'
            f'  <text class="label" x="38" y="{y + 9}">{label}</text>\n'
            f'  <text class="value" x="470" y="{y + 9}">{percent:.1f}%</text>\n'
            f'  <text class="muted right" x="646" y="{y + 9}">'
            f'{format_count(lines)} lines</text>'
        )

    legend_markup = "\n  ".join(legend)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="680" height="{height}" viewBox="0 0 680 {height}" role="img" aria-labelledby="title desc">
  <title id="title">Authored code changes by language</title>
  <desc id="desc">Programming language chart calculated from authored commits and external pull requests. Documentation, vendor, generated, lock, and asset files are ignored.</desc>
  <style>
    .bg {{ fill: #ffffff; }}
    .title {{ fill: #24292f; font: 600 18px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .label {{ fill: #24292f; font: 500 13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .value {{ fill: #24292f; font: 600 13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .muted {{ fill: #6e7781; font: 400 12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .right {{ text-anchor: end; }}
    .border {{ stroke: #d0d7de; }}
    @media (prefers-color-scheme: dark) {{
      .bg {{ fill: #0d1117; }}
      .title, .label, .value {{ fill: #e6edf3; }}
      .muted {{ fill: #8b949e; }}
      .border {{ stroke: #30363d; }}
    }}
  </style>
  <rect class="bg border" x="0.5" y="0.5" width="679" height="{height - 1}" rx="6" />
  <text class="title" x="20" y="31">Authored code changes</text>
  <text class="muted" x="20" y="52">commits + external PRs - docs/vendor/generated ignored - generated {generated}</text>
  {bar_segments(rows, 20, 66, 640)}
  {legend_markup}
</svg>
'''


def main() -> int:
    counter: Counter[str] = Counter()
    owned_repos = repos_owned_by_login()
    local_repos = local_owned_repo_paths(owned_repos)

    for _, repo_path in sorted(local_repos.items()):
        for file_info in authored_local_changes(repo_path):
            add_file(counter, file_info)

    for file_info in external_pull_request_files():
        add_file(counter, file_info)

    if not counter:
        print("No language data found.", file=sys.stderr)
        return 1

    ASSET_PATH.parent.mkdir(exist_ok=True)
    ASSET_PATH.write_text(render_svg(counter), encoding="utf-8")

    print(f"local repos: {len(local_repos)}")
    for language, lines, percent in rounded_rows(counter):
        print(f"{language}\t{lines}\t{percent:.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
