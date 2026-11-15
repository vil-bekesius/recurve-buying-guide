"""Build the static site into docs/ for GitHub Pages.

Pages are HTML fragments in content/<stem>.html. Tables live in
data/products.json and are inserted where a fragment contains
{{table:<id>}}.

    python3 build.py            build into docs/
    python3 build.py --serve    build, then preview at http://localhost:8000

The build also adds, to every page: the header and tab bar, an "On this page"
list on longer pages, tick boxes on the kit list, and previous/next links.

It stops with a clear message, and leaves docs/ untouched, if a page asks for
a table that doesn't exist, an internal link or #anchor points nowhere, or two
elements on a page share an id.
"""

import hashlib
import html
import json
import re
import shutil
import struct
import subprocess
import sys
import zlib
from datetime import date
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).parent
CONTENT = ROOT / "content"
OUT = ROOT / "docs"

SITE = "UK Olympic Recurve Buying Guide"
SITE_URL = "https://vil-bekesius.github.io/recurve-buying-guide/"  # the live address, for link previews
BASE_PATH = "/recurve-buying-guide/"  # where the site lives on GitHub Pages (used by the 404 page)
SHARE_IMAGE = "share.png"  # picture shown when a link to the site is shared

# (file stem, tab label, page title, description for search results and link previews).
# The order is the reading order used by the previous/next links.
PAGES = [
    ("index", "Home", SITE,
     "My opinionated guide to buying kit for Olympic recurve target archery in the UK: "
     "your first bow, the upgrades worth making, and the shops I use."),
    ("before-you-buy", "Before you buy", "Before you buy",
     "When to buy your first recurve bow, how to prepare for a shop fitting, "
     "and where to spend and save."),
    ("starter", "Starter", "Starter kit",
     "A first Olympic recurve kit: which riser to buy, 22–26 lb first limbs, "
     "a printable kit list, arrows and stabilisers."),
    ("intermediate", "Intermediate", "Intermediate kit",
     "What to upgrade after your first recurve bow, and when: clicker, stabilisers, "
     "limbs and aluminium-carbon arrows, plus a look further ahead."),
    ("second-hand", "Second-hand", "Buying second-hand",
     "Buying second-hand recurve archery kit in the UK: where to look, "
     "what to pay, and how to check risers and limbs."),
    ("shops", "UK shops", "Recommended UK shops",
     "UK archery shops I have used and been happy with."),
    ("glossary", "Glossary", "Glossary",
     "Plain-English definitions of recurve archery terms used in this guide: "
     "limb pocket, brace height, nocking point, centre shot, spine and more."),
]

# Old pages that were merged into others: old URL -> new location
REDIRECTS = {
    "advanced": "intermediate.html#further-ahead",
    "limbs-arrows": "intermediate.html#stepping-up",
}

TOC_MIN_WORDS = 500  # shorter pages get no "On this page" list

TABLE_TAG = re.compile(r"\{\{table:([\w-]+)\}\}")
H2 = re.compile(r'<h2(?: id="([^"]+)")?>(.*?)</h2>', re.S)
LEAD = re.compile(r'<p class="lead">.*?</p>', re.S)

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

def share_image(width=1200, height=630):
    """The target mark on the page background, as PNG bytes, for link previews."""
    background = bytes.fromhex("0d1117")
    rings = [(15.5, "f0f6fc"), (12.4, "0d1117"), (9.3, "58a6ff"), (6.2, "f85149"), (3.1, "e3b341")]
    scale = height * 0.36 / 15.5  # the outer ring fills 72% of the height
    cx, cy = width // 2, height // 2
    rows = []
    for y in range(height):
        row = bytearray(background * width)
        for radius, colour in rings:  # largest first, so smaller rings paint over it
            half = (radius * scale) ** 2 - (y - cy) ** 2
            if half > 0:
                x0, x1 = round(cx - half**0.5), round(cx + half**0.5)
                row[x0 * 3 : x1 * 3] = bytes.fromhex(colour) * (x1 - x0)
        rows.append(b"\x00" + bytes(row))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )


