"""Print the HTML manual to an A4 PDF, with a linked, numbered table of contents.

Requires playwright and PyMuPDF (fitz). Pass a local Chrome/Edge executable.
Example:
  python scripts/export-manual-pdf.py --browser-executable "C:/Program Files/Google/Chrome/Application/chrome.exe"
"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import pymupdf as fitz
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-executable',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'output/pdf/solistpan-manual.pdf')
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=ROOT/'tmp/pdfs';temporary.mkdir(parents=True,exist_ok=True)
    draft=temporary/'manual-draft.pdf'
    footer='''<div style="width:100%;font-family:Arial,sans-serif;font-size:8px;color:#526b74;padding:0 15mm;display:flex;justify-content:space-between"><span>Solistpan | 2026.11.16</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'''
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser_executable,headless=True)
        page=browser.new_page(viewport={'width':1280,'height':1000},device_scale_factor=1)
        page.goto((ROOT/'manual/index.html').as_uri(),wait_until='networkidle')
        page.emulate_media(media='print')
        page.add_style_tag(path=str(ROOT/'manual/pdf.css'))
        page.evaluate('''()=>{
          const cover=document.querySelector('.cover');
          cover.querySelector('.meta').innerHTML=cover.querySelector('.meta').innerHTML.replace('HTML版。印刷用スタイルはA4縦。','PDF版 / A4縦。');
          const toc=document.createElement('div');toc.className='pdf-toc';
          toc.innerHTML='<h2>目次</h2><ol></ol>';
          document.querySelectorAll('.toc nav a').forEach(a=>{
            const li=document.createElement('li');const link=document.createElement('a');
            link.href=a.getAttribute('href');
            const label=document.createElement('span');label.textContent=a.textContent.trim();
            const number=document.createElement('span');number.className='toc-page';number.textContent='00';
            link.append(label,number);li.append(link);toc.querySelector('ol').append(li);
          });
          toc.append(cover.querySelector('.note'),cover.querySelector('.caption'));
          cover.after(toc);
          const transfer=document.querySelector('#collect pre');
          const transferNote=document.createElement('p');
          transferNote.textContent='ラズパイからPCへの転送コマンドは、付録A「A.4 収録データをPCへコピーする」を参照してください。';
          transfer.before(transferNote);
          const transferHeading=document.createElement('h3');
          transferHeading.textContent='A.4 収録データをPCへコピーする';
          document.querySelector('#pi').append(transferHeading,transfer);
          const prompt=document.querySelector('#retrain pre:last-of-type');
          const promptIntro=prompt.previousElementSibling;
          const promptHeading=promptIntro.previousElementSibling;
          const promptGroup=document.createElement('div');promptGroup.className='pdf-prompt';
          promptHeading.before(promptGroup);promptGroup.append(promptHeading,promptIntro,prompt);
          document.querySelectorAll('details').forEach(d=>d.open=true);
        }''')
        assert page.locator('main img').evaluate_all('(es)=>es.every(e=>e.complete && e.naturalWidth>0)')
        options=dict(format='A4',prefer_css_page_size=True,print_background=True,
                     display_header_footer=True,header_template='<span></span>',footer_template=footer,
                     tagged=True,outline=True)
        page.pdf(path=str(draft),**options)
        doc=fitz.open(draft)
        # Chromium includes internal destination coordinates. Resolve each TOC link
        # to its printed target instead of guessing based on heading text.
        targets={}
        toc_links=page.locator('.pdf-toc a').evaluate_all('(es)=>es.map(e=>e.getAttribute("href"))')
        printed_toc=doc[1].get_links()
        destination_pages=[link['page']+1 for link in printed_toc if link.get('page',-1)>=0]
        if len(destination_pages)!=len(toc_links):
            raise RuntimeError(f'TOC destination mismatch: {len(destination_pages)} / {len(toc_links)}')
        for key,number in zip(toc_links,destination_pages):targets[key]=number
        doc.close()
        page.evaluate('''(pages)=>document.querySelectorAll('.pdf-toc a').forEach(a=>a.querySelector('.toc-page').textContent=pages[a.getAttribute('href')])''',targets)
        page.pdf(path=str(args.output),**options)
        browser.close()
    final=fitz.open(args.output)
    assert all(abs(p.rect.width-595.28)<1 and abs(p.rect.height-841.89)<1 for p in final)
    assert len(final)==len(fitz.open(draft)),'Pagination changed while numbering TOC'
    print(f'{args.output}: {len(final)} pages; A4 portrait; linked TOC')
    final.close()
    public=ROOT/'manual/solistpan-manual.pdf'
    shutil.copy2(args.output,public)
    print(f'Published-file copy: {public}')

if __name__=='__main__':main()
