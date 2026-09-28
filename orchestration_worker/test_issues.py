import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,MagicMock,patch
import issue_action as issues


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.cfg={'state_path':str(Path(self.temp.name)/'worker.db'),'publish_remote':'https://github.com/example/repo'}
        self.task={'gmail_message_id':'message1','request_text':'ACTION: CREATE_ISSUE\nTITLE: Example\nISSUE_KEY: test-one\nLABELS: bug\nBODY:\nDescription'}
        self.factory=MagicMock(); self.session=self.factory.return_value.__enter__.return_value
        self.response=self.session.post.return_value
        self.response.status_code=201; self.response.json.return_value={'number':12}

    def test_create_and_persistent_dedup_across_messages(self):
        with patch('socket.socket.connect',side_effect=AssertionError('Offline only')):
            a=issues.create(self.cfg,self.task,self.factory)
            self.task['gmail_message_id']='message2'
            b=issues.create(self.cfg,self.task,self.factory)
        self.assertEqual(a,b); self.assertEqual(a['url'],'https://github.com/example/repo/issues/12')
        self.session.post.assert_called_once()
        self.assertFalse(self.session.post.call_args.kwargs['allow_redirects'])
        self.assertEqual(self.session.post.call_args.kwargs['json']['labels'],['bug'])

    def test_timeout_never_reposts(self):
        self.session.post.side_effect=TimeoutError
        with self.assertRaises(TimeoutError): issues.create(self.cfg,self.task,self.factory)
        with self.assertRaisesRegex(RuntimeError,'uncertain'): issues.create(self.cfg,self.task,self.factory)
        self.session.post.assert_called_once()

    def test_routing_and_validation(self):
        self.assertTrue(issues.requested(self.task['request_text']))
        self.assertFalse(issues.requested('Please explain GitHub issues'))
        with self.assertRaises(ValueError): issues.payload(self.cfg,'TITLE: Missing body')
        with self.assertRaises(ValueError): issues.payload(self.cfg,self.task['request_text'].replace('TITLE: Example','TITLE: One\nTITLE: Two'))
        self.cfg['publish_remote']='https://evil.invalid/repo'
        with self.assertRaises(ValueError): issues.payload(self.cfg,self.task['request_text'])

    def test_roadmap_is_committed_and_links_pinned(self):
        self.cfg['engineering_repository']='unused'
        with patch('issue_action.subprocess.run') as git:
            git.return_value.returncode=0
            git.side_effect=[Mock(returncode=0,stdout='a'*40),Mock(returncode=0,stdout='# Roadmap\n[code](crypto_radar/main.py)')]
            _,data,_=issues.payload(self.cfg,'SOURCE: ROADMAP')
        self.assertIn('/blob/'+('a'*40)+'/crypto_radar/main.py',data['body'])
        self.assertIn('roadmap',data['title'])

    def comment_task(self):
        self.session.get.return_value.status_code=200
        self.session.get.return_value.json.return_value={'number':2,'title':'Roadmap','state':'open'}
        self.response.json.return_value={'id':123}
        return dict(gmail_message_id='comment1', request_text=
            'ACTION: COMMENT_ISSUE\nISSUE_NUMBER: 2\nCOMMENT_KEY: review-one\nBODY:\nReview results')

    def test_comment_lookup_and_persistent_dedup(self):
        task=self.comment_task()
        with patch('socket.socket.connect',side_effect=AssertionError('Offline only')):
            first=issues.comment(self.cfg,task,self.factory)
            task['gmail_message_id']='comment2'
            self.assertEqual(first,issues.comment(self.cfg,task,self.factory))
        self.assertEqual(first['url'],'https://github.com/example/repo/issues/2#issuecomment-123')
        self.session.post.assert_called_once()
        self.assertTrue(self.session.post.call_args.args[0].endswith('/issues/2/comments'))
        self.assertFalse(self.session.get.call_args.kwargs['allow_redirects'])

    def test_comment_timeout_never_reposts(self):
        task=self.comment_task(); self.session.post.side_effect=TimeoutError
        with self.assertRaises(TimeoutError): issues.comment(self.cfg,task,self.factory)
        with self.assertRaisesRegex(RuntimeError,'uncertain'): issues.comment(self.cfg,task,self.factory)
        self.session.post.assert_called_once()

    def test_missing_issue_and_pull_request_never_post(self):
        task=self.comment_task(); self.session.get.return_value.status_code=404
        with self.assertRaises(RuntimeError): issues.comment(self.cfg,task,self.factory)
        self.session.get.return_value.status_code=200
        self.session.get.return_value.json.return_value={'number':2,'title':'PR','pull_request':{}}
        with self.assertRaises(ValueError): issues.comment(self.cfg,task,self.factory)
        self.session.post.assert_not_called()

    def test_comment_fields_reject_invalid_targets(self):
        for text in ('ISSUE_NUMBER: -1', 'ISSUE_NUMBER: https://evil.invalid', 'ISSUE_NUMBER: 2\nISSUE_NUMBER: 3'):
            with self.assertRaises(ValueError): issues.issue_fields(self.cfg,text)
        self.assertTrue(issues.requested('ACTION: LOOKUP_ISSUE\nISSUE_NUMBER: 2'))
        self.assertTrue(issues.requested('ACTION: COMMENT_ISSUE\nISSUE_NUMBER: 2'))

    def test_explicit_roadmap_authorization_normalized(self):
        text='Update the existing Crypto Radar roadmap GitHub issue with a fresh development-status comment.\nIssue URL: https://github.com/example/repo/issues/2'
        self.assertTrue(issues.review_request(self.cfg,text).startswith('POST_COMMENT: YES\nISSUE_NUMBER: 2\n'))
        for other in (text.replace('Update','Do not update'),text.replace('example/repo','other/repo'),
                      text+'\nhttps://github.com/example/repo/issues/3', 'POST_COMMENT: NO\n'+text):
            self.assertEqual(issues.review_request(self.cfg,other),other)

    def test_structured_roadmap_status_request(self):
        text='TASK_ID=ROADMAP-STATUS-005\nTASK_TYPE=ROADMAP_STATUS_UPDATE\nTARGET=github_issue_2\n'
        result=issues.review_request(self.cfg,text)
        self.assertTrue(result.startswith('POST_COMMENT: YES\nISSUE_NUMBER: 2\n'))
        self.assertIn('Do not modify files',result)
        for bad in (text+'TARGET=github_issue_3\n',text+'ISSUE_NUMBER: 3\n',
                    text.replace('github_issue_2','https://evil.invalid/2'),
                    text.replace('ROADMAP_STATUS_UPDATE','UNKNOWN')):
            with self.assertRaises(ValueError): issues.review_request(self.cfg,bad)
