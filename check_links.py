"""Check that the external links in content/*.html still work.

    python3 check_links.py

Run it by hand now and then. It is not part of build.py, so a shop's website
being down never blocks publishing. Exits with status 1 if any link fails.
"""

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONTENT = Path(__file__).parent / "content"
TIMEOUT = 15  # seconds
HEADERS = {"User-Agent": "Mozilla/5.0 (link check)"}


def status(url):
    """Return the HTTP status for url, or a short description of the failure."""
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return f"failed: {getattr(error, 'reason', error)}"


def main():
    links = {}  # url -> pages that use it
    for page in sorted(CONTENT.glob("*.html")):
        for url in re.findall(r'href="(https?://[^"]+)"', page.read_text(encoding="utf-8")):
            links.setdefault(url, []).append(page.name)

    failed = 0
    for url, pages in links.items():
        result = status(url)
        # Some shops refuse automated requests (403) although the page is fine in a browser.
        ok = result == 200 or result == 403
        failed += not ok
        note = " (refuses automated checks; open it in a browser)" if result == 403 else ""
        print(f"{'ok  ' if ok else 'FAIL'} {result} {url}  [{', '.join(pages)}]{note}")
    print(f"{len(links)} links checked, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
