"""Discover category property controls; stop on ambiguous/unsupported controls."""
from playwright.sync_api import expect
import time
from draft_basics import select_virtual_option, ensure_category_viewport, confirm_category


def missing_required_attributes(fields):
    """Required fields come from the current category, never a fixed name list."""
    return [f['name'] or f['id'] for f in fields if f['required'] and not f['value']]


def expected_attribute_values(settings):
    values = dict(settings.get('attributes', {}), 品牌=settings['brand'])
    for name in settings.get('skipped_attributes', {}):
        values[name] = ''
    return values


def leave_unmatched_attribute_empty(page, block, field, value, settings):
    search = page.locator('.options-search > .next-input > input:visible')
    if search.count() == 1:
        search.fill('')
    native = block.locator('select')
    combo = block.get_by_role('combobox')
    if native.count() == 1:
        native.select_option([])
    elif combo.count() == 1:
        if combo.evaluate("n => n.tagName === 'INPUT' && !n.readOnly"):
            combo.fill('')
        combo.press('Escape')
        combo.press('Tab')
    else:
        inputs = block.locator('input:visible:not([readonly]):not([disabled]):not([type=hidden]):not([type=radio]):not([type=checkbox]),textarea:visible:not([readonly]):not([disabled])')
        if inputs.count() == 1:
            inputs.fill('')
            inputs.press('Tab')
    fields = wait_for_property_fields(page)
    current = next((f for f in fields if f['id'] == field['id']), None)
    if current is None or current['value']:
        raise ValueError('选项不匹配且无法确认已留空：' + field['name'])
    settings.setdefault('skipped_attributes', {})[field['name']] = {
        'requested_value': value, 'reason': '发布页无唯一匹配选项，已留空'}
    print('属性选项不匹配，已留空继续：' + field['name'] + '=' + value, flush=True)


def property_fields(page):
    fields = page.locator('[id^="struct-p-"]').evaluate_all(r'''ns => ns.filter(n =>
      n.getClientRects().length && !n.querySelector('[id^="struct-p-"]')).map(n => {
        const w=n.closest('.sell-component-info-wrapper-wrap') || n;
        const control=n.querySelector('input,textarea,select');
        const linkedIds=(control?.getAttribute('aria-labelledby') || n.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
        const linked=linkedIds.map(id=>document.getElementById(id)?.textContent.trim() || '').filter(Boolean).join(' ');
        const labels=[...document.querySelectorAll('label[for]')].filter(l=>l.htmlFor===n.id || (control?.id && l.htmlFor===control.id));
        const name=w.querySelector('.sell-component-info-wrapper-label')?.textContent.trim() || linked ||
          (labels.length===1 ? labels[0].textContent.trim() : '') || control?.getAttribute('aria-label') || '';
        return ({
        id:n.id,
        name:name.replace(/^\s*\*\s*/, '').replace(/[：:]\s*$/, '').trim(),
        required:!!w.querySelector('.sell-component-info-wrapper-required') || !!n.querySelector('[required],[aria-required="true"]'),
        value:[...new Set([...n.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,select,.next-select-values em,input[type=checkbox]:checked,input[type=radio]:checked')].filter(x=>!x.closest('.options-search')).map(x=>(x.tagName === 'SELECT' ? (x.value ? [...x.selectedOptions].map(o=>o.textContent.trim()).join(' / ') : '') : (x.value || x.textContent || '')).trim()).filter(Boolean))].join(' / ')
      })})''')
    # The category chooser uses a different label layout from the editor.
    # These exact IDs were verified on the chooser; do not guess unknown IDs.
    from urllib.parse import urlparse
    location = urlparse(page.url)
    chooser_fields = {
        'struct-p-21299': '产地',
        'struct-p-21299-122450261': '省份',
        'struct-p-21299-122450261-20741': '城市',
        'struct-p-6939376': '包装方式',
    }
    if location.hostname == 'item.upload.taobao.com' and location.path == '/sell/ai/category.htm':
        for field in fields:
            if field['id'] in chooser_fields:
                if not field['name']:
                    field['name'] = chooser_fields[field['id']]
                field['required'] = True
    return fields


def wait_for_property_fields(page, timeout_ms=20000, stable_ms=1000):
    """Wait for named fields to settle, without treating an empty page as valid."""
    deadline = time.monotonic() + timeout_ms / 1000
    previous, since, fields = None, None, []
    while time.monotonic() < deadline:
        fields = property_fields(page)
        signature = tuple((f['id'], f['name'], f['required'], f['value']) for f in fields)
        now = time.monotonic()
        if fields and all(f['name'] for f in fields):
            if signature != previous:
                since = now
            elif since is not None and (now - since) * 1000 >= stable_ms:
                return fields
        else:
            since = None
        previous = signature
        page.wait_for_timeout(200)
    if not fields:
        raise ValueError('等待类目属性超时：未发现可见属性控件。可能尚未进入属性页、加载失败或当前类目结构未适配')
    unnamed = [f['id'] for f in fields if not f['name']]
    if unnamed:
        raise ValueError('发现属性控件但无法识别名称：' + '、'.join(unnamed))
    raise ValueError('类目属性持续刷新，尚未稳定，请检查页面')


