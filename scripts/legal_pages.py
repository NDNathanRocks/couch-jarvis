"""Publish the legal texts as pages of the phone app's website (the Store needs a privacy URL).

    python scripts/legal_pages.py           # write the pages into web-remote/public/
    python scripts/legal_pages.py --check   # exit 1 if one is out of date (not licenses.html)

Pages: privacy, terms, get, support (from docs/) and licenses (from the installed web packages).

docs/PRIVACY.md and docs/TERMS.md are the source; this turns the small Markdown subset they use
(headings, paragraphs, lists, tables, quotes, bold, code, links) into self-contained pages.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "web-remote" / "public"
PAGES = {
    "privacy.html": ROOT / "docs" / "PRIVACY.md",
    "terms.html": ROOT / "docs" / "TERMS.md",
    "get.html": ROOT / "docs" / "GET.md",
    "support.html": ROOT / "docs" / "SUPPORT.md",
}

STYLE = """
:root { color-scheme: dark; --bg: #0b0d12; --text: #e8eaf0; --muted: #9aa3b5; --line: #252b38;
        --accent: #0ea5e9; }
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) { color-scheme: light; --bg: #ffffff; --text: #111318;
        --muted: #4b5262; --line: #dde1ea; --accent: #0369a1; }
}
body { margin: 0; background: var(--bg); color: var(--text);
       font: 16px/1.6 Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 46rem; margin: 0 auto; padding: 2rem 16px 4rem; }
h1 { font-size: 1.8rem; line-height: 1.2; } h2 { font-size: 1.2rem; margin-top: 2rem; }
a { color: var(--accent); } code { font-size: .9em; }
blockquote { margin: 1rem 0; padding: .5rem 1rem; border-left: 3px solid var(--accent);
             color: var(--muted); }
.table { overflow-x: auto; } table { border-collapse: collapse; width: 100%; font-size: .95rem; }
th, td { text-align: left; vertical-align: top; padding: .5rem;
         border-bottom: 1px solid var(--line); }
nav { font-size: .9rem; color: var(--muted); }
"""


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', out)


def to_html(markdown: str) -> tuple[str, str]:
    """(title, body html) for the Markdown subset the legal texts use."""
    lines = markdown.splitlines()
    body: list[str] = []
    title = "Armrest"
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            body.append(f"<h1>{inline(title)}</h1>")
            i += 1
        elif line.startswith("## "):
            body.append(f"<h2>{inline(line[3:].strip())}</h2>")
            i += 1
        elif line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip("> ").strip())
                i += 1
            body.append(f"<blockquote>{inline(' '.join(quote))}</blockquote>")
        elif line.startswith("- "):
            items: list[str] = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("  ")):
                if lines[i].startswith("- "):
                    items.append(lines[i][2:].strip())
                elif items:
                    items[-1] += " " + lines[i].strip()
                i += 1
            body.append("<ul>" + "".join(f"<li>{inline(t)}</li>" for t in items) + "</ul>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, *rest = rows
            table = "<tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>"
            table += "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in rest
            )
            body.append(f'<div class="table"><table>{table}</table></div>')
        else:
            para = []
            while (
                i < len(lines)
                and lines[i].strip()
                and not lines[i].startswith(("#", "-", "|", ">"))
            ):
                para.append(lines[i].strip())
                i += 1
            body.append(f"<p>{inline(' '.join(para))}</p>")
    return title, "\n".join(body)


def page(title: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n<main>\n"
        '<nav><a href="/">Armrest</a> · <a href="/privacy.html">Privacy</a> · '
        '<a href="/terms.html">Terms</a> · <a href="/licenses.html">Licences</a></nav>\n'
        f"{body}\n</main>\n</body>\n</html>\n"
    )


def legal_pages() -> dict[str, str]:
    return {name: page(*to_html(src.read_text(encoding="utf-8"))) for name, src in PAGES.items()}


def licences_page() -> str:
    sys.path.insert(0, str(ROOT / "scripts"))
    from licenses import web_packages

    rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{html.escape(version)}</td>"
        f"<td>{html.escape(licence)}</td></tr>"
        for name, (version, licence) in web_packages().items()
    )
    body = (
        "<h1>Third-party licences</h1>\n<p>The phone app is built with these open-source packages, "
        "used under their licences. The PC app lists its own in THIRD_PARTY_NOTICES.txt, "
        "also shown in the Armrest window under Help.</p>\n"
        f'<div class="table"><table><tr><th>Package</th><th>Version</th><th>Licence</th></tr>{rows}'
        "</table></div>"
    )
    return page("Third-party licences", body)


def main(argv: list[str]) -> int:
    pages = legal_pages()
    if "--check" in argv:
        stale = [
            n
            for n, text in pages.items()
            if not (PUBLIC / n).is_file() or (PUBLIC / n).read_text(encoding="utf-8") != text
        ]
        if stale:
            print(f"out of date: {', '.join(stale)} (run python scripts/legal_pages.py)")
            return 1
        print("legal pages: up to date")
        return 0
    pages["licenses.html"] = licences_page()
    for name, text in pages.items():
        (PUBLIC / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote web-remote/public/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
