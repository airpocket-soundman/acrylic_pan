"""Export the five-section exhibition quick guide as an A4 portrait PDF.
Requires playwright and PyMuPDF; uses the same asset files as the full manual.
"""
import argparse
from pathlib import Path
import shutil
import pymupdf
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--browser-executable',required=True)
args=parser.parse_args()
output=ROOT/'output/pdf/solistpan-simple.pdf';output.parent.mkdir(parents=True,exist_ok=True)
footer='<div style="width:100%;font-family:Arial,sans-serif;font-size:8px;color:#526b74;padding:0 15mm;display:flex;justify-content:space-between"><span>Solistpan | Quick Guide | 2026.11.16</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=args.browser_executable,headless=True)
    page=browser.new_page(viewport={'width':1280,'height':1000})
    page.goto((ROOT/'manual/simple.html').as_uri(),wait_until='networkidle')
    assert page.locator('main img').evaluate_all('(es)=>es.every(e=>e.complete&&e.naturalWidth>0)')
    page.pdf(path=str(output),format='A4',prefer_css_page_size=True,print_background=True,display_header_footer=True,header_template='<span></span>',footer_template=footer,tagged=True,outline=True)
    browser.close()
doc=pymupdf.open(output)
assert all(abs(p.rect.width-595)<1 and abs(p.rect.height-842)<1 for p in doc)
print(f'{len(doc)} pages, A4 portrait: {output}')
shutil.copy2(output,ROOT/'manual/solistpan-simple.pdf')