def try_attribute_value(page, block, name, value):
    native = block.locator('select')
    combo = block.get_by_role('combobox')
    if native.count() == 1:
        matches = native.locator('option').evaluate_all('(ns,v) => ns.filter(n => n.label === v && !n.disabled).length', value)
        if matches != 1:
            return False
        native.select_option(label=value, timeout=10000)
    elif combo.count() == 1:
        combo.click()
        search = page.locator('.options-search > .next-input > input:visible')
        if search.count() == 1:
            search.fill(value)
        try:
            select_virtual_option(page, value)
        except ValueError:
            combo.press('Escape')
            editable = combo.evaluate("n => ['INPUT', 'TEXTAREA'].includes(n.tagName) && !n.readOnly && !n.disabled")
            if not editable:
                return False
            # Some category controls accept custom text even though they expose
            # combobox semantics. Blur commits it without risking form submission.
            if search.count() == 1:
                search.fill('')
            combo.fill(value)
            combo.press('Tab')
            field_id = block.get_attribute('id')
            fields = wait_for_property_fields(page)
            current = next((f for f in fields if f['id'] == field_id), None)
            if current is None or current['value'].strip() != value.strip():
                return False
            print('未找到对应选项，已直接录入并核对：' + name + '=' + value, flush=True)
    else:
        inputs = block.locator('input:not([type=hidden]):not([type=radio]):not([type=checkbox]),textarea')
        if inputs.count() != 1:
            raise ValueError('尚未适配该属性控件：' + name)
        inputs.fill(value)
        inputs.press('Tab')
    return True


def fill_empty_attribute_with_none(page, block, field, settings):
    if not settings.get('fill_empty_attributes_with_none', False) or field['value'].strip():
        return False
    if block.locator('select').count():
        return False
    inputs = block.locator('input:visible:not([readonly]):not([disabled]),textarea:visible:not([readonly]):not([disabled])')
    if inputs.count() != 1 or not inputs.evaluate("n => !n.closest('.options-search') && (n.tagName === 'TEXTAREA' || ['text','search'].includes(n.type))"):
        return False
    inputs.fill('无')
    inputs.press('Tab')
    current = next((f for f in wait_for_property_fields(page) if f['id'] == field['id']), None)
    if current is None or current['value'].strip() != '无':
        # A dropdown may reject custom text. Do not claim a default was accepted.
        if current is not None and current['value']:
            inputs.fill('')
            inputs.press('Tab')
            cleared = next((f for f in wait_for_property_fields(page) if f['id'] == field['id']), None)
            if cleared is None or cleared['value']:
                raise ValueError('默认值未被接受且无法清空：' + field['name'])
        return False
    settings.setdefault('attributes', {})[field['name']] = '无'
    if field['name'] == '品牌':
        settings['brand'] = '无'
    settings.setdefault('defaulted_attributes', {})[field['name']] = '无'
    settings.get('skipped_attributes', {}).pop(field['name'], None)
    print('属性无可用取值，已直接录入并核对：' + field['name'] + '=无', flush=True)
    return True


def first_attribute_option(page, block):
    native = block.locator('select')
    if native.count() == 1:
        return native.locator('option').evaluate_all('''ns => {
          const n = ns.find(n => !n.disabled && !n.parentElement.disabled && n.value &&
            n.label.trim() && !/^请选择/.test(n.label.trim()));
          return n ? n.label.trim() : '';
        }''')
    combo = block.get_by_role('combobox')
    if combo.count() != 1:
        return ''
    combo.click(timeout=3000)
    search = page.locator('.options-search > .next-input > input:visible')
    if search.count() == 1:
        search.fill('')
    try:
        for _ in range(10):
            value = page.locator('.options-item:visible, [role="option"]:visible, .next-menu-item:visible').evaluate_all('''ns => {
              const n = ns.find(n => !n.matches('[disabled],[aria-disabled="true"],.disabled,.next-disabled') &&
                n.innerText.trim() && !/^请选择/.test(n.innerText.trim()));
              return n ? (n.getAttribute('title') || n.innerText).trim() : '';
            }''')
            if value:
                return value
            page.wait_for_timeout(200)
        return ''
    finally:
        combo.press('Escape', timeout=3000)


def fill_required_attribute(page, block, field, settings):
    name = field['name']
    candidates = []
    configured = settings.get('attributes', {}).get(name)
    if name == '品牌':
        configured = settings.get('brand') or configured
    if settings.get('attribute_origins', {}).get(name) == '手动配置' and configured:
        candidates.append((configured, '手动配置'))
    source = settings.get('source_attributes', {}).get(name)
    reference = settings.get('reference_attributes', {}).get(name)
    candidates.extend([(source, '商品详情'), (reference, '参考目标店铺页面')])
    if configured and not any(value == configured for value, _ in candidates):
        candidates.insert(0, (configured, '现有配置'))
    tried = set()
    for value, origin in candidates:
        if not value or value in tried:
            continue
        tried.add(value)
        if field['value'].strip() == str(value).strip() or try_attribute_value(page, block, name, value):
            break
    else:
        if field['value'].strip() and not tried:
            return  # Preserve an existing selection when no replacement is known.
        value = first_attribute_option(page, block)
        origin = '第一个有效选项'
        if not value or not try_attribute_value(page, block, name, value):
            leave_unmatched_attribute_empty(page, block, field, '', settings)
            return
    settings.setdefault('attributes', {})[name] = value
    if name == '品牌':
        settings['brand'] = value
    settings.get('skipped_attributes', {}).pop(name, None)
    settings.setdefault('required_attribute_results', {})[name] = {'value': value, 'source': origin}
    print(f'必填属性：{name}={value}（{origin}）', flush=True)


