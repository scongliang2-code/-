import unittest
from copy import deepcopy
from attribute_plan import resolve_attributes
from run_task import validate_task
import test_publishing


class AttributePlanTests(unittest.TestCase):
    def setUp(self):
        self.product = {'properties': [{'name': '净含量'}, {'name': '自定义规格'}],
                        'source_attributes': [{'name': k, 'value': v} for k, v in {
                            '品牌': '木薯', '品种': '黄心木薯', '外包装种类': '食用农产品',
                            '包装方式': '食用农产品', '食用方式': '蒸煮食用', '城市': '钦州市',
                            '产地': '中国大陆', '省份': '广西壮族自治区', '净含量': '1斤,2斤',
                            '自定义规格': '大果', '发货地': '来源发货地'}.items()]}

    def test_source_parameters_and_store_overrides(self):
        settings = {'brand': '无品牌', 'attributes': {'城市': '梧州市'},
                    'shipping_origin': {'province': '广西', 'city': '梧州'}, 'stock_per_sku': 88}
        original = deepcopy(settings)
        result, report = resolve_attributes(self.product, settings)
        self.assertEqual(result['attributes']['品种'], '黄心木薯')
        self.assertEqual(result['attributes']['外包装种类'], '食用农产品')
        self.assertEqual(result['attributes']['包装方式'], '食用农产品')
        self.assertEqual(result['attributes']['城市'], '梧州市')
        self.assertEqual(result['brand'], '无品牌')
        self.assertEqual(result['shipping_origin'], settings['shipping_origin'])
        self.assertEqual(result['stock_per_sku'], 88)
        self.assertEqual(settings, original)
        origins = {a['name']: a['source'] for a in report['attributes']}
        self.assertEqual(origins['城市'], '手动配置')
        self.assertEqual(origins['品种'], '详情页')

    def test_blank_config_inherits_source_and_excludes_skus(self):
        result, report = resolve_attributes(self.product, {'brand': '', 'attributes': {'品种': ''}})
        self.assertEqual(result['brand'], '木薯')
        self.assertEqual(result['attributes']['城市'], '钦州市')
        for name in ['净含量', '自定义规格', '发货地', '系列']:
            self.assertNotIn(name, result['attributes'])
        self.assertEqual(len(report['ignored']), 3)

    def test_conflicting_source_value_is_not_guessed(self):
        self.product['source_attributes'].append({'name': '品种', 'value': '其他品种'})
        result, report = resolve_attributes(self.product, {'brand': '无品牌'})
        self.assertNotIn('品种', result['attributes'])
        self.assertTrue(any(r['name'] == '品种' for r in report['ignored']))
        result, _ = resolve_attributes(self.product, {'attributes': {'品种': '手动品种'}})
        self.assertEqual(result['attributes']['品种'], '手动品种')

    def test_missing_source_brand_requires_manual_value(self):
        with self.assertRaisesRegex(ValueError, '请手动配置品牌'):
            resolve_attributes({}, {'brand': ''})

    def test_attribute_brand_used_when_brand_box_empty(self):
        result, _ = resolve_attributes(self.product, {'brand': '', 'attributes': {'品牌': '指定品牌'}})
        self.assertEqual(result['brand'], '指定品牌')

    def test_task_accepts_brand_from_source(self):
        task = test_publishing.PublishingTests().task()
        task['settings']['brand'] = ''
        validate_task(task)

    def test_reference_fills_only_missing_parameters_and_excludes_skus(self):
        settings = {'brand': '无品牌', 'attributes': {'城市': '梧州市'},
                    'reference_attributes': {'城市': '南宁市', '品种': '参考品种',
                                             '系列': '参考系列', '净含量': '10斤'}}
        result, report = resolve_attributes(self.product, settings)
        self.assertEqual(result['attributes']['城市'], '梧州市')
        self.assertEqual(result['attributes']['品种'], '黄心木薯')
        self.assertEqual(result['attributes']['系列'], '参考系列')
        self.assertNotIn('净含量', result['reference_attributes'])
        self.assertEqual(next(a['source'] for a in report['attributes'] if a['name'] == '系列'), '参考发布页')
