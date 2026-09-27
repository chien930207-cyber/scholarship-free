#!/usr/bin/env python3
"""Bounded, offline attachment text extraction. Never approves scholarship rules.

Runs as an isolated subprocess. Binary data never executes, macros are ignored,
scanned PDFs require visual review, and table interpretation remains unverified.
"""
from __future__ import annotations
import io
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_UNCOMPRESSED=12_000_000
MAX_TEXT=160_000

def extract(body:bytes, suffix:str)->dict:
    if len(body)>8_000_000: raise ValueError('Attachment exceeds limit')
    if body.startswith(b'%PDF-'):
        try:
            from pypdf import PdfReader
        except ImportError:
            return {'state':'manual_review','text':'','note':'PDF parser not installed; bytes are still monitored.'}
        reader=PdfReader(io.BytesIO(body),strict=False)
        if reader.is_encrypted:
            return {'state':'manual_review','text':'','note':'Encrypted PDF requires review.'}
        if len(reader.pages)>60:
            return {'state':'manual_review','text':'','note':'PDF page limit exceeded.'}
        parts=[];partial=False
        for page in reader.pages:
            t=page.extract_text() or '';parts.append(t)
            if len(t.strip())<30 and len(page.images):partial=True
        full='\n'.join(parts);text=full[:MAX_TEXT];partial=partial or len(full)>MAX_TEXT
        state='visual_review' if len(text.strip())<30 else 'partial_text' if partial else 'text_extracted'
        return {'state':state,'text':text,'note':'Text is machine extracted; blank/image-only pages or truncation remain unverified.'}
    if suffix=='.docx' and body[:2]==b'PK':
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            infos=z.infolist()
            if len(infos)>512 or sum(i.file_size for i in infos)>MAX_UNCOMPRESSED:
                raise ValueError('Expanded archive limit exceeded')
            if 'word/document.xml' not in z.namelist(): raise ValueError('Missing DOCX document XML')
            raw=z.read('word/document.xml')
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('XML entities denied')
            root=ET.fromstring(raw)
            full='\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'));text=full[:MAX_TEXT]
            images=any(n.tag.endswith(('}drawing','}pict')) for n in root.iter())
            return {'state':'partial_text' if images or len(full)>MAX_TEXT else 'text_extracted','text':text,'note':'DOCX embedded images are not read; layout remains machine interpreted.'}
    return {'state':'manual_review','text':'','note':'Binary attachment fingerprinted; this format is not parsed.'}

if __name__=='__main__':
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU,(10,10))
        except (ImportError,ValueError): pass
        body=Path(sys.argv[1]).read_bytes()
        print(json.dumps(extract(body,sys.argv[2].lower()),ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'state':'manual_review','text':'','note':type(e).__name__+': '+str(e)[:180]}))