def fill_attributes(page, settings, strict=True):
    values = dict(settings.get('attributes', {}))
    values['品牌'] = settings['brand']
    # Re-discover after each change: one property can add/remove dependent fields.
    done = set()
    for _ in range(100):
        fields = wait_for_property_fields(page)
        pending = [f for f in fields if f['id'] not in done and (f['name'] in values or
                   (settings.get('required_attribute_fallback', False) and f['required']) or
                   (settings.get('fill_empty_attributes_with_none', False) and not f['value'].strip()))]
        if not pending:
            break
        field = pending[0]
        block = page.locator('[id="' + field['id'] + '"]')
        if field['required'] and settings.get('required_attribute_fallback', False):
            fill_required_attribute(page, block, field, settings)
            values = dict(settings.get('attributes', {}), 品牌=settings['brand'])
            done.add(field['id'])
            continue
        if field['name'] not in values:
            fill_empty_attribute_with_none(page, block, field, settings)
            done.add(field['id'])
            continue
        value = str(values[field['name']])
        # The chooser carries values into the editor. Reopening a selected
        # dropdown creates duplicate matching text (selected value + option).
        if field['value'].strip() == value.strip():
            settings.get('skipped_attributes', {}).pop(field['name'], None)
            done.add(field['id'])
            continue
        if not try_attribute_value(page, block, field['name'], value):
            reference = settings.get('reference_attributes', {}).get(field['name'])
            if reference and reference != value and try_attribute_value(page, block, field['name'], reference):
                settings.setdefault('reference_fallbacks', {})[field['name']] = {
                    'original_value': value, 'value': reference, 'source': '参考发布页'}
                settings.setdefault('attributes', {})[field['name']] = reference
                values[field['name']] = reference
                if field['name'] == '品牌':
                    settings['brand'] = reference
                print('已使用参考发布页参数：' + field['name'] + '=' + reference, flush=True)
            else:
                leave_unmatched_attribute_empty(page, block, field, value, settings)
                # Explicit manual values are not replaced by the generic default.
                if settings.get('attribute_origins', {}).get(field['name']) != '手动配置':
                    empty_field = dict(field, value='')
                    fill_empty_attribute_with_none(page, block, empty_field, settings)
                done.add(field['id'])
                continue
        done.add(field['id'])
        settings.get('skipped_attributes', {}).pop(field['name'], None)
        page.wait_for_timeout(500)
        if field['name'] == '品牌':
            page.wait_for_timeout(5000)
    fields = wait_for_property_fields(page)
    verify_attribute_values(fields, expected_attribute_values(settings))
    missing = [name for name in missing_required_attributes(fields)
               if name not in settings.get('skipped_attributes', {})]
    if missing and settings.get('allow_missing_attributes', False):
        print('详情页或手动配置未补齐以下必填属性，留空交由平台校验：' + '、'.join(missing), flush=True)
    elif strict and missing:
        raise ValueError('请在类目属性中补充：' + '、'.join(missing))
    return fields


def verify_attribute_values(fields, values):
    for field in fields:
        if field['name'] in values and field['value'].strip() != str(values[field['name']]).strip():
            raise ValueError('属性回读不一致：' + field['name'] + '，期望=' + str(values[field['name']]) + '，实际=' + (field['value'] or '未选择'))


def prepare_category(context, product, settings):
    page = context.new_page()
    ensure_category_viewport(page)
    page.goto('https://item.upload.taobao.com/sell/ai/category.htm', wait_until='domcontentloaded')
    search = page.get_by_placeholder('可输入产品名称、类目关键词、条码信息')
    try:
        search.wait_for(timeout=15000)
    except Exception:
        input('请登录目标卖家店铺，进入选择类目页后按回车：')
    expect(page.get_by_text(settings['shop_name'], exact=True)).to_be_visible(timeout=15000)
    category = settings['category']
    search.fill(category.split('>')[-1])
    page.get_by_role('button', name='搜索', exact=True).click()
    page.get_by_text(category, exact=True).click()
    fill_attributes(page, settings)
    confirm_category(page)
    title = page.get_by_placeholder('最多允许输入30个汉字（60字符）')
    title.wait_for(timeout=60000)
    title.fill(settings.get('title') or product['title'])
    fill_attributes(page, settings)
    expect(title).to_have_value(settings.get('title') or product['title'])
    return page
