#!/usr/bin/env python3
"""Render the profile poster (light and dark SVG) from live GitHub data.

Contribution counts, monthly activity and account age come from the GitHub
GraphQL API through `gh`, so this runs in CI with the workflow's own token.
Language, commit and pull request figures come from assets/code-stats.json,
written by tools/build_language_chart.py on a machine with the private clones.
A value that cannot be fetched renders as a dash, never as a made-up zero.
"""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import html
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
LOGIN = os.environ.get("GITHUB_LOGIN", "GbrFrn")

# 12-column grid: 48px columns, 16px gutters, 44px margins -> 840 wide.
COL, GUT, MARGIN = 48, 16, 44
WIDTH = MARGIN * 2 + COL * 12 + GUT * 11

THEMES = {
    "light": {"bg": "#ffffff", "ink": "#111111", "soft": "#5c5c5c", "rule": "#d4d4d4", "red": "#e30613"},
    "dark": {"bg": "#0d1117", "ink": "#f2f2f2", "soft": "#a3a3a3", "rule": "#30363d", "red": "#ff4136"},
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def x(col: int) -> int:
    """Left edge of a 1-based grid column."""
    return MARGIN + (col - 1) * (COL + GUT)


def span(cols: int) -> int:
    return cols * COL + (cols - 1) * GUT


def fetch_github() -> dict | None:
    try:
        out = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"login={LOGIN}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return json.loads(out)["data"]["user"]
    except (subprocess.CalledProcessError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"GitHub fetch failed: {error}", file=sys.stderr)
        return None


def monthly(user: dict, today: dt.date) -> list[tuple[str, int]]:
    """The last 12 calendar months ending with the current one."""
    totals: dict[str, int] = {}
    for week in user["contributionsCollection"]["contributionCalendar"]["weeks"]:
        for day in week["contributionDays"]:
            totals[day["date"][:7]] = totals.get(day["date"][:7], 0) + day["contributionCount"]

    months = []
    year, month = today.year, today.month
    for _ in range(12):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return [(key, totals.get(key, 0)) for key in reversed(months)]


def num(value: int | None) -> str:
    return "–" if value is None else f"{value:,}"


def font_faces() -> str:
    faces = []
    for weight in (400, 600, 800):
        data = base64.b64encode((ASSETS / "fonts" / f"archivo-{weight}.woff2").read_bytes()).decode()
        faces.append(
            f"@font-face{{font-family:'Archivo';font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');}}"
        )
    return "".join(faces)


def render(theme: dict[str, str], data: dict) -> str:
    t = theme
    parts: list[str] = []
    add = parts.append

    # Lead figure: contributions, cols 1-8. Size shrinks only if the number grows past what fits.
    lead = num(data["contributions"])
    lead_size = min(168, int(span(8) / (0.6 * len(lead))))
    add(f'<text class="n" x="{x(1) - 6}" y="176" font-size="{lead_size}" fill="{t["ink"]}">{lead}</text>')
    add(f'<text class="l" x="{x(1)}" y="{176 + round(lead_size * 0.3)}" fill="{t["ink"]}">contributions on GitHub in the last 12 months</text>')

    # Supporting figures, cols 9-12, separated by hairline rules.
    side = [
        (num(data["years"]), "years on GitHub"),
        (num(data["commits"]), "authored commits"),
        (num(data["prs"]), f"merged pull requests to {num(data['pr_repos'])} outside repositories"),
    ]
    y = 44
    for value, label in side:
        add(f'<rect x="{x(9)}" y="{y}" width="{span(4)}" height="1" fill="{t["ink"]}"/>')
        add(f'<text class="n" x="{x(9) - 2}" y="{y + 50}" font-size="48" fill="{t["ink"]}">{value}</text>')
        words, lines, line = label.split(), [], ""
        for word in words:  # wrap to the 4-column measure
            if len(line) + len(word) + 1 > 27:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        lines.append(line)
        for i, text in enumerate(lines):
            add(f'<text class="s" x="{x(9)}" y="{y + 76 + i * 20}" fill="{t["soft"]}">{html.escape(text)}</text>')
        y += 94 + 20 * (len(lines) - 1)

    # Monthly activity: one red column per grid column, one shared scale.
    top = max(260, y + 16)
    add(f'<rect x="{x(1)}" y="{top}" width="{span(12)}" height="1" fill="{t["ink"]}"/>')
    add(f'<text class="h" x="{x(1)}" y="{top + 30}" fill="{t["ink"]}">Contributions per month</text>')
    chart_top, chart_h = top + 64, 120
    base = chart_top + chart_h
    months = data["months"]
    peak = max((count for _, count in months), default=0) or 1
    for i, (key, count) in enumerate(months):
        bar = round(chart_h * count / peak)
        left = x(i + 1)
        if bar:
            add(f'<rect x="{left}" y="{base - bar}" width="{COL}" height="{bar}" fill="{t["red"]}"><title>{key}: {count:,}</title></rect>')
        add(f'<text class="v" x="{left}" y="{base - bar - 8}" fill="{t["ink"]}">{count:,}</text>')
        year, month = map(int, key.split("-"))
        label = calendar.month_abbr[month] + (f" ’{year % 100:02d}" if month == 1 or i == 0 else "")
        add(f'<text class="s" x="{left}" y="{base + 22}" fill="{t["soft"]}">{label}</text>')
    add(f'<rect x="{x(1)}" y="{base}" width="{span(12)}" height="1" fill="{t["ink"]}"/>')

    # Languages: name cols 1-3, bar cols 4-10, share cols 11-12.
    top = base + 62
    add(f'<rect x="{x(1)}" y="{top}" width="{span(12)}" height="1" fill="{t["ink"]}"/>')
    add(f'<text class="h" x="{x(1)}" y="{top + 30}" fill="{t["ink"]}">Code I wrote, by language</text>')
    row = top + 64
    languages = data["languages"]
    widest = max((lang["percent"] for lang in languages), default=0) or 1
    for i, lang in enumerate(languages):
        ry = row + i * 32
        fill = t["red"] if i == 0 else t["ink"]
        width = max(2, round(span(7) * lang["percent"] / widest))
        add(f'<text class="l" x="{x(1)}" y="{ry}" fill="{t["ink"]}">{html.escape(lang["name"])}</text>')
        add(f'<rect x="{x(4)}" y="{ry - 13}" width="{width}" height="14" fill="{fill}"><title>{lang["lines"]:,} lines</title></rect>')
        add(f'<text class="l r" x="{x(12) + COL}" y="{ry}" fill="{t["ink"]}">{lang["percent"]:.1f}%</text>')
    if not languages:
        add(f'<text class="l" x="{x(1)}" y="{row}" fill="{t["soft"]}">–</text>')

    # Footer: method on the left, the as-of dates locked to the right corner.
    foot = row + max(len(languages), 1) * 32 + 16
    add(f'<rect x="{x(1)}" y="{foot}" width="{span(12)}" height="1" fill="{t["rule"]}"/>')
    add(f'<text class="f" x="{x(1)}" y="{foot + 28}" fill="{t["soft"]}">Lines changed in my own commits and PRs; docs, vendored code, imports excluded</text>')
    add(f'<text class="f" x="{x(1)}" y="{foot + 50}" fill="{t["soft"]}">GitHub data {data["github_date"]} · code data {data["code_date"]}</text>')
    height = foot + 78

    desc = (
        f"{lead} contributions in the last 12 months, {num(data['years'])} years on GitHub, "
        f"{num(data['commits'])} authored commits, {num(data['prs'])} merged pull requests to outside repositories. "
        + ", ".join(f"{lang['name']} {lang['percent']:.1f}%" for lang in languages)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="t d">
<title id="t">GbrFrn on GitHub</title>
<desc id="d">{html.escape(desc)}</desc>
<style>{font_faces()}
text{{font-family:'Archivo','Helvetica Neue',Arial,sans-serif;font-variant-numeric:tabular-nums}}
.n{{font-weight:800;letter-spacing:-0.04em}}
.h{{font-size:22px;font-weight:600}}
.l{{font-size:19px;font-weight:400}}
.s{{font-size:16px;font-weight:400}}
.v{{font-size:15px;font-weight:600}}
.f{{font-size:14px;font-weight:400}}
.r{{text-anchor:end}}
</style>
<rect width="{WIDTH}" height="{height}" fill="{t["bg"]}"/>
{chr(10).join(parts)}
</svg>
"""


def main() -> int:
    today = dt.datetime.now(dt.timezone.utc).date()
    code = json.loads((ASSETS / "code-stats.json").read_text(encoding="utf-8"))
    user = fetch_github()

    data = {
        "contributions": None,
        "years": None,
        "months": [],
        "github_date": "–",
        "commits": code.get("authored_commits"),
        "prs": code.get("merged_prs"),
        "pr_repos": code.get("merged_pr_repos"),
        "languages": code.get("languages", []),
        "code_date": code.get("generated", "–"),
    }
    if user:
        created = dt.date.fromisoformat(user["createdAt"][:10])
        data["contributions"] = user["contributionsCollection"]["contributionCalendar"]["totalContributions"]
        data["years"] = today.year - created.year - ((today.month, today.day) < (created.month, created.day))
        data["months"] = monthly(user, today)
        data["github_date"] = today.isoformat()

    for name, theme in THEMES.items():
        (ASSETS / f"profile-{name}.svg").write_text(render(theme, data), encoding="utf-8")
    print(f"rendered profile-light.svg and profile-dark.svg ({'live' if user else 'NO GitHub data'})")
    return 0 if user else 1


if __name__ == "__main__":
    raise SystemExit(main())
