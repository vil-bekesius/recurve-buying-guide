"""Build the static site into docs/ for GitHub Pages.

Pages are HTML fragments in content/<stem>.html. Tables live in
data/products.json and are inserted where a fragment contains
{{table:<id>}}.

    python3 build.py            build into docs/
    python3 build.py --serve    build, then preview at http://localhost:8000

The build also adds, to every page: the header and tab bar, an "On this page"
list on longer pages, tick boxes on the checklist, and previous/next links.

It stops with a clear message, and leaves docs/ untouched, if a page asks for
a table that doesn't exist, an internal link or #anchor points nowhere, or two
elements on a page share an id.
"""

import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).parent
CONTENT = ROOT / "content"
OUT = ROOT / "docs"

SITE = "UK Olympic Recurve Buying Guide"
BASE_PATH = "/recurve-buying-guide/"  # where the site lives on GitHub Pages (used by the 404 page)

# (file stem, tab label, page title, description for search results and link previews).
# The order is the reading order used by the previous/next links.
PAGES = [
    ("index", "Home", SITE,
     "My opinionated guide to buying Olympic recurve archery kit in the UK: "
     "your first bow, the upgrades worth making, and the shops I use."),
    ("before-you-buy", "Before you buy", "Before you buy",
     "When to buy your first recurve bow, how to prepare for a shop fitting, "
     "where to spend and save, and a printable shopping checklist."),
    ("starter", "Starter", "Starter kit",
     "A first Olympic recurve kit: which riser to buy, 22–26 lb first limbs, "
     "an example build, arrows and stabilisers."),
    ("intermediate", "Intermediate", "Intermediate kit",
     "What to upgrade after your first recurve bow, and when: clicker, stabilisers, "
     "limbs and aluminium-carbon arrows, plus a look further ahead."),
    ("second-hand", "Second-hand", "Buying second-hand",
     "Buying second-hand recurve archery kit in the UK: where to look, "
     "what to pay, and how to check risers and limbs."),
    ("shops", "UK shops", "Recommended UK shops",
     "UK archery shops I have used and been happy with."),
]

# Old pages that were merged into others: old URL -> new location
REDIRECTS = {
    "advanced": "intermediate.html#further-ahead",
    "limbs-arrows": "intermediate.html#stepping-up",
}

TOC_MIN_SECTIONS = 4  # pages with fewer <h2> sections get no "On this page" list

TABLE_TAG = re.compile(r"\{\{table:([\w-]+)\}\}")
H2 = re.compile(r'<h2(?: id="([^"]+)")?>(.*?)</h2>', re.S)
LEAD = re.compile(r'<p class="lead">.*?</p>', re.S)
CHECKLIST = re.compile(r'(<ul class="checklist">)(.*?)(</ul>)', re.S)

# Target-face mark: the header logo and the favicon.
MARK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" aria-hidden="true">'
    '<circle cx="16" cy="16" r="15.5" fill="#f0f6fc"/>'
    '<circle cx="16" cy="16" r="12.4" fill="#0d1117"/>'
    '<circle cx="16" cy="16" r="9.3" fill="#58a6ff"/>'
    '<circle cx="16" cy="16" r="6.2" fill="#f85149"/>'
    '<circle cx="16" cy="16" r="3.1" fill="#e3b341"/>'
    "</svg>"
)
FAVICON = "data:image/svg+xml," + quote(MARK)

# The only script on the site. Everything still works without it.
SCRIPT = """<script>
(function () {
  // Bring the current tab into view in the scrolling tab bar.
  var bar = document.querySelector(".tabs-inner");
  var tab = bar && bar.querySelector("[aria-current]");
  if (tab) bar.scrollLeft = tab.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2;
  // Remember ticked checklist items on this device.
  document.querySelectorAll(".checklist input").forEach(function (box) {
    var key = "checklist:" + box.parentNode.textContent.trim();
    try { box.checked = localStorage.getItem(key) === "1"; } catch (e) {}
    box.addEventListener("change", function () {
      try { localStorage.setItem(key, box.checked ? "1" : "0"); } catch (e) {}
    });
  });
})();
</script>"""


def esc(s):
    return html.escape(str(s), quote=True)


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s)


def href(stem):
    return "./" if stem == "index" else f"{stem}.html"


def cell(value):
    """A cell is a string, or {"text": ..., "note": ...} to add a greyed note."""
    if isinstance(value, dict):
        text = esc(value["text"])
        if value.get("note"):
            text += f' <span class="note">{esc(value["note"])}</span>'
        return text
    return esc(value)


