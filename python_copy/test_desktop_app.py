import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import desktop_app
import run_task
import test_publishing


class DesktopLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_path = Path(self.temp.name)
        for name in ('desktop_app.ROOT', 'run_task.ROOT'):
            patcher = patch(name, self.root_path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = desktop_app.App.__new__(desktop_app.App)
        self.app.root = MagicMock()
        self.app.status = MagicMock()
        self.app.process = None
        self.task = test_publishing.PublishingTests().task()
        url = MagicMock()
        url.get.return_value = self.task['url']
        self.app.vars = {'url': url}
        self.app.settings = MagicMock(return_value=self.task['settings'])
        self.app.save_last_form = MagicMock(return_value=True)
        self.app.save_timer = None

    def test_raw_form_roundtrip_preserves_unfinished_input(self):
        self.app.vars['url'].get.return_value = 'https://item.taobao.com/item.htm?id=123'
        for name, value in [('attrs', '{"材质":'), ('desc', '第一段\n第二段'), ('advanced', '{')]:
            widget = MagicMock()
            widget.get.return_value = value
            setattr(self.app, name, widget)
        self.assertTrue(desktop_app.App.save_last_form(self.app))
        data = json.loads((self.root_path / 'output' / 'desktop-last-form.json').read_text(encoding='utf-8'))
        self.assertEqual(data['attrs'], '{"材质":')
        self.assertEqual(data['desc'], '第一段\n第二段')
        self.app.restore_last_form()
        self.app.attrs.insert.assert_called_once_with('1.0', '{"材质":')
        self.app.vars['url'].set.assert_called_once_with(data['fields']['url'])

    def test_upgrade_restores_previous_task(self):
        directory = self.root_path / 'output' / 'task-configs'
        directory.mkdir(parents=True)
        (directory / 'last.json').write_text(json.dumps(self.task), encoding='utf-8')
        for name in ('attrs', 'desc', 'advanced'):
            setattr(self.app, name, MagicMock())
        self.app.restore_last_form()
        self.app.vars['url'].set.assert_called_once_with(self.task['url'])

    def test_form_saved_before_invalid_task_is_rejected(self):
        self.app.settings.side_effect = ValueError('JSON尚未填写完整')
        with patch('desktop_app.messagebox.showerror'):
            self.app.start('publish')
        self.app.save_last_form.assert_called_once()

    def test_close_saves_before_destroying_window(self):
        self.app.close()
        self.app.save_last_form.assert_called_once()
        self.app.root.destroy.assert_called_once()

    def test_reference_shop_link_is_preserved_in_task_recovery(self):
        self.task['settings']['target_shop_reference_url'] = 'https://shop123.taobao.com/'
        data = desktop_app.task_to_form(self.task)
        self.assertEqual(data['fields']['target_shop_reference_url'], 'https://shop123.taobao.com/')

    def test_old_task_without_reference_shop_link_is_supported(self):
        data = desktop_app.task_to_form(self.task)
        self.assertNotIn('target_shop_reference_url', data['fields'])

    def test_publish_can_restart_with_existing_unknown_submission(self):
        journal = run_task.task_directory(self.task) / 'submission.json'
        journal.parent.mkdir(parents=True)
        journal.write_text('{"status":"unknown"}', encoding='utf-8')
        with patch('desktop_app.subprocess.Popen') as launch, patch('desktop_app.messagebox.showerror') as dialog:
            self.app.start('publish')
        launch.assert_called_once()
        dialog.assert_not_called()
        self.assertIn('已启动', self.app.status.set.call_args.args[0])
        self.assertEqual(journal.read_text(encoding='utf-8'), '{"status":"unknown"}')

    def test_draft_still_starts_with_prior_submission(self):
        journal = run_task.submission_journal(self.task)
        journal.parent.mkdir(parents=True)
        journal.write_text('{}', encoding='utf-8')
        with patch('desktop_app.subprocess.Popen') as launch:
            self.app.start('draft')
        launch.assert_called_once()
        self.app.root.after.assert_called_once()

    def test_new_product_can_start_publish(self):
        with patch('desktop_app.subprocess.Popen') as launch:
            self.app.start('publish')
        launch.assert_called_once()
        self.assertIn('已启动', self.app.status.set.call_args.args[0])

    def test_early_process_exit_updates_status(self):
        self.app.process = MagicMock()
        self.app.process.poll.return_value = 1
        self.app.poll_process()
        self.assertIsNone(self.app.process)
        self.assertIn('异常退出', self.app.status.set.call_args.args[0])
