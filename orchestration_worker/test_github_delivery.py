import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import github_delivery as delivery

REQUEST='''TASK_TYPE=ENGINEERING
GIT
Work on a worker/* branch, commit, push, and create a PR for owner review. DO NOT merge the PR.

ROADMAP
Add one concise status comment to GitHub issue #2 with tests and limitations.

SAFETY
Do not change credentials.
'''


class DeliveryTests(unittest.TestCase):
    def test_explicit_sections_normalize(self):
        text=delivery.normalize(REQUEST)
        for field in ('PUBLISH: YES','CREATE_PR: YES','POST_COMMENT: YES','ISSUE_NUMBER: 2'):
            self.assertIn(field,text)
        self.assertEqual(delivery.normalize(text),text)

    def test_negation_explicit_no_and_conditional_not_inferred(self):
        self.assertNotIn('PUBLISH: YES',delivery.normalize(REQUEST.replace('commit, push,','commit, do not push,')))
        self.assertNotIn('PUBLISH: YES',delivery.normalize('PUBLISH: NO\n'+REQUEST))
        self.assertNotIn('PUBLISH: YES',delivery.normalize(REQUEST.replace('Work on','If approved, work on')))
        self.assertNotIn('PUBLISH: YES',delivery.normalize(REQUEST.replace('GIT\n','EVIDENCE\n')))
        with self.assertRaises(ValueError): delivery.normalize('ISSUE_NUMBER: 3\n'+REQUEST)

    def test_delivery_order_and_links(self):
        cfg={'publish_remote':'https://github.com/example/repo'}
        report={'branch':'worker/test','head':'a'*40,'summary':'Results','github_access_status':'','outcome':'COMPLETED'}
        calls=[]
        with patch('publishing.publish',side_effect=lambda *a:calls.append('push') or 'commit-url'), \
             patch('github_delivery.pull_request',side_effect=lambda *a:calls.append('pr') or 'pr-url'), \
             patch('issue_action.comment',side_effect=lambda *a:calls.append('comment') or {'url':'comment-url'}):
            delivery.deliver(cfg,{'gmail_message_id':'m','request_text':REQUEST},report)
        self.assertEqual(calls,['push','pr','comment'])
        self.assertEqual(report['outcome'],'COMPLETED')
        self.assertIn('pr-url',report['summary'])

    def test_pr_creation_dedup_and_uncertain_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg={'publish_remote':'https://github.com/example/repo','state_path':str(Path(temp)/'worker.db')}
            factory=MagicMock(); session=factory.return_value.__enter__.return_value
            session.get.return_value.json.side_effect=[[],{'default_branch':'main'}]
            session.post.return_value.status_code=201;session.post.return_value.json.return_value={'number':7}
            url=delivery.pull_request(cfg,'worker/test','a'*40,'Summary',factory)
            self.assertEqual(url,'https://github.com/example/repo/pull/7')
            self.assertEqual(delivery.pull_request(cfg,'worker/test','a'*40,'Summary',factory),url)
            session.post.assert_called_once()
            session.get.return_value.json.side_effect=[[],{'default_branch':'main'}]
            session.post.side_effect=TimeoutError
            with self.assertRaises(TimeoutError): delivery.pull_request(cfg,'worker/other','b'*40,'Summary',factory)
            with self.assertRaisesRegex(RuntimeError,'uncertain'): delivery.pull_request(cfg,'worker/other','b'*40,'Summary',factory)
            self.assertEqual(session.post.call_count,2)
