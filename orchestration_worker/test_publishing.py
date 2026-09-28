import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import publishing


class PublishingTests(unittest.TestCase):
    def test_rejects_main_and_unpinned_destinations(self):
        with self.assertRaises(ValueError): publishing.publish({},'main','a'*40)
        with self.assertRaises(ValueError): publishing.publish({'publish_remote':'https://evil.invalid/repo'},'worker/test','a'*40)

    def test_exact_sha_push_verified_and_helpers_pinned(self):
        parent=Path(publishing.__file__).parent/'workspaces'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            root=Path(temp); (root/'.git').mkdir()
            config=root/'.git/config'
            config.write_text('[core]\n bare = false\n[remote "origin"]\n url = https://github.com/example/repo\n')
            cfg={'engineering_repository':str(root),'publish_remote':'https://github.com/example/repo'}
            sha='a'*40
            with patch('publishing.subprocess.run') as run:
                run.return_value.returncode=0
                run.return_value.stdout=sha
                url=publishing.publish(cfg,'worker/test',sha)
                self.assertTrue(url.endswith('/commit/'+sha))
                push=run.call_args_list[1]
                self.assertIn(sha+':refs/heads/worker/test',push.args[0])
                self.assertIn('credential.helper=manager',push.args[0])
                self.assertEqual(push.kwargs['env']['GIT_CONFIG_NOSYSTEM'],'1')
                self.assertNotIn('--force',push.args[0])
            config.write_text('[include]\n path = malicious.config\n')
            with patch('publishing.subprocess.run') as run:
                with self.assertRaises(ValueError): publishing.publish(cfg,'worker/test',sha)
                run.assert_not_called()
