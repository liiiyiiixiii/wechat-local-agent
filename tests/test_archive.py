import unittest,tempfile,json,os,sqlite3,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'src'))
from wxlocal.archive import pipeline as exporter
from wxlocal.archive.sqlcipher import DB
class ExportTests(unittest.TestCase):
 def test_plain_snapshot_preserves_sparse_rowids_and_blobs(self):
  with tempfile.TemporaryDirectory(dir=exporter.ROOT/'private') as td:
   root=Path(td);source=root/'source.db';dest=root/'plain.db';keys=root/'keys.json';key='47'*32
   keys.write_text(json.dumps({'keys':{'source.db':key}}));keys.chmod(0o600)
   with DB(source,key,fixture_write=True) as db:
    db.query('CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY);INSERT INTO Name2Id(rowid,user_name) VALUES(5,\'alpha\'),(19,\'beta\');CREATE TABLE payload(id INTEGER PRIMARY KEY,svr INTEGER,body BLOB);INSERT INTO payload VALUES(7,9223372036854775806,X\'000102FF00\')')
   before=exporter.digest(source);exporter.snapshot(source,dest,keys)
   self.assertEqual(before,exporter.digest(source))
   with sqlite3.connect(dest) as db:
    self.assertEqual(db.execute('SELECT rowid,user_name FROM Name2Id ORDER BY rowid').fetchall(),[(5,'alpha'),(19,'beta')])
    self.assertEqual(db.execute('SELECT * FROM payload').fetchone(),(7,9223372036854775806,b'\x00\x01\x02\xff\x00'))
 def test_xml_and_precision_preservation(self):
  f=exporter.fields('<msg><appmsg><title><![CDATA[a&b.pdf]]></title><type>6</type><appattach><totallen>123</totallen><filemd5>'+('a'*32)+'</filemd5></appattach></appmsg></msg>')
  self.assertEqual(f['file_name'],'a&b.pdf');self.assertEqual(f['file_size'],123);self.assertEqual(f['md5s'],['a'*32])
  self.assertEqual(exporter.rawjson(9223372036854775806),'9223372036854775806')
  self.assertEqual(exporter.rawjson(b'\x00\xff'),{'encoding':'base64','data':'AP8='})
 def test_content_addressed_copy_and_magic_detection(self):
  with tempfile.TemporaryDirectory(dir=exporter.ROOT/'private') as td:
   p=Path(td);f=p/'extensionless';f.write_bytes(b'GIF89a'+b'x'*20)
   result=exporter.put_file(f,p/'out')
   self.assertTrue(result['path'].endswith('.gif'))
   self.assertEqual((p/'out'/result['path']).read_bytes(),f.read_bytes())
   self.assertEqual(exporter.put_file(f,p/'out'),result)
 def test_identical_files_are_not_rewritten_and_surrogates_are_sanitized(self):
  with tempfile.TemporaryDirectory(dir=exporter.ROOT/'private') as td:
   p=Path(td)/'conversations'/'2026-10.md';exporter.write_if_changed(p,'第一条\n')
   inode=p.stat().st_ino
   exporter.write_if_changed(p,'第一条\n')
   self.assertEqual(p.stat().st_ino,inode)
   exporter.write_if_changed(p,'第二条\n');self.assertEqual(p.read_bytes(),'第二条\n'.encode())
   exporter.dump(p.with_name('index.json'),{'name':'群'})
   self.assertEqual(json.loads(p.with_name('index.json').read_text()),{'name':'群'})
   exporter.write_if_changed(p.with_name('surrogate.txt'),'x\ud83ey')
   self.assertEqual(p.with_name('surrogate.txt').read_bytes(),'x?y'.encode())
if __name__=='__main__':unittest.main()
