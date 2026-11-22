#!/usr/bin/env python3
"""Extract document text; no implicit installation or OCR."""
import argparse, os, subprocess, tempfile
from pathlib import Path

def extract(path):
    suffix=path.suffix.lower()
    if suffix=='.docx':
        try:
            from docx import Document
            from docx.text.paragraph import Paragraph
            from docx.table import Table
        except ImportError: raise ValueError('DOCX requires python-docx')
        doc=Document(path);parts=[]
        for child in doc.element.body.iterchildren():
            if child.tag.endswith('}p'): parts.append(Paragraph(child,doc).text)
            elif child.tag.endswith('}tbl'):
                parts.extend('\t'.join(c.text for c in row.cells) for row in Table(child,doc).rows)
        return '\n'.join(parts)
    if suffix=='.pdf':
        try: from pypdf import PdfReader
        except ImportError: raise ValueError('PDF requires pypdf')
        pages=[page.extract_text() or '' for page in PdfReader(path).pages]
        if any(not s.strip() for s in pages):
            raise ValueError('One or more PDF pages have no text; inspect/OCR these pages before conversion')
        return '\n\f\n'.join(pages)
    if suffix=='.doc':
        return subprocess.run(['antiword',str(path)],check=True,capture_output=True).stdout.decode('utf-8')
    raise ValueError('Supported extensions: .docx .pdf .doc')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path)
    p.add_argument('--output',type=Path);p.add_argument('--overwrite',action='store_true');a=p.parse_args()
    out=a.output or a.input.with_suffix('.txt')
    if out.resolve()==a.input.resolve():p.error('Output must differ from input')
    if out.exists() and not a.overwrite:p.error('Output exists; use --overwrite only for intentional replacement')
    try: text=extract(a.input)
    except (ValueError,FileNotFoundError,subprocess.CalledProcessError) as e:p.exit(1,str(e)+'\n')
    if not text.strip():p.error('No text extracted; inspect document or use OCR')
    out.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=out.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(text+'\n')
        os.replace(tmp,out)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    print(out)
if __name__=='__main__':main()
