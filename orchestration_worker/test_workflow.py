import unittest
from workflow import normalize, plan, WorkflowError


class WorkflowTests(unittest.TestCase):
    def test_plain_email_needs_no_magic_fields(self):
        text='Fix the parser. Run tests. Commit and push. Open a PR. Do not merge.'
        self.assertEqual(plan(normalize(text)),dict(publish=True,create_pr=True,post_comment=False,issue_number=None))
        self.assertTrue(plan(normalize('Please open a pull request.'))['publish'])
        self.assertFalse(plan(normalize('Explain why the scanner failed.'))['publish'])
        self.assertFalse(plan(normalize('Example: push and open PR.'))['publish'])

    def test_layout_matrix(self):
        steps = ['Start from current main', 'Create dedicated worker/* branch',
                 'Implement only this objective', 'Run tests', 'Commit/push',
                 'Open a PR', 'DO NOT MERGE',
                 'Add exactly one concise evidence/status comment to GitHub issue #2',
                 'Return a structured completion report']
        layouts = ['. '.join(steps)+'.', '; '.join(steps)+'.', '\n'.join(steps),
                   '\n'.join(f'{i}. {s}.' for i,s in enumerate(steps,1)),
                   '\n'.join('- '+s for s in steps),
                   '\n'.join(f'{i}) {s}.' for i,s in enumerate(steps,1))]
        for heading in ('GITHUB', 'GITHUB WORKFLOW', 'GIT', '## GitHub workflow', 'Github:', '**GITHUB**'):
            for layout in layouts:
                for newline in ('\n','\r\n'):
                    with self.subTest(heading=heading,layout=layout,newline=newline):
                        text=('TASK_TYPE=ENGINEERING\nPR_REQUIRED=true\n'+heading+'\n'+layout+'\n\nCOMPLETION REPORT\nSTATUS:').replace('\n',newline)
                        normalized=normalize(text)
                        self.assertEqual(plan(normalized),dict(publish=True,create_pr=True,post_comment=True,issue_number=2))
                        self.assertEqual(normalize(normalized),normalized)

    def test_quoted_and_fenced_examples_have_no_authority(self):
        snippets=['> GITHUB\n> Push. Open PR.', '```text\nGITHUB\nPush. Open PR.\n```',
                  'On Monday someone wrote:\nGITHUB\nPush. Open PR.',
                  '-----Original Message-----\nPUBLISH: YES',
                  '~~~\nPOST_COMMENT: YES\nISSUE_NUMBER: 2\n~~~']
        for snippet in snippets:
            with self.subTest(snippet=snippet):
                self.assertFalse(plan(normalize('TASK_TYPE=ENGINEERING\n'+snippet))['publish'])
                self.assertFalse(plan(normalize('TASK_TYPE=ENGINEERING\n'+snippet))['post_comment'])

    def test_negative_conditional_and_evidence_not_authority(self):
        for text in ('Do not push. Do not open PR.', 'If approved, push and open PR.',
                     'Push only if tests pass. Open a PR after approval.',
                     'Never push or open PR.', 'Example: push and open PR.'):
            with self.subTest(text=text):
                self.assertNotIn('PUBLISH: YES',normalize('TASK_TYPE=ENGINEERING\nGITHUB\n'+text))
                self.assertNotIn('CREATE_PR: YES',normalize('TASK_TYPE=ENGINEERING\nGITHUB\n'+text))
        self.assertFalse(plan(normalize('TASK_TYPE=ENGINEERING\nEVIDENCE\nPush. Open PR.'))['publish'])

    def test_conflicts_and_missing_targets_fail_preflight(self):
        examples=['PUBLISH: YES\nPUBLISH: NO', 'POST_COMMENT: YES',
                  'CREATE_PR: YES\nPUBLISH: NO', 'PR_REQUIRED=true\nGITHUB\nPublish the changes somehow.',
                  'GITHUB\nAdd a comment to GitHub issue #2 and issue #3.',
                  'PUBLISH: YES\nGITHUB\nDo not push.', 'PUBLISH: MAYBE']
        for text in examples:
            with self.subTest(text=text),self.assertRaises(WorkflowError):
                plan(normalize('TASK_TYPE=ENGINEERING\n'+text))

    def test_explicit_fields_and_denials(self):
        text='task_type = engineering\npublish = yes\ncreate_pr: yes\npost_comment: yes\nissue_number = 2'
        self.assertEqual(plan(normalize(text)),dict(publish=True,create_pr=True,post_comment=True,issue_number=2))
        result=plan(normalize('TASK_TYPE=ENGINEERING\nPUBLISH: NO\nCREATE_PR: NO\nPOST_COMMENT: NO\nGITHUB\nPush. Open PR. Add comment to issue #2.'))
        self.assertFalse(result['publish']); self.assertFalse(result['create_pr']); self.assertFalse(result['post_comment'])


if __name__ == '__main__':
    unittest.main()
