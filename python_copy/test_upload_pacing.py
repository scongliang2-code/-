import unittest
from unittest.mock import MagicMock, patch
from upload_pacing import pause_for_upload_verification, wait_upload_state


class UploadPacingTests(unittest.TestCase):
    def test_verification_waits_for_user_then_resumes(self):
        with patch('upload_pacing.upload_verification_visible', side_effect=[True, False]), patch('builtins.input') as prompt:
            self.assertTrue(pause_for_upload_verification(MagicMock()))
        prompt.assert_called_once()

    def test_unresolved_verification_stops(self):
        with patch('upload_pacing.upload_verification_visible', return_value=True), patch('builtins.input'):
            with self.assertRaisesRegex(ValueError, '仍存在'):
                pause_for_upload_verification(MagicMock())

    def test_timeout_never_reuploads(self):
        ready = MagicMock(return_value=False)
        with patch('upload_pacing.pause_for_upload_verification', return_value=False):
            with self.assertRaisesRegex(ValueError, '不会自动重复上传'):
                wait_upload_state(MagicMock(), ready, '等待上传', timeout_ms=0)
        ready.assert_called_once()
