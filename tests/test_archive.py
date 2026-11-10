import unittest,tempfile,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'src'))
from wxlocal.archive import pipeline as exporter
from wxlocal.archive.sqlcipher import DB
class ExportTests(unittest.TestCase):
 def test_plain_snapshot_preserves_sparse_rowids_and_blobs(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/'source.db';dest=root/'plain.db';keys=root/'keys.json';key='47'*32
   keys.write_text(json.dumps({'keys':{'source.db':key}}));keys.chmod(0o600)
   with DB(source,key,fixture_write=True) as db:
    db.query('CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY);INSERT INTO Name2Id(rowid,user_name) VALUES(5,\'alpha\'),(19,\'beta\');CREATE TABLE payload(id INTEGER PRIMARY KEY,svr INTEGER,body BLOB);INSERT INTO payload VALUES(7,9223372036854775806,X\'000102FF00\')')
   before=exporter.digest(source);exporter.snapshot(source,dest,keys)
   self.assertEqual(before,exporter.digest(source))
   with exporter.connect(dest) as db:
    self.assertEqual([tuple(r) for r in db.execute('SELECT rowid,user_name FROM Name2Id ORDER BY rowid').fetchall()],[(5,'alpha'),(19,'beta')])
    self.assertEqual(tuple(db.execute('SELECT * FROM payload').fetchone()),(7,9223372036854775806,b'\x00\x01\x02\xff\x00'))
 def test_xml_and_precision_preservation(self):
  f=exporter.fields('<msg><appmsg><title><![CDATA[a&b.pdf]]></title><type>6</type><appattach><totallen>123</totallen><filemd5>'+('a'*32)+'</filemd5></appattach></appmsg></msg>')
  self.assertEqual(f['file_name'],'a&b.pdf');self.assertEqual(f['file_size'],123);self.assertEqual(f['md5s'],['a'*32])
  self.assertEqual(exporter.rawjson(9223372036854775806),'9223372036854775806')
  self.assertEqual(exporter.rawjson(b'\x00\xff'),{'encoding':'base64','data':'AP8='})
 def test_content_addressed_copy_and_magic_detection(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);f=p/'extensionless';f.write_bytes(b'GIF89a'+b'x'*20)
   result=exporter.put_file(f,p/'out')
   self.assertTrue(result['path'].endswith('.gif'))
   self.assertEqual((p/'out'/result['path']).read_bytes(),f.read_bytes())
   self.assertEqual(exporter.put_file(f,p/'out'),result)
 def test_identical_files_are_not_rewritten_and_surrogates_are_sanitized(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'conversations'/'2026-10.md';exporter.write_if_changed(p,'第一条\n')
   inode=p.stat().st_ino
   exporter.write_if_changed(p,'第一条\n')
   self.assertEqual(p.stat().st_ino,inode)
   exporter.write_if_changed(p,'第二条\n');self.assertEqual(p.read_bytes(),'第二条\n'.encode())
   exporter.dump(p.with_name('index.json'),{'name':'群'})
   self.assertEqual(json.loads(p.with_name('index.json').read_text()),{'name':'群'})
   exporter.write_if_changed(p.with_name('surrogate.txt'),'x\ud83ey')
   self.assertEqual(p.with_name('surrogate.txt').read_bytes(),'x?y'.encode())
 def test_malformed_file_size_and_partial_document_status(self):
  from unittest.mock import patch
  from wxlocal.messages import normalize
  rec=normalize({'local_id':1,'local_type':49,'message_content':'<appmsg><type>6</type><title>x.pdf</title><totallen>bad</totallen></appmsg>'}, 'message/message_0.db','Msg_test','chat',{})
  self.assertEqual(rec['type'],'file')
  self.assertEqual(rec['metadata']['file_size'],0)
  with tempfile.TemporaryDirectory() as t:
   out=Path(t)
   with patch.object(exporter,'documents_python',side_effect=RuntimeError('unavailable')):
    exporter.finalize(out,{'chat':[rec]}, {},[],1,{},publish_latest=False)
   self.assertEqual(json.loads((out/'archive.json').read_text())['status'],'partial_documents')
   self.assertEqual(json.loads((out/'export-report.json').read_text())['documents']['status'],'extraction_failed')
 def test_invalid_compression_preserves_raw(self):
  from wxlocal.messages import normalize
  damaged=b'\x28\xb5\x2f\xfd' + b'bad data'
  result=normalize({'local_id':3,'local_type':1,'message_content':damaged},'db','table','chat',{})
  self.assertIn('invalid_compressed_content',result['metadata']['parse_issues'])
  self.assertEqual(result['raw']['message_content'],exporter.rawjson(damaged))

 def test_supported_office_formats_on_fixture_archive(self):
  import subprocess, zipfile
  import openpyxl
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   files=[]
   for ext,member in [('docx','word/document.xml'),('pptx','ppt/slides/slide1.xml')]:
    source=root/('sample.'+ext)
    with zipfile.ZipFile(source,'w') as archive:
     archive.writestr(member,'<root><p><t>人工文档文字</t></p></root>')
    files.append(source)
   book=openpyxl.Workbook()
   book.active['A1']='人工文档文字'
   source=root/'sample.xlsx'
   book.save(source)
   book.close()
   files.append(source)
   from pypdf import PdfWriter
   pdf=PdfWriter()
   pdf.add_blank_page(width=72,height=72)
   pdf.write(root/'blank.pdf')
   files.append(root/'blank.pdf')
   inventory=[{'source':f.name,'original':exporter.put_file(f,root)} for f in files]
   exporter.lines(root/'asset-inventory.jsonl',inventory)
   subprocess.run([sys.executable,str(exporter.DOCUMENTS_SCRIPT),str(root)],check=True,capture_output=True)
   docs=json.loads((root/'document-index.json').read_text(encoding='utf-8'))
   for doc in docs:
    if doc['format']=='.pdf':
     if sys.platform=='win32':self.assertEqual(doc['status'],'requires_ocr')
    else:
     self.assertEqual(doc['status'],'extracted')
     self.assertIn('人工文档文字',(root/doc['text_path']).read_text(encoding='utf-8'))

if __name__=='__main__':unittest.main()
