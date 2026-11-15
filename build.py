"""Build the static site into docs/ for GitHub Pages.

Pages are HTML fragments in content/<stem>.html. Tables live in
data/products.json and are inserted where a fragment contains
{{table:<id>}}.

    python3 build.py            build into docs/
    python3 build.py --serve    build, then preview at http://localhost:8000

The build stops with a clear message, and leaves docs/ untouched, if a page
asks for a table that doesn't exist or an internal link or #anchor points
nowhere.
"""

import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CONTENT = ROOT / "content"
OUT = ROOT / "docs"

SITE = "UK Olympic Recurve Buying Guide"
DESCRIPTION = (
    "An opinionated guide to buying Olympic recurve archery equipment in the UK, "
    "from your first bow to club competition kit."
)

# (file stem, nav label, page title). The home page's title is the site name.
PAGES = [
    ("index", "Home", SITE),
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

TABLE_TAG = re.compile(r"\{\{table:([\w-]+)\}\}")


def esc(s):
    return html.escape(str(s), quote=True)


def cell(value):
    """A cell is a string, or {"text": ..., "note": ...} to add a greyed note."""
    if isinstance(value, dict):
        text = esc(value["text"])
        if value.get("note"):
            text += f' <span class="note">{esc(value["note"])}</span>'
        return text
    return esc(value)


def render_table(table):
    head = "".join(f"<th>{esc(c)}</th>" for c in table["columns"])
    rows = "".join(
        "<tr>" + "".join(f"<td>{cell(c)}</td>" for c in row) + "</tr>"
        for row in table["rows"]
    )
    caption = f"<caption>{esc(table['caption'])}</caption>" if table.get("caption") else ""
    return (
        f'<div class="table-wrap"><table>{caption}<thead><tr>{head}</tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def render_page(stem, title, body, css_href):
    nav = "".join(
        '<a href="{}"{}>{}</a>'.format(
            "./" if s == "index" else f"{s}.html",
            ' aria-current="page"' if s == stem else "",
            esc(label),
        )
        for s, label, _ in PAGES
    )
    full_title = SITE if stem == "index" else f"{esc(title)} | {SITE}"
    return f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{full_title}</title>
<meta name="description" content="{esc(DESCRIPTION)}">
<link rel="stylesheet" href="{css_href}">
</head>
<body>
<header class="site-header">
  <a class="brand" href="./">{SITE}</a>
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


def render_redirect(target):
    return (
        '<!DOCTYPE html><meta charset="utf-8"><title>Moved</title>'
        f'<meta http-equiv="refresh" content="0; url={target}">'
        f'<link rel="canonical" href="{target}">'
        f'<p>This page has moved: <a href="{target}">{target}</a></p>\n'
    )


def check_links(pages):
    """Return a problem for every internal link whose page or #anchor is missing."""
    ids = {name: set(re.findall(r'\bid="([^"]+)"', text)) for name, text in pages.items()}
    problems = []
    for name, text in pages.items():
        for href in re.findall(r'\bhref="([^"]+)"', text):
            if re.match(r"[a-z][a-z0-9+.-]*:", href) or href.startswith("style.css"):
                continue  # external link or the stylesheet
            target, _, anchor = href.partition("#")
            target = "index.html" if target == "./" else target or name
            if target not in pages:
                problems.append(f"{name}: link to missing page '{href}'")
            elif anchor and anchor not in ids[target]:
                problems.append(f"{name}: link to missing anchor '{href}'")
    return problems


def build():
    tables = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))["tables"]
    css = (ROOT / "style.css").read_bytes()
    # The hash makes browsers fetch a changed stylesheet straight away.
    css_href = f"style.css?v={hashlib.sha1(css).hexdigest()[:8]}"

    pages, problems, used = {}, [], set()
    for stem, _, title in PAGES:
        source = CONTENT / f"{stem}.html"
        if not source.exists():
            problems.append(f"{stem}: missing file {source.relative_to(ROOT)}")
            continue

        def table(match, stem=stem):
            table_id = match.group(1)
            if table_id not in tables:
                problems.append(f"{stem}.html: unknown table '{table_id}'")
                return ""
            used.add(table_id)
            return render_table(tables[table_id])

        body = TABLE_TAG.sub(table, source.read_text(encoding="utf-8"))
        pages[f"{stem}.html"] = render_page(stem, title, body, css_href)
    for old, target in REDIRECTS.items():
        pages[f"{old}.html"] = render_redirect(target)

    problems += check_links(pages)
    if problems:
        sys.exit("Build failed:\n  " + "\n  ".join(problems))

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "style.css").write_bytes(css)
    (OUT / ".nojekyll").write_text("")
    for name, text in pages.items():
        (OUT / name).write_text(text, encoding="utf-8")

    unused = sorted(set(tables) - used)
    if unused:
        print("Warning: tables not used on any page:", ", ".join(unused))
    print(f"Built {len(PAGES)} pages and {len(REDIRECTS)} redirects into {OUT}")


def serve(port=8000):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    handler = partial(SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"Preview at http://localhost:{port} (Ctrl+C to stop)")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    build()
    if "--serve" in sys.argv[1:]:
        serve()