# The only script on the site. Everything still works without it.
SCRIPT = """<script>
(function () {
  // Bring the current tab into view in the scrolling tab bar.
  var bar = document.querySelector(".tabs-inner");
  var tab = bar && bar.querySelector("[aria-current]");
  if (tab) bar.scrollLeft = tab.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2;
  // "Print checklist" prints only the checklist (see the print rules in style.css).
  document.querySelectorAll("button.print-checklist").forEach(function (button) {
    button.hidden = false;
    button.addEventListener("click", function () {
      document.body.classList.add("checklist-only");
      window.print();
    });
  });
  window.addEventListener("afterprint", function () {
    document.body.classList.remove("checklist-only");
  });
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
    """A cell is a string, or {"text": ..., "href": ..., "note": ...}.

    "href" links the text to another part of the site (checked like every other
    internal link); "note" adds a greyed note after it. Both are optional.
    """
    if isinstance(value, dict):
        text = esc(value["text"])
        if value.get("href"):
            text = f'<a href="{esc(value["href"])}">{text}</a>'
        if value.get("note"):
            text += f' <span class="note">{esc(value["note"])}</span>'
        return text
    return esc(value)


def render_table(table):
    columns = table["columns"]
    tick = table.get("tick")  # a tick box in front of each row's first cell
    stack = len(columns) >= 3  # wide tables become stacked cards on phones
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)

    def td(col, value, first):
        inner = cell(value)
        if tick and first:
            inner = f'<label><input type="checkbox"><span>{inner}</span></label>'
        label = f' data-label="{esc(col)}"' if stack else ""
        return f"<td{label}>{inner}</td>"

    rows = "".join(
        "<tr>"
        + "".join(
            td(col, c, first=(i == 0))
            for i, (col, c) in enumerate(zip(columns, row))
        )
        + "</tr>"
        for row in table["rows"]
    )
    caption = f"<caption>{esc(table['caption'])}</caption>" if table.get("caption") else ""
    cls = ' class="stack"' if stack else ""
    wrap = "table-wrap checklist" if tick else "table-wrap"
    return (
        f'<div class="{wrap}"><table{cls}>{caption}'
        f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def add_toc(body, name, problems):
    """Give every <h2> an id, and add an "On this page" list to longer pages."""
    unmatched = len(re.findall(r"<h2\b", body)) - len(H2.findall(body))
    if unmatched:
        problems.append(
            f'{name}: {unmatched} <h2> tag(s) not written as <h2> or <h2 id="...">, '
            "so they would get no anchor"
        )
    sections = []

    def heading(match):
        text = match.group(2)
        anchor = match.group(1) or re.sub(r"[^a-z0-9]+", "-", strip_tags(text).lower()).strip("-")
        sections.append((anchor, strip_tags(text).split(":")[0]))
        return f'<h2 id="{anchor}">{text}</h2>'

    body = H2.sub(heading, body)
    if len(strip_tags(body).split()) < TOC_MIN_WORDS or len(sections) < 2:
        return body
    links = "".join(f'<li><a href="#{anchor}">{label}</a></li>' for anchor, label in sections)
    toc = f'<details class="toc"><summary>On this page</summary><ol>{links}</ol></details>'
    return LEAD.sub(lambda m: f"{m.group(0)}\n{toc}", body, count=1)


def last_reviewed(stem):
    """A "Last reviewed" line from the last commit that touched the page's content file."""
    try:
        iso = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", f"content/{stem}.html"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        day = date.fromisoformat(iso)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return ""  # no git, or the file has never been committed
    return f'<p class="reviewed">Last reviewed <time datetime="{iso}">{day.day} {day:%B %Y}</time></p>\n'


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
    body = f"{body}\n{last_reviewed(stem)}{render_pager(index)}"
    return page_shell(stem, title, description, body, css_href)


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
    # Real pages state their own address; the 404 page has none.
    address = ""
    if not base:
        url = SITE_URL + ("" if stem == "index" else f"{stem}.html")
        address = f'<link rel="canonical" href="{url}">\n<meta property="og:url" content="{url}">\n'
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
<meta property="og:image" content="{SITE_URL}{SHARE_IMAGE}">
<meta name="twitter:card" content="summary_large_image">
{address}<link rel="icon" href="{FAVICON}">
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
        body = add_toc(body, f"{stem}.html", problems)
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
    (OUT / SHARE_IMAGE).write_bytes(share_image())
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
