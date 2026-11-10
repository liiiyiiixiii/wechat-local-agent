"""Extract local document text without running macros, embedded code, or network."""
import json,os,sys,time,zipfile,re,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
from pypdf import PdfReader
import openpyxl

# Failures and OCR-only PDFs used to be re-parsed on every archive refresh. Keep the
# previous result and retry such documents only after this window (seconds).
RETRY_SECONDS=int(os.environ.get('WECHAT_DOC_RETRY_SECONDS','3600'))
# pypdf can split emoji into UTF-16 surrogate pairs that cannot be encoded as UTF-8.
def clean(value):
 return value.encode('utf-8','replace').decode('utf-8')

root=Path(sys.argv[1]);seen={};rows=[];now=time.time()
previous={r['sha256']:r for r in json.loads((root/'document-index.json').read_text(encoding='utf-8'))} if (root/'document-index.json').exists() else {}
for line in (root/'asset-inventory.jsonl').read_text(encoding='utf-8').splitlines():
 entry=json.loads(line);asset=entry.get('original')
 if not asset:continue
 source=entry['source'];ext=Path(source).suffix.lower()
 if ext not in {'.pdf','.docx','.xlsx','.pptx','.odt'}:continue
 key=asset['sha256']
 if key in seen:continue
 old=previous.get(key)
 if old and old.get('status') in ['extracted','ocr_extracted','empty'] and (root/old.get('text_path','MISSING')).is_file():
  seen[key]=old;rows.append(old);continue
 if old and old.get('status') in ['failed','requires_ocr'] and old.get('retry_after',0) > now:
  seen[key]=old;rows.append(old);continue
 row={'source':source,'asset_path':asset['path'],'sha256':key,'format':ext};parts=[]
 try:
  file=root/asset['path']
  if ext=='.pdf':
   reader=PdfReader(file)
   texts=[page.extract_text() or '' for page in reader.pages]
   row['pages']=len(texts)
   for n,page_text in enumerate(texts,1):parts.append('第 '+str(n)+' 页\n'+page_text)
   if not any(t.strip() for t in texts):
    row['status']='requires_ocr'
    ocr=Path(__file__).with_name('pdf_ocr');ocrout=root/'document-text'/(key+'.ocr.txt');ocrout.parent.mkdir(exist_ok=True)
    if sys.platform=='darwin' and ocr.exists():
     # Bound OCR below the daemon's 30-minute media-job timeout; oversized scans retry hourly.
     done=subprocess.run([str(ocr),str(file),str(ocrout)],capture_output=True,text=True,timeout=min(max(60,len(texts)*45),1200))
     if done.returncode==0 and ocrout.exists():
      parts=[clean(ocrout.read_text(encoding='utf-8'))];row['status']='ocr_extracted';row['note']='Local Apple Vision OCR; check the original for recognition errors.'
  elif ext=='.xlsx':
   book=openpyxl.load_workbook(file,read_only=True,data_only=False)
   for sheet in book:
    parts.append('工作表：'+sheet.title)
    for vals in sheet.iter_rows(values_only=True):
     if any(v is not None for v in vals):parts.append('\t'.join('' if v is None else str(v) for v in vals))
   book.close();row['note']='Formulas preserved as formulas; no recalculation or macros executed.'
  else:
   with zipfile.ZipFile(file) as z:
    if ext=='.docx':names=[n for n in z.namelist() if re.fullmatch(r'word/(document|footnotes|endnotes|comments|header\d+|footer\d+)\.xml',n)]
    elif ext=='.pptx':names=sorted([n for n in z.namelist() if re.fullmatch(r'ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml',n)],key=lambda s:int(re.search(r'(\d+)\.xml',s).group(1)))
    else:names=['content.xml']
    for name in names:
     if z.getinfo(name).file_size>100*1024*1024:raise ValueError('XML part too large')
     xml=ET.fromstring(z.read(name));parts.append(name)
     for e in xml.iter():
      tag=e.tag.split('}')[-1]
      if tag in ['p','h']:
       text=''.join(e.itertext()).strip()
       if text:parts.append(text)
  text=clean('\n\n'.join(parts).strip())
  if row.get('status') not in ['requires_ocr','ocr_extracted']:row['status']='extracted' if text else 'empty'
  dest=Path('document-text')/(key+'.txt');(root/dest).parent.mkdir(exist_ok=True);(root/dest).write_text(clean(text+'\n'),encoding='utf-8');row.update(text_path=dest.as_posix(),characters=len(text))
  if row['status'] in ['requires_ocr']:row['retry_after']=now+RETRY_SECONDS
 except Exception as e:
  row.update(status='failed',error=clean(type(e).__name__+': '+str(e)[:180]),retry_after=now+RETRY_SECONDS)
 seen[key]=row;rows.append(row)
temporary=root/'document-index.json.partial'
temporary.write_text(clean(json.dumps(rows,ensure_ascii=False,indent=2)+'\n'),encoding='utf-8')
temporary.replace(root/'document-index.json')
print(json.dumps({'documents':len(rows),'extracted':sum(r['status']=='extracted' for r in rows),'ocr_extracted':sum(r['status']=='ocr_extracted' for r in rows),'requires_ocr':sum(r['status']=='requires_ocr' for r in rows),'failed':sum(r['status']=='failed' for r in rows)}))
