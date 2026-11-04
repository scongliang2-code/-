"""Resolve logistics from the reference editor, independently of product origin."""
from copy import deepcopy
import re


def read_shipping_reference(page):
    return page.evaluate('''() => {
      const value = id => {
        const block = document.querySelector(id);
        if (!block) return '';
        const combo = block.querySelector('[role=combobox]');
        return (combo?.getAttribute('aria-valuetext') || combo?.value ||
          block.querySelector('.next-select-values')?.textContent ||
          (id === '#struct-shippingArea' ? block.textContent : '') || '').trim();
      };
      const parts = value('#struct-shippingArea').replace(/^发货地[：:]?\\s*/, '').split(/\\s*[/／]\\s*/);
      const origin = parts.length >= 2 && parts.every(x => x && !x.includes('请选择'))
        ? {province: parts[0], city: parts[1]} : {};
      const template = value('#struct-tbExtractWay');
      return {shipping_origin: origin, shipping_template: template.includes('请选择') ? '' : template};
    }''')


def resolve_shipping_settings(settings):
    result = deepcopy(settings)
    reference = result.get('reference_shipping', {})
    origin = {k: str(result.get('shipping_origin', {}).get(k) or '').strip() for k in ('province', 'city')}
    fallback = reference.get('shipping_origin', {})
    clean = lambda s: re.sub(r'(壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区|省|市)$', '', s)
    if not origin['province'] and origin['city'] and origin['city'] != fallback.get('city'):
        raise ValueError('发货省缺失，且手填发货市与参考商品不同，请补充发货省')
    if origin['province'] and not origin['city'] and fallback.get('province') and clean(origin['province']) != clean(fallback['province']):
        raise ValueError('手填发货省与参考商品不同，请补充对应的发货市')
    for key in ('province', 'city'):
        if not origin[key]:
            origin[key] = fallback.get(key, '')
    if not all(origin.values()):
        raise ValueError('未配置且参考商品未读取到完整发货省市，请填写或提供包含发货地的参考发布页链接')
    result['shipping_origin'] = origin
    result['shipping_template'] = str(result.get('shipping_template') or reference.get('shipping_template') or '').strip()
    return result
