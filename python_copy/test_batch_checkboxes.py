import unittest
from unittest.mock import MagicMock, patch
from draft_product import select_image_batch, check_image_checkbox, find_image_batch


class BatchCheckboxTests(unittest.TestCase):
    def run_selection(self, inserted=None, ambiguous=False):
        page, frame = MagicMock(), MagicMock()
        frame.get_by_role.return_value.count.return_value = 0
        frame.get_by_text.return_value.count.return_value = 0
        cards = [MagicMock(), MagicMock()]
        for card in cards:
            card.get_by_role.return_value.count.return_value = 1
            card.get_by_role.return_value.is_checked.return_value = False
        if ambiguous:
            cards[0].get_by_role.return_value.count.return_value = 2
            cards[0].locator.return_value.count.return_value = 2
        with patch('draft_product.find_image_batch', return_value=False), patch('draft_product.pause_for_upload_verification'), \
                patch('draft_product.image_card', side_effect=cards), patch('draft_product.expect'):
            select_image_batch(page, frame, [{'name': 'tb-batch-01.jpg'}, {'name': 'tb-batch-02.jpg'}],
                               inserted=inserted)
        return page, frame, cards

    def test_icon_only_checkboxes_are_scoped_and_confirmed_once(self):
        page, frame, cards = self.run_selection()
        for card in cards:
            card.get_by_role.assert_called_once_with('checkbox')
            card.get_by_role.return_value.check.assert_called_once_with(timeout=5000)
        frame.get_by_role.return_value.click.assert_called_once_with(timeout=8000)
        self.assertTrue(any(c.args == (3000,) for c in page.wait_for_timeout.call_args_list))

    def test_realtime_main_picker_needs_no_confirm(self):
        _, frame, _ = self.run_selection(inserted=lambda: True)
        frame.get_by_role.return_value.click.assert_not_called()

    def test_ambiguous_checkbox_does_not_guess(self):
        with self.assertRaisesRegex(ValueError, '无法唯一定位'):
            self.run_selection(ambiguous=True)

    def test_hidden_input_clicks_label_and_verifies_state(self):
        card, checkbox = MagicMock(), MagicMock()
        checkbox.is_checked.return_value = False
        checkbox.is_visible.return_value = False
        handle = checkbox.evaluate_handle.return_value
        with patch('draft_product.expect') as verify:
            check_image_checkbox(card, checkbox)
        checkbox.check.assert_not_called()
        handle.as_element.return_value.click.assert_called_once_with(timeout=5000)
        verify.assert_called_once_with(checkbox)
        verify.return_value.to_be_checked.assert_called_once_with(timeout=5000)
        handle.dispose.assert_called_once()

    def test_checked_image_is_not_toggled_off(self):
        card, checkbox = MagicMock(), MagicMock()
        checkbox.is_checked.return_value = True
        check_image_checkbox(card, checkbox)
        card.hover.assert_not_called()
        checkbox.check.assert_not_called()
        checkbox.evaluate_handle.assert_not_called()

    def test_hidden_input_without_label_stops_without_forcing(self):
        card, checkbox = MagicMock(), MagicMock()
        checkbox.is_checked.return_value = False
        checkbox.is_visible.return_value = False
        checkbox.evaluate_handle.return_value.as_element.return_value = None
        with self.assertRaisesRegex(ValueError, '未找到对应'):
            check_image_checkbox(card, checkbox)
        checkbox.check.assert_not_called()

    def test_extra_cards_do_not_block_batch_but_disable_select_all(self):
        page, frame, card = MagicMock(), MagicMock(), MagicMock()
        card.count.return_value = 1
        frame.locator.return_value.count.return_value = 7
        with patch('draft_product.image_card', return_value=card), \
                patch('draft_product.wait_upload_state', side_effect=lambda p, ready, *a, **k: self.assertTrue(ready())):
            self.assertFalse(find_image_batch(page, frame, ['one', 'two'], 'batch'))

    def test_prefix_search_falls_back_without_uploading(self):
        page, frame = MagicMock(), MagicMock()
        frame.locator.return_value.count.return_value = 2
        with patch('draft_product.wait_upload_state', side_effect=[
                ValueError('等待本批图片出现在图片列表超时；已停止，不会自动重复上传'), None]):
            self.assertTrue(find_image_batch(page, frame, ['one', 'two'], 'batch'))
        self.assertEqual([c.args for c in frame.get_by_placeholder.return_value.fill.call_args_list], [('batch',), ('',)])

    def test_verification_failure_is_not_swallowed(self):
        with patch('draft_product.wait_upload_state', side_effect=ValueError('平台验证仍存在')):
            with self.assertRaisesRegex(ValueError, '平台验证'):
                find_image_batch(MagicMock(), MagicMock(), ['one'], 'batch')
