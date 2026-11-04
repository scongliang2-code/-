"""Merge source product parameters with explicit destination overrides."""
from copy import deepcopy


def resolve_attributes(product, settings):
    result = deepcopy(settings)
    sku_names = {p['name'] for p in product.get('properties', [])}
    excluded = sku_names | {'库存', '价格', '售价', '发货地',
                            '发货省', '发货市', '运费', '运费模板', '发货时限'}
    candidates, ignored = {}, []
    for attribute in product.get('source_attributes', []):
        name = str(attribute.get('name') or '').strip().rstrip('：:').strip()
        value = str(attribute.get('value') or '').strip()
        if not name or not value:
            continue
        if name in excluded:
            ignored.append({'name': name, 'value': value, 'reason': 'SKU或店铺配置，不能作为类目属性复制'})
            continue
        candidates.setdefault(name, set()).add(value)
    values, origins = {}, {}
    for name, options in candidates.items():
        if len(options) == 1:
            values[name] = next(iter(options))
            origins[name] = '详情页'
        else:
            ignored.append({'name': name, 'values': sorted(options), 'reason': '来源同名属性有不同取值，需手动配置'})
    result['source_attributes'] = dict(values)
    for name, value in settings.get('attributes', {}).items():
        name = str(name).strip().rstrip('：:').strip()
        value = str(value).strip() if value is not None else ''
        if name and value and name not in excluded:
            values[name] = value
            origins[name] = '手动配置'
    brand = str(settings.get('brand') or '').strip()
    if brand:
        values['品牌'], origins['品牌'] = brand, '手动配置'
    references = {str(k).strip(): str(v).strip() for k, v in settings.get('reference_attributes', {}).items()
                  if str(k).strip() not in excluded and v is not None and str(v).strip()}
    result['reference_attributes'] = references
    for name, value in references.items():
        if name not in values:
            values[name], origins[name] = value, '参考发布页'
    result['brand'] = values.get('品牌', '')
    if not result['brand']:
        raise ValueError('详情页未提供唯一品牌，请手动配置品牌')
    result['attributes'] = values
    result['attribute_origins'] = origins
    report = {'attributes': [{'name': name, 'value': value, 'source': origins[name]}
                             for name, value in values.items()], 'ignored': ignored,
              'note': '填写计划；仅匹配发布页存在的字段，实际选择结果须回读核对'}
    return result, report
