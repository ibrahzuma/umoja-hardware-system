"""Render docs/Umoja_Training_Guide.md to a styled HTML file and print it to PDF.

Markdown -> HTML (python-markdown), then HTML -> PDF with headless Chromium
through Playwright (the same browser the capture scripts use). Images use
relative `screens/...` paths, so the HTML is written into docs/ for them to
resolve. Run with `--no-pdf` to stop after the HTML.
"""

from __future__ import annotations

from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "Umoja_Training_Guide.md"
HTML = ROOT / "docs" / "Umoja_Training_Guide.html"
PDF = ROOT / "docs" / "Umoja_Training_Guide.pdf"

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", "Calibri", Arial, sans-serif;
  color: #141a23; font-size: 11.5pt; line-height: 1.5;
  max-width: 100%; margin: 0;
}
h1 { font-size: 26pt; color: #2d3bb0; margin: 0 0 6pt; line-height: 1.15; }
h2 {
  font-size: 18pt; color: #ffffff; background: #141a23;
  padding: 8pt 12pt; border-radius: 6px; margin: 20pt 0 12pt;
  page-break-before: always; page-break-after: avoid;
}
h2:first-of-type { page-break-before: avoid; }
h3 {
  font-size: 14.5pt; color: #2d3bb0; margin: 16pt 0 6pt;
  border-bottom: 2px solid #e3e7f7; padding-bottom: 3pt;
  page-break-after: avoid;
}
h4 { font-size: 12pt; color: #16a37b; margin: 12pt 0 4pt; page-break-after: avoid; }
p { margin: 5pt 0; }
strong { color: #141a23; }
blockquote {
  margin: 8pt 0; padding: 6pt 12pt; background: #fff8e6;
  border-left: 4px solid #e0a423; color: #5a4a18; border-radius: 0 4px 4px 0;
}
blockquote p { margin: 2pt 0; }
code {
  font-family: "Consolas", "Courier New", monospace; font-size: 9.5pt;
  background: #f1f3f8; padding: 1px 4px; border-radius: 3px; color: #b5274a;
}
pre {
  background: #1b2230; color: #e6ebf5; padding: 10pt 12pt; border-radius: 6px;
  font-family: "Consolas", "Courier New", monospace; font-size: 9pt;
  line-height: 1.35; overflow-x: auto; page-break-inside: avoid;
  white-space: pre;
}
pre code { background: none; color: inherit; padding: 0; font-size: 9pt; }
ul, ol { margin: 5pt 0 5pt 0; padding-left: 20pt; }
li { margin: 2pt 0; }
table {
  border-collapse: collapse; width: 100%; margin: 10pt 0; font-size: 10pt;
  page-break-inside: avoid;
}
th, td { border: 1px solid #d4dae6; padding: 5pt 8pt; text-align: left; vertical-align: top; }
th { background: #eef1fb; color: #2d3bb0; font-weight: 600; }
tr:nth-child(even) td { background: #f8f9fc; }
img { max-width: 100%; height: auto; border: 1px solid #d4dae6; border-radius: 4px; margin: 4pt 0; page-break-inside: avoid; }
td img { max-width: 100%; }
hr { border: none; border-top: 1px solid #d4dae6; margin: 16pt 0; }
a { color: #2d3bb0; text-decoration: none; }
"""

HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Umoja Hardware System — User Training Guide</title>
<style>{css}</style></head>
<body>{body}</body></html>
"""


def build() -> None:
    text = MD.read_text(encoding="utf-8")
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list", "md_in_html"],
    )
    HTML.write_text(HTML_SHELL.format(css=CSS, body=body), encoding="utf-8")
    print(f"wrote {HTML}  ({HTML.stat().st_size // 1024} KB)")


def print_pdf() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(HTML.resolve().as_uri(), wait_until="load")
        page.wait_for_timeout(1500)  # let every screenshot decode before printing
        page.pdf(
            path=str(PDF), format="A4", print_background=True, prefer_css_page_size=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                '<div style="width:100%;font-size:8pt;color:#6b7686;padding:0 16mm;'
                'display:flex;justify-content:space-between;">'
                '<span>Umoja Hardware System — User Training Guide</span>'
                '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'),
            margin={"top": "16mm", "bottom": "18mm", "left": "0", "right": "0"},
        )
        browser.close()
    print(f"wrote {PDF}  ({PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    import sys

    build()
    if "--no-pdf" not in sys.argv:
        print_pdf()
