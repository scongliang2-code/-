"""Read a reference form or open a shop page, without editing either."""
from urllib.parse import urlparse, parse_qs
from category_adapter import wait_for_property_fields
from draft_basics import ensure_category_viewport


def reference_kind(url):
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password or not (
        host == 'taobao.com' or host.endswith('.taobao.com') or
        host == 'tmall.com' or host.endswith('.tmall.com')):
        raise ValueError('参考链接须为 HTTPS 淘宝或天猫页面')
    if host == 'item.upload.taobao.com':
        item_id = parse_qs(parsed.query).get('itemId', [''])[0]
        if parsed.path != '/sell/v2/publish.htm' or not item_id.isdigit():
            raise ValueError('参考发布页链接须包含有效 itemId')
        return 'form'
    return 'shop'


def read_reference_page(context, url):
    kind = reference_kind(url)
    report = {'url': url, 'kind': kind, 'attributes': {}, 'ignored': []}
    if not kind:
        return report
    page = context.new_page()
    ensure_category_viewport(page)
    page.goto(url, wait_until='domcontentloaded', timeout=60000)
    if kind == 'shop':
        report['note'] = '已打开店铺参考页面供确认；不以此代替登录店铺核对'
        return report
    fields = wait_for_property_fields(page)
    candidates = {}
    for field in fields:
        if field['name'] and field['value'].strip():
            candidates.setdefault(field['name'], set()).add(field['value'].strip())
    for name, values in candidates.items():
        if len(values) == 1:
            report['attributes'][name] = next(iter(values))
        else:
            report['ignored'].append({'name': name, 'reason': '参考表单同名字段取值不同'})
    report['note'] = '仅读取属性；未填写、保存或提交参考表单'
    return report
