import unittest
from unittest.mock import MagicMock, patch
from shipping_reference import resolve_shipping_settings
from run_task import validate_task
from draft_product import fill_shipping
import test_publishing


class ShippingReferenceTests(unittest.TestCase):
    def test_validation_allows_blank_template_and_origin_for_later_resolution(self):
        task = test_publishing.PublishingTests().task()
        task['settings'].update(shipping_template='', shipping_origin={})
        validate_task(task)

    def test_reference_supplies_missing_origin_and_optional_template(self):
        result = resolve_shipping_settings({'shipping_origin': {}, 'reference_shipping': {
            'shipping_origin': {'province': '广西', 'city': '梧州'}}})
        self.assertEqual(result['shipping_origin'], {'province': '广西', 'city': '梧州'})
        self.assertEqual(result['shipping_template'], '')

    def test_manual_settings_have_priority(self):
        result = resolve_shipping_settings({'shipping_origin': {'province': '广东', 'city': '广州'},
            'shipping_template': '自用模板', 'reference_shipping': {
                'shipping_origin': {'province': '广西', 'city': '梧州'}, 'shipping_template': '参考模板'}})
        self.assertEqual(result['shipping_origin']['city'], '广州')
        self.assertEqual(result['shipping_template'], '自用模板')

    def test_province_city_are_not_mixed_across_provinces(self):
        with self.assertRaisesRegex(ValueError, '对应的发货市'):
            resolve_shipping_settings({'shipping_origin': {'province': '广东', 'city': ''},
                'reference_shipping': {'shipping_origin': {'province': '广西', 'city': '梧州'}}})

    def test_missing_required_shipping_is_reported_after_reference_attempt(self):
        with self.assertRaisesRegex(ValueError, '参考商品未读取到完整发货省市'):
            resolve_shipping_settings({'shipping_origin': {}})

    def test_optional_template_not_touched_when_empty(self):
        page = MagicMock()
        page.locator.return_value.get_by_role.return_value.count.return_value = 1
        ship = {'shipping_origin': {'province': '广西', 'city': '梧州'}, 'shipping_template': '', 'carrier': '普通快递'}
        with patch('draft_product.select_virtual_option') as select, patch('draft_product.expect'):
            fill_shipping(page, {'shipping': ship})
        self.assertEqual([c.args[1] for c in select.call_args_list], ['广西', '梧州'])

    def test_unmatched_origin_and_template_use_reference(self):
        page = MagicMock()
        page.locator.return_value.get_by_role.return_value.count.return_value = 1
        ship = {'shipping_origin': {'province': '旧省', 'city': '旧市'}, 'shipping_template': '旧模板', 'carrier': '普通快递'}
        settings = {'reference_shipping': {'shipping_origin': {'province': '广西', 'city': '梧州'}, 'shipping_template': '参考模板'}}
        def select(_, value):
            if value in ('旧省', '旧模板'):
                raise ValueError('missing')
        with patch('draft_product.select_virtual_option', side_effect=select), patch('draft_product.expect'):
            fill_shipping(page, {'shipping': ship}, settings)
        self.assertEqual(settings['shipping_origin'], {'province': '广西', 'city': '梧州'})
        self.assertEqual(settings['shipping_template'], '参考模板')

    def test_unmatched_optional_template_is_skipped(self):
        page = MagicMock()
        page.locator.return_value.get_by_role.return_value.count.return_value = 1
        ship = {'shipping_origin': {'province': '广西', 'city': '梧州'}, 'shipping_template': '不存在', 'carrier': '普通快递'}
        def select(_, value):
            if value == '不存在':
                raise ValueError('missing')
        with patch('draft_product.select_virtual_option', side_effect=select), patch('draft_product.expect'):
            fill_shipping(page, {'shipping': ship})
        self.assertEqual(ship['shipping_template'], '')
