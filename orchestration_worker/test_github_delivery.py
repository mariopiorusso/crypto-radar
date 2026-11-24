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
    def test_semicolon_github_email(self):
        request='''TASK_TYPE=ENGINEERING
PR_REQUIRED=true
GITHUB
Start from current main; create dedicated worker/* branch; implement only this objective; run tests; commit/push; open PR; DO NOT MERGE; add exactly one concise status/evidence comment to GitHub issue #2; return structured completion report.

COMPLETION REPORT
STATUS:
'''
        for field in ('PUBLISH: YES','CREATE_PR: YES','POST_COMMENT: YES','ISSUE_NUMBER: 2'):
            self.assertIn(field,delivery.normalize(request))
        self.assertNotIn('PUBLISH: YES',delivery.normalize(request.replace('commit/push','do not push')))
        self.assertNotIn('PUBLISH: YES',delivery.normalize(request.replace('commit/push','if approved, push')))
        self.assertNotIn('POST_COMMENT: YES',delivery.normalize(request.replace('add exactly','if approved, add exactly')))

    def test_numbered_email_workflow(self):
        request='''TASK_TYPE=ENGINEERING
PR_REQUIRED=true
AUTO_MERGE=false
GITHUB WORKFLOW
1. Start from current main.
2. Create dedicated worker/* branch.
3. Implement only this objective.
4. Run complete relevant test suite.
5. Commit and push branch.
6. Open PR.
7. DO NOT MERGE.
8. Add exactly one concise evidence/status comment to GitHub issue #2.
9. Return structured completion report.
'''
        normalized=delivery.normalize(request)
        for field in ('PUBLISH: YES','CREATE_PR: YES','POST_COMMENT: YES','ISSUE_NUMBER: 2'):
            self.assertIn(field,normalized)
        self.assertEqual(delivery.normalize(normalized),normalized)
        for field in ('PUBLISH','CREATE_PR','POST_COMMENT'):
            self.assertNotIn(field+': YES',delivery.normalize(field+': NO\n'+request))
        self.assertNotIn('CREATE_PR: YES',delivery.normalize(request.replace('6. Open PR.', '6. If tests pass, open PR.')))
        self.assertNotIn('PUBLISH: YES',delivery.normalize(request.replace('GITHUB WORKFLOW','EVIDENCE')))
        self.assertNotIn('POST_COMMENT: YES',delivery.normalize(request.replace('8. Add exactly', '8. Do not post a comment. Add exactly')))

    def test_blocked_work_delivers_status_only(self):
        report={'branch':'worker/test','head':'a'*40,'summary':'Provider licensing unverified',
                'github_access_status':'','outcome':'BLOCKED'}
        with patch('publishing.publish') as push, patch('github_delivery.pull_request') as pr, \
             patch('issue_action.comment',return_value={'url':'comment-url'}) as comment:
            delivery.deliver({'publish_remote':'https://github.com/example/repo'},
                             {'gmail_message_id':'m','request_text':REQUEST},report)
        push.assert_not_called(); pr.assert_not_called(); comment.assert_called_once()
        self.assertEqual(report['outcome'],'BLOCKED')
        self.assertIn('Implementation outcome: BLOCKED',comment.call_args.args[1]['request_text'])
        self.assertIn('comment-url',report['github_access_status'])

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

    def test_publish_failure_does_not_suppress_status_comment(self):
        report={'branch':'worker/test','head':'a'*40,'summary':'Local tests passed',
                'github_access_status':'','outcome':'COMPLETED'}
        with patch('publishing.publish',side_effect=TimeoutError), patch('github_delivery.pull_request') as pr, \
             patch('issue_action.comment',return_value={'url':'comment-url'}) as comment:
            delivery.deliver({'publish_remote':'https://github.com/example/repo'},
                             {'gmail_message_id':'m','request_text':REQUEST},report)
        pr.assert_not_called(); comment.assert_called_once()
        self.assertEqual(report['implementation_outcome'],'COMPLETED')
        self.assertEqual(report['outcome'],'BLOCKED')
        self.assertEqual(report['delivery']['publish']['status'],'FAILED_OR_UNCERTAIN')
        self.assertEqual(report['delivery']['create_pr']['status'],'SKIPPED_DEPENDENCY')
        self.assertEqual(report['delivery']['post_comment']['status'],'DELIVERED')

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
