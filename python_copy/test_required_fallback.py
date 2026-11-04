import unittest
from unittest.mock import MagicMock, patch
from category_adapter import fill_required_attribute
from attribute_plan import resolve_attributes


class RequiredFallbackTests(unittest.TestCase):
    def run_case(self, responses, option='默认项'):
        settings = {'brand': '无品牌', 'attributes': {'品种': '来源值'},
                    'source_attributes': {'品种': '来源值'}, 'reference_attributes': {'品种': '参考值'}}
        field = {'id': 'x', 'name': '品种', 'value': '', 'required': True}
        with patch('category_adapter.try_attribute_value', side_effect=responses) as attempt, \
                patch('category_adapter.first_attribute_option', return_value=option) as first, \
                patch('category_adapter.leave_unmatched_attribute_empty') as leave:
            fill_required_attribute(MagicMock(), MagicMock(), field, settings)
        return settings, [c.args[-1] for c in attempt.call_args_list], first, leave

    def test_source_before_reference(self):
        settings, values, first, _ = self.run_case([True])
        self.assertEqual(values, ['来源值'])
        first.assert_not_called()

    def test_reference_when_source_unmatched(self):
        settings, values, first, _ = self.run_case([False, True])
        self.assertEqual(values, ['来源值', '参考值'])
        self.assertEqual(settings['attributes']['品种'], '参考值')
        first.assert_not_called()

    def test_first_option_after_both_unmatched(self):
        settings, values, _, _ = self.run_case([False, False, True])
        self.assertEqual(values, ['来源值', '参考值', '默认项'])
        self.assertEqual(settings['required_attribute_results']['品种']['source'], '第一个有效选项')

    def test_no_option_leaves_blank_not_none_text(self):
        _, values, _, leave = self.run_case([False, False], option='')
        leave.assert_called_once()
        self.assertNotIn('无', values)

    def test_manual_override_preserves_source_for_fallback(self):
        result, _ = resolve_attributes({'source_attributes': [{'name': '品种', 'value': '来源值'}]},
            {'brand': '无品牌', 'attributes': {'品种': '手动值'}})
        self.assertEqual(result['source_attributes']['品种'], '来源值')
        self.assertEqual(result['attributes']['品种'], '手动值')
