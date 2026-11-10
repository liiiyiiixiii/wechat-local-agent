from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wxlocal.archive.sqlcipher import DB
from wxlocal.collector import Collector, Native
from wxlocal.scheduler import MediaTrigger
from wxlocal.store import Store
from wxlocal.util import dumps, sha


def record(local=1, server='100', text='项目预算最终方案', chat='chat-a', ts=1735689600, kind='text'):
    return {'id':'archive-'+str(local), 'session_id':chat, 'sender_id':'sender', 'local_id':local,
            'server_id':server, 'timestamp':ts, 'type':kind, 'content':text, 'text':text,
            'source':{'database':'message/message_0.db','table':'Msg_a'},
            'raw':{'local_id':local,'server_id':server,'message_content':text},'assets':[]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name)
        self.store = Store(self.root/'index.sqlite', 'test-account')

    def tearDown(self):
        self.store.close();self.doCleanups();self.temp.cleanup()

    def add(self, r, gen='generation-one'):
        with self.store.c:
            return self.store.upsert(r, gen)

    def test_chinese_document_search_context_and_pagination(self):
        mids = [self.add(record(i, str(100+i), ts=1735689600+i)) for i in range(5)]
        with self.store.c:
            self.store.contacts({'chat-a':{'nickname':'同名群'},'chat-b':{'nickname':'同名群'}})
        self.assertEqual(len(self.store.find_conversations('同名群')['items']),2)
        self.assertEqual(len(self.store.search_messages('预算')['items']),5)
        p1=self.store.search_messages('最终方案',limit=2)
        p2=self.store.search_messages('最终方案',limit=2,offset=p1['next_offset'])
        self.assertFalse({x['id'] for x in p1['items']} & {x['id'] for x in p2['items']})
        self.assertEqual(len(self.store.get_context(mids[2],before=1,after=1)['items']),3)
        self.assertEqual(self.store.search_messages(start='2026-01-01')['items'],[])
        f=self.root/'file.txt';f.write_text('附件独有的海运排期以及交付方案')
        r=record();r['assets']=[{'path':'file.txt','sha256':sha(f.read_text()),'source':'合同.txt','status':'readable'}]
        r['media_status']='available'
        with self.store.c:self.store.attach(mids[0],r,self.root)
        found=self.store.search_messages('海运排期')['items']
        self.assertEqual(found[0]['id'],mids[0]);aid=found[0]['matches'][0]['attachment_id']
        self.assertIn('海运排期',self.store.read_attachment(aid)['text'])
        f.unlink();self.assertFalse(self.store.read_attachment(aid)['available'])

    def test_private_xml_credentials_not_exposed(self):
        xml='<msg><appmsg><title>项目文件</title><aeskey>PRIVATEKEY</aeskey><cdnattachurl>SECRETURL</cdnattachurl><des>说明</des></appmsg></msg>'
        mid=self.add(record(text=xml,kind='file'))
        out=dumps(self.store.get_context(mid))
        self.assertIn('项目文件',out);self.assertNotIn('PRIVATEKEY',out);self.assertNotIn('SECRETURL',out)
        self.assertEqual(self.store.search_messages('PRIVATEKEY')['items'],[])

    def test_consumers_replay_ack_and_restart(self):
        self.add(record())
        self.assertEqual(self.store.get_changes('fresh')['items'],[])
        self.add(record(2,'101','新的消息'))
        a=self.store.get_changes('fresh');b=self.store.get_changes('other',True)
        self.assertEqual(len(a['items']),1);self.assertEqual(len(b['items']),2)
        self.assertEqual(self.store.get_changes('fresh')['ack_cursor'],a['ack_cursor'])
        with self.assertRaises(ValueError):self.store.ack_changes('fresh',99999)
        self.store.close();self.store=Store(self.root/'index.sqlite')
        self.assertEqual(len(self.store.get_changes('fresh')['items']),1)
        self.store.ack_changes('fresh',a['ack_cursor'])
        self.assertEqual(self.store.get_changes('fresh')['items'],[])
        self.assertEqual(len(self.store.get_changes('other')['items']),2)

    def test_transaction_rollback_does_not_advance_event(self):
        before=self.store.get_status()['latest_change']
        try:
            with self.store.c:
                self.store.upsert(record(),'one');raise RuntimeError('simulated_crash')
        except RuntimeError:pass
        self.assertEqual(self.store.get_status()['latest_change'],before)
        self.assertEqual(self.store.get_status()['messages'],0)

    def test_delayed_attachment_event(self):
        r=record(kind='file');mid=self.add(r)
        self.store.get_changes('files')
        f=self.root/'arrived.txt';f.write_text('稍后下载完成')
        r['assets']=[{'path':'arrived.txt','sha256':sha(f.read_text()),'source':'arrived.txt'}];r['media_status']='available'
        with self.store.c:self.store.attach(mid,r,self.root)
        events=self.store.get_changes('files')['items']
        self.assertEqual(events[0]['kind'],'attachment_updated')
        with self.store.c:self.store.attach(mid,r,self.root)
        self.assertEqual(len(self.store.get_changes('files')['items']),1)
        bad={**r,'assets':[{'path':'../escape','sha256':'bad'}]}
        with self.assertRaises(ValueError):
            with self.store.c:self.store.attach(mid,bad,self.root)

    def test_id_generation_and_history_update(self):
        old=record(server='0');a=self.add(old,'one');b=self.add(old,'two')
        self.assertNotEqual(a,b)
        r=record(server='888');mid=self.add(r,'one');r['text']=r['content']='更新后的方案';r['raw']['message_content']=r['text']
        mid2=self.add(r,'two');self.assertEqual(mid,mid2)
        self.assertEqual(self.store.get_context(mid,before=0,after=0)['items'][0]['version'],2)

    def test_validation_and_sql_literal_queries(self):
        self.add(record(text='含引号 " 和百分号 % 的消息'))
        self.assertEqual(len(self.store.search_messages('%')['items']),1)
        self.assertEqual(self.store.search_messages("' OR 1=1 --")['items'],[])
        with self.assertRaises(ValueError):self.store.search_messages(limit=201)
        with self.assertRaises(ValueError):self.store.get_context('missing')
        with self.assertRaises(ValueError):self.store.call('execute_sql',{})

    def test_long_message_chunks_and_same_second_order(self):
        mids=[]
        for i in range(3):
            r=record(i,str(333+i),text='长文本'*10000 if i==1 else str(i))
            r['raw']['sort_seq']=i+100
            mids.append(self.add(r))
        result=self.store.get_context(mids[1],before=1,after=1)
        self.assertEqual([m['id'] for m in result['items']],mids)
        first=self.store.get_context(mids[1],before=0,after=0)['items'][0]
        rest=self.store.get_context(mids[1],before=0,after=0,text_offset=first['text_next_offset'])['items'][0]
        self.assertEqual(first['text']+rest['text'],'长文本'*10000)

    def test_cross_month_and_late_ocr_update(self):
        a=self.add(record(1,'20',ts=1735689600));b=self.add(record(2,'21',ts=1738368000))
        self.assertEqual([x['id'] for x in self.store.search_messages(start='2025-02-01',end='2025-03-01')['items']],[b])
        (self.root/'scan.pdf').write_bytes(b'%PDF-fixture')
        r=record();r['assets']=[{'path':'scan.pdf','sha256':'fixturepdf','source':'scan.pdf'}];r['media_status']='available'
        with self.store.c:self.store.attach(a,r,self.root)
        self.store.get_changes('ocr')
        (self.root/'ocr.txt').write_text('迟到的识别文字')
        r['assets'][0].update(text_path='ocr.txt',text_extraction_status='ocr_extracted')
        with self.store.c:self.store.attach(a,r,self.root)
        self.assertEqual(self.store.get_changes('ocr')['items'][0]['kind'],'attachment_updated')
        self.assertEqual(self.store.search_messages('识别文字')['items'][0]['id'],a)

    def test_attachment_order_does_not_create_false_changes(self):
        mid=self.add(record(kind='image'));r=record(kind='image');r['media_status']='available'
        for name in ('a.png','b.png'):
            (self.root/name).write_bytes(name.encode())
            r['assets'].append({'path':name,'sha256':sha(name),'source':name})
        with self.store.c:self.store.attach(mid,r,self.root)
        cursor=self.store.get_status()['latest_change']
        r['assets'].reverse()
        with self.store.c:self.store.attach(mid,r,self.root)
        self.assertEqual(self.store.get_status()['latest_change'],cursor)

    def test_media_trigger_debounces_and_forces_on_long_churn(self):
        t=MediaTrigger(settle=20,max_wait=180)
        self.assertIsNone(t.observe('committed','a',0))
        self.assertIsNone(t.observe('committed','a',10))
        self.assertEqual(t.observe('committed','a',20),'a')
        self.assertIsNone(t.pending)
        t=MediaTrigger(settle=20,max_wait=180)
        for i in range(18):
            self.assertIsNone(t.observe('committed','sig-%d'%i,i*10))
        self.assertEqual(t.observe('committed','sig-18',180),'sig-18')
        t=MediaTrigger(settle=20,max_wait=180)
        self.assertIsNone(t.observe('committed','a',0))
        self.assertIsNone(t.observe('committed','committed',5))
        self.assertIsNone(t.pending)

    def test_media_job_queue_coalesces(self):
        with self.store.c:
            self.store.c.execute("INSERT INTO jobs(id,state,requested,completed) VALUES('media','idle',3,3)")
            self.store.queue_media()
        row=self.store.c.execute("SELECT * FROM jobs WHERE id='media'").fetchone()
        self.assertEqual((row['requested'],row['state']),(4,'pending'))
        with self.store.c:self.store.queue_media()
        row=self.store.c.execute("SELECT * FROM jobs WHERE id='media'").fetchone()
        self.assertEqual(row['requested'],4)
        with self.store.c:
            self.store.c.execute("UPDATE jobs SET state='running',requested=4,completed=3 WHERE id='media'")
            self.store.queue_media();self.store.queue_media()
        row=self.store.c.execute("SELECT * FROM jobs WHERE id='media'").fetchone()
        self.assertEqual((row['requested'],row['completed'],row['state']),(5,3,'running'))
        with self.store.c:self.store.queue_media()
        row=self.store.c.execute("SELECT * FROM jobs WHERE id='media'").fetchone()
        self.assertEqual(row['requested'],5)

    def test_text_changes_do_not_queue_media_but_attachments_do(self):
        account,path,c,table=self.source_fixture()
        collector=Collector(self.root,account,fixture=True)
        self.assertEqual(collector.sync_once()['errors'],[])
        self.assertIsNone(self.store.c.execute("SELECT 1 FROM jobs WHERE id='media'").fetchone())
        c.execute('UPDATE '+table+' SET local_type=3 WHERE local_id=1');c.commit()
        collector.sync_once(force=True)
        row=self.store.c.execute("SELECT requested,completed FROM jobs WHERE id='media'").fetchone()
        self.assertEqual((row['requested'],row['completed']),(1,0))
        c.close()

    def test_append_with_historic_media_and_interrupted_reconcile(self):
        account,path,writer,table = self.source_fixture()
        self.addCleanup(writer.close)
        writer.execute('UPDATE '+table+' SET local_type=3 WHERE local_id=1')
        writer.executemany('INSERT INTO '+table+' VALUES(?,?,1,1735689600,1,?,NULL)',
                           ((n,n+1000,'history') for n in range(2,700)))
        writer.commit()
        collector = Collector(self.root,account,fixture=True)
        original = Store.upsert
        def interrupt(store,record,*args,**kwargs):
            if record['local_id']==300:
                raise RuntimeError('interrupted')
            return original(store,record,*args,**kwargs)
        with patch.object(Store,'upsert',interrupt):
            self.assertTrue(collector.sync_once()['errors'])
        self.assertEqual(self.store.get_status()['messages'],256)
        self.assertFalse(collector.sync_once()['errors'])
        self.assertEqual(self.store.get_status()['latest_change'],699)
        with self.store.c:
            self.store.c.execute("UPDATE jobs SET completed=requested,state='idle'")
        writer.execute('INSERT INTO '+table+' VALUES(700,1700,1,1735689600,1,?,NULL)',('new text',))
        writer.commit()
        result=collector.sync_once()
        self.assertEqual(result['rows_scanned'],33)
        self.assertEqual(result['changed'],1)
        self.assertEqual(self.store.get_status()['media_job']['state'],'idle')
        writer.execute('UPDATE '+table+' SET message_content=? WHERE local_id=50',('history edit',))
        writer.commit()
        self.assertFalse(collector.sync_once(force=True)['errors'])
        self.assertEqual(len(self.store.search_messages('history edit')['items']),1)

    def test_concurrent_ack_is_monotonic(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        for n in range(1,4):
            self.add(record(n,str(n)))
        for trial in range(8):
            consumer='parallel-'+str(trial)
            self.store.get_changes(consumer,True)
            barrier=threading.Barrier(2)
            def confirm(cursor):
                local=Store(self.root/'index.sqlite')
                try:
                    barrier.wait(timeout=5)
                    try:
                        local.ack_changes(consumer,cursor)
                    except ValueError:
                        if cursor!=1:
                            raise
                finally:
                    local.close()
            with ThreadPoolExecutor(2) as pool:
                list(pool.map(confirm,(1,3)))
            self.assertEqual(self.store.get_changes(consumer)['confirmed_cursor'],3)

    def test_old_progress_and_confirmed_cursor_survive_upgrade(self):
        account,path,writer,table=self.source_fixture()
        self.addCleanup(writer.close)
        collector=Collector(self.root,account,fixture=True)
        collector.sync_once()
        mid=self.store.search_messages('第一条')['items'][0]['id']
        batch=self.store.get_changes('old-workflow',True)
        self.store.ack_changes('old-workflow',batch['ack_cursor'])
        # v1 stored per-table max_local_id and count, before rowid checkpoints were introduced.
        import json
        with self.store.c:
            self.store.c.execute('UPDATE sources SET progress=?',(json.dumps({table:{'max_local_id':1,'rows':1}}),))
        restarted=Collector(self.root,account,fixture=True)
        self.assertFalse(restarted.sync_once()['errors'])
        self.assertEqual(self.store.search_messages('第一条')['items'][0]['id'],mid)
        result=self.store.get_changes('old-workflow')
        self.assertEqual(result['confirmed_cursor'],batch['ack_cursor'])
        self.assertEqual(result['items'],[])

    def source_fixture(self):
        account=self.root/'test-account';directory=account/'db_storage/message';directory.mkdir(parents=True)
        path=directory/'message_0.db';c=sqlite3.connect(path)
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA wal_autocheckpoint=0')
        c.execute('CREATE TABLE Name2Id(user_name TEXT)');c.execute("INSERT INTO Name2Id VALUES('chat-a')")
        import hashlib
        table='Msg_'+hashlib.md5(b'chat-a').hexdigest()
        c.execute('CREATE TABLE '+table+'(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB,compress_content BLOB)')
        c.execute('INSERT INTO '+table+' VALUES(1,100,1,1735689600,1,?,NULL)',('第一条'.encode(),));c.commit()
        return account,path,c,table

    def test_live_wal_new_chat_history_edit_and_source_replacement(self):
        account,path,c,table=self.source_fixture()
        collector=Collector(self.root,account,fixture=True)
        self.assertEqual(collector.sync_once()['errors'],[])
        self.assertEqual(self.store.get_status()['messages'],1)
        c.execute('UPDATE '+table+' SET message_content=? WHERE local_id=1',('修改历史'.encode(),))
        c.execute('INSERT INTO '+table+' VALUES(2,101,1,1735689601,1,?,NULL)',('第二条'.encode(),));c.commit()
        start=time.time();result=collector.sync_once()
        self.assertLess(time.time()-start,10);self.assertEqual(result['errors'],[])
        self.assertEqual(len(self.store.search_messages('修改历史')['items']),1)
        import hashlib
        t2='Msg_'+hashlib.md5(b'new-chat').hexdigest();c.execute("INSERT INTO Name2Id VALUES('new-chat')")
        c.execute('CREATE TABLE '+t2+' AS SELECT * FROM '+table+' WHERE local_id=2');c.commit()
        collector.sync_once();self.assertEqual(self.store.get_status()['conversations_with_messages'],2)
        c.execute('DELETE FROM '+table+' WHERE local_id=1');c.commit();collector.sync_once()
        item=self.store.search_messages('修改历史')['items'][0]
        self.assertFalse(item['present_in_last_source_scan'])
        c.close()
        replacement=path.with_name('replacement.db');src=sqlite3.connect(path);dst=sqlite3.connect(replacement);src.backup(dst);src.close();dst.close();replacement.replace(path)
        collector.sync_once()
        self.assertTrue(self.store.c.execute("SELECT 1 FROM changes WHERE kind='source_replaced'").fetchone())

    def test_key_failure_explicit_and_recovers(self):
        account,path,c,table=self.source_fixture();collector=Collector(self.root,account,fixture=True)
        collector.sync_once()
        with patch.object(collector,'open',side_effect=RuntimeError('key_failure')):
            result=collector.sync_once(force=True)
        self.assertTrue(result['errors']);self.assertEqual(self.store.get_status()['sync']['state'],'degraded')
        self.assertEqual(self.store.get_status()['messages'],1)
        self.assertEqual(collector.sync_once()['errors'],[]);c.close()

    def test_native_readonly_preserves_blob_and_integer(self):
        path=self.root/'encrypted.db';key='12'*32
        with DB(path,key,fixture_write=True) as w:
            w.query('CREATE TABLE sample(n INTEGER,b BLOB,t TEXT); INSERT INTO sample VALUES(9223372036854775806,x\'00ff0010\',\'fixture\');')
        before=path.read_bytes();r=Native(path,key)
        try:
            row=r.query('SELECT * FROM sample')[0]
            self.assertEqual(row['n'],9223372036854775806);self.assertEqual(row['b'],b'\x00\xff\x00\x10')
            with self.assertRaises(RuntimeError):r.query('DELETE FROM sample')
        finally:r.close()
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main(verbosity=2)
