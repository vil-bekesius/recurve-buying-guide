"""Build the static site into docs/ for GitHub Pages.

Pages are HTML fragments in content/*.html. Product tables live in
data/products.json and are inserted where a fragment contains
{{table:<id>}}. Run:  python3 build.py
"""

import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
CONTENT = ROOT / "content"
OUT = ROOT / "docs"
DATA = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))

# (file stem, nav label, page title)
PAGES = [
    ("index", "Home", "UK Olympic recurve buying guide"),
    ("before-you-buy", "Before you buy", "Before you buy"),
    ("starter", "Starter", "Starter kit"),
    ("intermediate", "Intermediate", "Intermediate kit"),
    ("second-hand", "Second-hand", "Buying second-hand"),
    ("shops", "UK shops", "Recommended UK shops"),
]

# Old pages that were merged into others: old URL -> new location
REDIRECTS = {
    "advanced": "intermediate.html#further-ahead",
    "limbs-arrows": "intermediate.html#stepping-up",
}


def esc(s):
    return html.escape(str(s), quote=True)


def cell(value):
    """A cell is a string, or {"text":..., "url":..., "note":...}, or a list of those."""
    if isinstance(value, list):
        return "<br>".join(cell(v) for v in value)
    if isinstance(value, dict):
        text = esc(value["text"])
        if value.get("url"):
            text = f'<a href="{esc(value["url"])}" rel="noopener">{text}</a>'
        if value.get("note"):
            text += f' <span class="note">{esc(value["note"])}</span>'
        return text
    return esc(value)


def render_table(table_id):
    t = DATA["tables"][table_id]
    head = "".join(f"<th>{esc(c)}</th>" for c in t["columns"])
    rows = []
    for r in t["rows"]:
        rows.append("<tr>" + "".join(f"<td>{cell(c)}</td>" for c in r) + "</tr>")
    caption = f"<caption>{esc(t['caption'])}</caption>" if t.get("caption") else ""
    return (
        f'<div class="table-wrap"><table>{caption}<thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_page(stem, title, body):
    links = []
    for s, label, _ in PAGES:
        href = "./" if s == "index" else f"{s}.html"
        current = ' aria-current="page"' if s == stem else ""
        links.append(f'<a href="{href}"{current}>{esc(label)}</a>')
    nav = "".join(links)
    return f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{"UK Olympic Recurve Buying Guide" if stem == "index" else esc(title) + " | UK Olympic Recurve Buying Guide"}</title>
<meta name="description" content="An opinionated guide to buying Olympic recurve archery equipment in the UK, from your first bow to club competition kit.">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="site-header">
  <a class="brand" href="./">UK Olympic Recurve Buying Guide</a>
  <nav>{nav}</nav>
</header>
<main>
{body}
</main>
<footer>
  <p>Independent guide: no affiliate links, no sponsorship.</p>
</footer>
</body>
</html>
"""


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copy(ROOT / "style.css", OUT / "style.css")
    (OUT / ".nojekyll").write_text("")
    used = set()
    for stem, _, title in PAGES:
        body = (CONTENT / f"{stem}.html").read_text(encoding="utf-8")

        def table(m):
            used.add(m.group(1))
            return render_table(m.group(1))

        body = re.sub(r"\{\{table:([\w-]+)\}\}", table, body)
        (OUT / f"{stem}.html").write_text(render_page(stem, title, body), encoding="utf-8")
    unused = set(DATA["tables"]) - used
    if unused:
        print("Warning: tables not used on any page:", ", ".join(sorted(unused)))
    for old, new in REDIRECTS.items():
        (OUT / f"{old}.html").write_text(
            f'<!DOCTYPE html><meta charset="utf-8"><title>Moved</title>'
            f'<meta http-equiv="refresh" content="0; url={new}">'
            f'<link rel="canonical" href="{new}"><p>This page has moved: <a href="{new}">{new}</a></p>\n',
            encoding="utf-8",
        )
    print(f"Built {len(PAGES)} pages and {len(REDIRECTS)} redirects into {OUT}")


if __name__ == "__main__":
    build()