def render_table(table):
    columns = table["columns"]
    stack = len(columns) >= 3  # wide tables become stacked cards on phones
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    rows = "".join(
        "<tr>"
        + "".join(
            f'<td data-label="{esc(col)}">{cell(c)}</td>' if stack else f"<td>{cell(c)}</td>"
            for col, c in zip(columns, row)
        )
        + "</tr>"
        for row in table["rows"]
    )
    caption = f"<caption>{esc(table['caption'])}</caption>" if table.get("caption") else ""
    cls = ' class="stack"' if stack else ""
    return (
        f'<div class="table-wrap"><table{cls}>{caption}'
        f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def add_toc(body):
    """Give every <h2> an id, and add an "On this page" list to longer pages."""
    sections = []

    def heading(match):
        text = match.group(2)
        anchor = match.group(1) or re.sub(r"[^a-z0-9]+", "-", strip_tags(text).lower()).strip("-")
        sections.append((anchor, strip_tags(text).split(":")[0]))
        return f'<h2 id="{anchor}">{text}</h2>'

    body = H2.sub(heading, body)
    if len(sections) < TOC_MIN_SECTIONS:
        return body
    links = "".join(f'<li><a href="#{anchor}">{label}</a></li>' for anchor, label in sections)
    toc = f'<details class="toc"><summary>On this page</summary><ol>{links}</ol></details>'
    return LEAD.sub(lambda m: f"{m.group(0)}\n{toc}", body, count=1)


def add_checkboxes(body):
    """Turn each checklist item into a tick box."""

    def boxes(match):
        items = re.sub(
            r"<li>(.*?)</li>",
            r'<li><label><input type="checkbox"><span>\1</span></label></li>',
            match.group(2),
            flags=re.S,
        )
        return match.group(1) + items + match.group(3)

    return CHECKLIST.sub(boxes, body)


def render_pager(index):
    def link(i, cls, word):
        stem, _, title, _ = PAGES[i]
        label = "Home" if stem == "index" else esc(title)
        return f'<a class="{cls}" href="{href(stem)}"><small>{word}</small>{label}</a>'

    links = []
    if index > 0:
        links.append(link(index - 1, "prev", "Previous"))
    if index < len(PAGES) - 1:
        links.append(link(index + 1, "next", "Next"))
    return f'<nav class="pager" aria-label="Previous and next page">{"".join(links)}</nav>'


def render_page(index, body, css_href):
    stem, _, title, description = PAGES[index]
    return page_shell(stem, title, description, f"{body}\n{render_pager(index)}", css_href)


def render_404(css_href):
    body = (
        "<h1>Page not found</h1>\n"
        '<p class="lead">This page doesn\'t exist, or it has moved.</p>\n'
        '<div class="cards"><a class="card" href="./"><strong>Go to the home page</strong>'
        "<span>Start from the beginning of the guide.</span></a></div>"
    )
    # GitHub Pages serves this file for any missing address, so its links
    # must be resolved from the site's root, not from the missing address.
    base = f'<base href="{BASE_PATH}">\n'
    return page_shell("404", "Page not found", "This page doesn't exist.", body, css_href, base)


def page_shell(stem, title, description, body, css_href, base=""):
    tabs = "".join(
        '<a href="{}"{}>{}</a>'.format(
            href(s), ' aria-current="page"' if s == stem else "", esc(label)
        )
        for s, label, _, _ in PAGES
    )
    full_title = SITE if stem == "index" else f"{esc(title)} | {SITE}"
    return f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
{base}<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0d1117">
<title>{full_title}</title>
<meta name="description" content="{esc(description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<link rel="icon" href="{FAVICON}">
<link rel="stylesheet" href="{css_href}">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header">
  <a class="brand" href="./">{MARK}<span>{SITE}</span></a>
</header>
<nav class="tabs" aria-label="Site"><div class="tabs-inner">{tabs}</div></nav>
<main id="main">
{body}
</main>
<footer>
  <p>Independent guide: no affiliate links, no sponsorship.</p>
</footer>
{SCRIPT}
</body>
</html>
"""


def render_redirect(target):
    return (
        '<!DOCTYPE html><html lang="en-GB"><meta charset="utf-8"><title>Moved</title>'
        '<meta name="robots" content="noindex">'
        f'<meta http-equiv="refresh" content="0; url={target}">'
        f'<link rel="canonical" href="{target}">'
        f'<p>This page has moved: <a href="{target}">{target}</a></p>\n'
    )


def check_pages(pages):
    """Return a problem for every duplicate id and every broken internal link."""
    problems = []
    ids = {}
    for name, text in pages.items():
        found = re.findall(r'\bid="([^"]+)"', text)
        ids[name] = set(found)
        for dup in sorted({i for i in found if found.count(i) > 1}):
            problems.append(f"{name}: id '{dup}' is used more than once")
    for name, text in pages.items():
        for link in re.findall(r'\bhref="([^"]+)"', text):
            if re.match(r"[a-z][a-z0-9+.-]*:", link) or link.startswith(("style.css", "/")):
                continue  # external link, data: URI, the stylesheet, or the site root
            target, _, anchor = link.partition("#")
            target = "index.html" if target == "./" else target or name
            if target not in pages:
                problems.append(f"{name}: link to missing page '{link}'")
            elif anchor and anchor not in ids[target]:
                problems.append(f"{name}: link to missing anchor '{link}'")
    return problems


def render_site():
    """Return ({filename: html}, problems, unused table ids, stylesheet bytes)."""
    tables = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))["tables"]
    css = (ROOT / "style.css").read_bytes()
    # The hash makes browsers fetch a changed stylesheet straight away.
    css_href = f"style.css?v={hashlib.sha1(css).hexdigest()[:8]}"

    pages, problems, used = {}, [], set()
    for index, (stem, *_) in enumerate(PAGES):
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
        body = add_checkboxes(add_toc(body))
        pages[f"{stem}.html"] = render_page(index, body, css_href)
    for old, target in REDIRECTS.items():
        pages[f"{old}.html"] = render_redirect(target)
    pages["404.html"] = render_404(css_href)

    problems += check_pages(pages)
    return pages, problems, sorted(set(tables) - used), css


def build():
    pages, problems, unused, css = render_site()
    if problems:
        sys.exit("Build failed:\n  " + "\n  ".join(problems))

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "style.css").write_bytes(css)
    (OUT / ".nojekyll").write_text("")
    for name, text in pages.items():
        (OUT / name).write_text(text, encoding="utf-8")

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
