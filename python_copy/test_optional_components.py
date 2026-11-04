import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import Error, TimeoutError
from draft_product import try_check_component, fill_shipping


class OptionalComponentTests(unittest.TestCase):
    def test_shipping_continues_after_missing_region_radios(self):
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 0
        page.locator.return_value.get_by_role.return_value.count.return_value = 1
        plan = {'shipping': {'shipping_origin': {'province': '广西', 'city': '钦州'},
                             'shipping_template': '测试模板', 'carrier': '普通快递'}}
        with patch('draft_product.select_virtual_option') as select, patch('draft_product.expect'):
            fill_shipping(page, plan)
        page.get_by_role.return_value.check.assert_not_called()
        page.get_by_role.return_value.set_checked.assert_not_called()
        self.assertIn('大陆及港澳台', plan['skipped_components'])
        self.assertEqual([c.args[1] for c in select.call_args_list], ['广西', '钦州', '测试模板'])

    def test_missing_shipping_inputs_skip_without_click_fill_or_assertion(self):
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 0
        page.locator.return_value.get_by_role.return_value.count.return_value = 0
        plan = {'shipping': {'shipping_origin': {'province': '广西', 'city': '钦州'},
                             'shipping_template': '测试模板', 'carrier': '普通快递'}}
        with patch('draft_product.select_virtual_option') as select, patch('draft_product.expect') as verify:
            fill_shipping(page, plan)
        select.assert_not_called()
        verify.assert_not_called()
        page.locator.return_value.get_by_role.return_value.click.assert_not_called()
        page.locator.return_value.get_by_role.return_value.fill.assert_not_called()
        self.assertIn('发货省市', plan['skipped_components'])
        self.assertIn('运费模板', plan['skipped_components'])

    def test_missing_component_is_recorded_without_clicking(self):
        control = MagicMock()
        control.count.return_value = 0
        settings = {}
        self.assertFalse(try_check_component(control, '大陆及港澳台', settings))
        control.set_checked.assert_not_called()
        control.wait_for.assert_not_called()
        self.assertIn('大陆及港澳台', settings['skipped_components'])

    def test_duplicate_component_skips(self):
        control = MagicMock()
        control.count.return_value = 2
        self.assertFalse(try_check_component(control, '配送范围'))

    def test_unresponsive_component_skips(self):
        control = MagicMock()
        control.count.return_value = 1
        control.set_checked.side_effect = TimeoutError('disabled')
        self.assertFalse(try_check_component(control, '配送范围'))

    def test_available_component_sets_requested_state(self):
        control = MagicMock()
        control.count.return_value = 1
        self.assertTrue(try_check_component(control, '顺丰', checked=False))
        control.set_checked.assert_called_once_with(False, timeout=3000)

    def test_closed_page_still_stops(self):
        control = MagicMock()
        control.count.side_effect = Error('Target page, context or browser has been closed')
        with self.assertRaises(Error):
            try_check_component(control, '配送范围')


if __name__ == '__main__':
    unittest.main()
