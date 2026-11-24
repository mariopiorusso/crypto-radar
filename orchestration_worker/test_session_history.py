import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gmail_poller as worker
from session_history import decode_events, lookup_session, save_session, write_history


class SessionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.cfg=dict(state_path=str(root/'worker.db'),repository=str(root/'repo'),
                      report_path=str(root/'reports'))
        self.state=worker.State(self.cfg['state_path']); self.addCleanup(self.state.db.close)
        self.task=dict(gmail_thread_id='thread1',sender='owner@example.com')
        self.sid='11111111-1111-4111-8111-111111111111'

    def test_resume_identity_is_bound_to_sender_thread_and_repository(self):
        save_session(self.cfg,self.task,self.sid,json.dumps({'branch':'worker/example'}))
        self.assertEqual(lookup_session(self.cfg,self.task)['session_id'],self.sid)
        self.assertIsNone(lookup_session(self.cfg,dict(self.task,sender='other@example.com')))
        self.assertIsNone(lookup_session(self.cfg,dict(self.task,gmail_thread_id='thread2')))
        self.assertIsNone(lookup_session(dict(self.cfg,repository='another-repo'),self.task))

    def test_final_message_is_extracted_without_tool_output(self):
        raw='\n'.join(json.dumps(event) for event in [
            {'type':'thread.started','thread_id':self.sid},
            {'type':'item.completed','item':{'type':'command_execution','aggregated_output':'secret'}},
            {'type':'item.completed','item':{'type':'agent_message','text':'{"outcome":"COMPLETED"}'}}])
        final,sid=decode_events(raw)
        self.assertEqual(sid,self.sid); self.assertEqual(json.loads(final)['outcome'],'COMPLETED')
        self.assertNotIn('secret',final)

    def test_history_and_session_are_saved_without_email_replies(self):
        self.state.record('mid','task','2026-11-11T12:00:00+00:00',request_text='Remember this request',
                          gmail_thread_id='thread1',sender='owner@example.com')
        self.state.claim('mid')
        completion={'session_id':self.sid}
        self.state.finish('mid',0,None,completion,queue_reply=False)
        write_history(self.cfg)
        text=(Path(self.cfg['report_path'])/'history.md').read_text(encoding='utf-8')
        self.assertIn('Remember this request',text); self.assertIn(self.sid,text)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM replies').fetchone()[0],0)

    def test_existing_database_migration_preserves_history(self):
        self.state.record('old','legacy','2026-10-01T00:00:00+00:00')
        self.state.db.close()
        reopened=worker.State(self.cfg['state_path'])
        self.state=reopened
        self.addCleanup(reopened.db.close)
        self.assertEqual(reopened.db.execute('SELECT task_id FROM tasks').fetchone()[0],'legacy')
