"""Fill a reviewable Taobao editor. Deliberately contains no submit/save action."""
import html
import json
import re
import time
from collections import Counter
from PIL import Image, ImageOps
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import expect, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from draft_basics import prepare_basic_draft, select_virtual_option, ensure_category_viewport
from upload_pacing import pause_for_upload_verification, wait_upload_state


def sku_label(row):
    return ' / '.join(value['value'] for value in row['values']) or '默认规格'


def fill_skus(page, plan):
    labels = [sku_label(s) for s in plan['skus']]
    if len(set(labels)) != len(labels):
        raise ValueError('规格显示名重复')
    if page.locator('tr.sku-table-row').count():
        raise ValueError('目标页面已存在SKU；为避免覆盖已有商品，本流程只填空白发布页')
    page.get_by_role('button', name='+ 创建规格', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='单层展示·自定义填写规格', exact=True).check()
    for index, label in enumerate(labels):
        if index:
            dialog.locator('button.add').click()
        fields = dialog.get_by_placeholder('输入规格')
        expect(fields).to_have_count(index + 1)
        fields.nth(index).fill(label)
        fields.nth(index).press('Tab')
    dialog.get_by_role('button', name='确认创建', exact=True).click()
    expect(page.locator('tr.sku-table-row')).to_have_count(len(labels))
    hint = page.get_by_role('button', name='我知道了', exact=True)
    if hint.is_visible():
        hint.click()
    for sku in plan['skus']:
        matching = [r for r in page.locator('tr.sku-table-row').all()
                    if r.locator('textarea[data-real=true]').input_value() == sku_label(sku)]
        if len(matching) != 1:
            raise ValueError('无法唯一匹配规格：' + sku_label(sku))
        row = matching[0]
        row.locator('[id$="-skuPrice"] input').fill(sku['price'])
        row.locator('[id$="-skuStock"] input').fill(str(sku['target_stock']))
        row.locator('[id$="-skuStock"] input').press('Tab')
    verify_skus(page, plan)


def verify_skus(page, plan):
    rows = page.locator('tr.sku-table-row').evaluate_all('''ns => ns.map(n => ({
        label:n.querySelector('textarea[data-real=true]').value,
        price:n.querySelector('[id$="-skuPrice"] input').value,
        stock:n.querySelector('[id$="-skuStock"] input').value
    }))''')
    expected = {sku_label(s): s for s in plan['skus']}
    if len(rows) != len(expected) or len({r['label'] for r in rows}) != len(rows):
        raise ValueError('SKU 数量不一致或有重复')
    for row in rows:
        sku = expected.get(row['label'])
        if not sku or Decimal(row['price']) != Decimal(sku['price']) or int(row['stock']) != sku['target_stock']:
            raise ValueError('SKU 回读不一致：' + row['label'])
    return rows


def try_check_component(locator, name, settings=None, checked=True, timeout_ms=3000):
    try:
        if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
            reason = '组件不存在、不唯一、隐藏或禁用'
        else:
            locator.set_checked(checked, timeout=timeout_ms)
            return True
    except PlaywrightError as error:
        if 'closed' in str(error).lower():
            raise
        reason = str(error).splitlines()[0]
    print(f'跳过配送组件「{name}」：{reason}', flush=True)
    if settings is not None:
        settings.setdefault('skipped_components', {})[name] = reason
    return False


def shipping_component_available(locator, name, report=None):
    if locator.count() == 1 and locator.is_visible() and locator.is_enabled():
        return True
    reason = '组件不存在、不唯一、隐藏或禁用'
    print(f'跳过配送组件「{name}」：{reason}', flush=True)
    if report is not None:
        report.setdefault('skipped_components', {})[name] = reason
    return False


def fill_shipping(page, plan):
    ship = plan['shipping']
    origin = ship['shipping_origin']
    for name in ('大陆及港澳台', '单一发货地'):
        try_check_component(page.get_by_role('radio', name=name, exact=True), name, plan)
    area = page.locator('#struct-shippingArea')
    origin_combo = area.get_by_role('combobox')
    has_origin = shipping_component_available(origin_combo, '发货省市', plan)
    if has_origin:
        origin_combo.click(timeout=3000)
        for level in ('province', 'city'):
            try:
                select_virtual_option(page, origin[level])
            except ValueError as error:
                raise ValueError('发货地选项不匹配：' + origin[level]) from error
    extract = page.locator('#struct-tbExtractWay')
    try_check_component(extract.get_by_role('checkbox', name=re.compile('使用物流配送')), '使用物流配送', plan)
    template_combo = extract.get_by_role('combobox')
    has_template = bool(ship.get('shipping_template')) and shipping_component_available(template_combo, '运费模板', plan)
    if has_template:
        template_combo.click(timeout=3000)
        template_combo.fill(ship['shipping_template'], timeout=3000)
        try:
            select_virtual_option(page, ship['shipping_template'])
        except ValueError as error:
            raise ValueError('当前店铺找不到运费模板：' + ship['shipping_template'] + '；请核对登录店铺和模板名称') from error
    sf = extract.get_by_role('checkbox', name=re.compile('针对快递包邮区域使用顺丰'))
    if ship['carrier'] == '普通快递' and sf.count():
        try_check_component(sf, '针对快递包邮区域使用顺丰', plan, checked=False)
    if has_origin:
        expect(area).to_contain_text(origin['province'] + ' / ' + origin['city'])
    if has_template:
        expect(extract).to_contain_text(ship['shipping_template'])


def selected_assets(plan, settings, directory):
    manifest = json.loads((directory / 'images.json').read_text(encoding='utf-8'))
    indexes = settings.get('main_image_indexes', [])
    if not indexes or len(indexes) > 5 or len(set(indexes)) != len(indexes):
        raise ValueError('请明确配置1到5张主图的来源序号')
    paths = []
    for index in indexes:
        matches = [m for m in manifest if m['group'] == 'main' and m['index'] == index]
        if len(matches) != 1:
            raise ValueError(f'缺少主图清单记录：{index}')
        source = (directory / matches[0]['file']).resolve()
        if directory.resolve() not in source.parents or not source.is_file():
            raise ValueError('素材路径不存在或超出商品目录')
        # The main-image upload route accepts JPEG reliably; retain originals separately.
        name = f'tb{plan["source_item_id"]}-main-{index:03d}-{matches[0]["sha256"][:8]}.jpg'
        target = directory / 'upload' / name
        target.parent.mkdir(exist_ok=True)
        with Image.open(source) as image:
            ImageOps.exif_transpose(image).convert('RGB').save(target, 'JPEG', quality=95)
        paths.append(target)
    return paths


def image_card(frame, filename):
    quoted = json.dumps(filename)
    identity = frame.get_by_text(filename, exact=True).or_(
        frame.locator(f'[title={quoted}], [alt={quoted}]'))
    return frame.locator('[class*="PicList_pic_background"]:visible').filter(has=identity)


def main_image_uploads(paths):
    # Repeated attempts leave previous assets in picture space. Never select by
    # a reusable local filename or assume the first same-named asset is ours.
    batch = uuid4().hex
    return [{'name': f'tb-{batch}-{index:02d}{path.suffix}',
             'mimeType': 'image/jpeg', 'buffer': path.read_bytes()}
            for index, path in enumerate(paths, 1)]


def choose_image_card(frame, filename, timeout_ms=30000):
    card = image_card(frame, filename)
    expect(card).to_have_count(1, timeout=timeout_ms)
    # The selectable label/overlay is inside the card and can cover its img.
    # A normal card click preserves actionability checks across list refreshes.
    card.click(timeout=timeout_ms)


MAIN_IMAGE_FRAME = 'iframe#mainImagesGroup:visible'


def active_main_image_frame(page, timeout_ms=10000):
    # Closed selectors may remain mounted with the same iframe id. Wait for
    # opening/closing transitions, and never pick first/last among live dialogs.
    try:
        expect(page.locator(MAIN_IMAGE_FRAME)).to_have_count(1, timeout=timeout_ms)
    except AssertionError as error:
        raise ValueError('主图选择器未稳定为一个可见窗口，请检查弹窗状态') from error
    return page.frame_locator(MAIN_IMAGE_FRAME)


def close_main_image_picker(page, timeout_ms=10000):
    frames = page.locator(MAIN_IMAGE_FRAME)
    if not frames.count():
        return
    title = page.get_by_placeholder('最多允许输入30个汉字（60字符）')
    try:
        title.click(timeout=min(3000, timeout_ms))
        expect(frames).to_have_count(0, timeout=min(1000, timeout_ms))
        return
    except (AssertionError, PlaywrightTimeoutError):
        # Escape inside a cross-origin iframe does not necessarily reach the
        # parent popover. Move focus into the main document before dismissing it.
        title.focus(timeout=min(3000, timeout_ms))
        title.press('Escape', timeout=min(3000, timeout_ms))
    try:
        expect(frames).to_have_count(0, timeout=timeout_ms)
    except AssertionError as error:
        raise ValueError('主图选择器未关闭，停止重复打开弹窗') from error


def finish_image_upload(page, frame, timeout_ms=8000, frame_element=None):
    finish = frame.get_by_role('button', name='完成', exact=True)
    search = frame.get_by_placeholder('搜索图片名称')
    pause_for_upload_verification(page)
    if not finish.is_visible() and search.is_visible():
        return
    # Both the outer iframe and its inner footer must fit the real window.
    try:
        outer = frame_element if frame_element is not None else page.locator(MAIN_IMAGE_FRAME)
        outer.evaluate(
            "n => n.scrollIntoView({block:'center', inline:'center', behavior:'instant'})", timeout=timeout_ms)
        finish.evaluate("n => n.scrollIntoView({block:'center', inline:'center', behavior:'instant'})", timeout=timeout_ms)
        finish.click(timeout=timeout_ms)
    except PlaywrightTimeoutError:
        # A click can take effect just as the timeout expires. Observe the UI;
        # never restart an upload or click a different button based on this error.
        print('完成按钮点击等待超时，正在核对上传面板是否已经关闭。', flush=True)
    wait_upload_state(page, lambda: not finish.is_visible() and search.is_visible(),
                      '等待上传面板关闭并返回图片列表', timeout_ms=15000)


def choose_local_upload_file(page, frame, upload, timeout_ms=15000):
    uploads = upload if isinstance(upload, list) else [upload]
    if not uploads:
        raise ValueError('上传批次不能为空')
    # Scope to the active picker. Hidden file inputs are normal; never guess
    # among multiple inputs or click the upload button again after a timeout.
    inputs = frame.locator('input[type="file"]:enabled')

    def set_unique_input():
        if inputs.count() != 1:
            return False
        if inputs.evaluate('n => n.files.length'):
            raise ValueError('上传控件已有文件，停止以避免重复上传')
        if len(uploads) > 1 and not inputs.evaluate('n => n.multiple'):
            raise ValueError('当前上传控件不支持批量选择，请检查上传入口；不会改成连续单张上传')
        inputs.set_input_files(uploads, timeout=timeout_ms)
        return True

    pause_for_upload_verification(page)
    if set_unique_input():
        return
    choosers = []
    def capture(chooser):
        choosers.append(chooser)
    page.on('filechooser', capture)
    try:
        frame.get_by_text('本地上传', exact=True).click(timeout=timeout_ms)
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if pause_for_upload_verification(page):
                deadline = time.monotonic() + timeout_ms / 1000
            if choosers:
                if len(uploads) > 1 and not choosers[0].is_multiple():
                    raise ValueError('当前文件选择器不支持批量上传')
                choosers[0].set_files(uploads, timeout=timeout_ms)
                return
            if set_unique_input():
                return
            if time.monotonic() >= deadline:
                raise ValueError('本地上传未出现文件选择事件，也未找到唯一可用的上传控件；已停止，不会重复点击或重传')
            page.wait_for_timeout(200)
    finally:
        page.remove_listener('filechooser', capture)


def upload_image_batch(page, frame, uploads, frame_element=None):
    pause_for_upload_verification(page)
    page.wait_for_timeout(3000)
    choose_local_upload_file(page, frame, uploads)
    success = frame.get_by_text(re.compile(r'^\s*' + str(len(uploads)) + r'\s*个文件上传成功\s*$'))
    wait_upload_state(page, lambda: success.is_visible(), '等待整批图片上传成功', timeout_ms=300000)
    finish_image_upload(page, frame, frame_element=frame_element)
    print(f'{len(uploads)} 张图片全部上传成功，上传面板已关闭。', flush=True)


def check_image_checkbox(card, checkbox, timeout_ms=5000):
    if checkbox.is_checked():
        return
    # Some pickers reveal the checkbox on hover; others keep the native input
    # hidden permanently and route clicks through its label.
    card.hover(timeout=timeout_ms)
    if checkbox.is_visible():
        checkbox.check(timeout=timeout_ms)
        return
    handle = checkbox.evaluate_handle('''input => {
      const visible = n => n && n.getClientRects().length &&
        getComputedStyle(n).visibility !== 'hidden' && getComputedStyle(n).display !== 'none';
      const labels = Array.from(input.labels || []).filter(visible);
      if (labels.length === 1) return labels[0];
      const wrapper = input.closest('.next-checkbox-wrapper') || input.closest('.next-checkbox');
      return visible(wrapper) ? wrapper : null;
    }''')
    try:
        target = handle.as_element()
        if target is None:
            raise ValueError('图片复选框已隐藏，且未找到对应的可见标签或勾选区域')
        target.click(timeout=timeout_ms)
        # Never click twice on an uncertain result: that could deselect it.
        expect(checkbox).to_be_checked(timeout=timeout_ms)
    finally:
        handle.dispose()


def find_image_batch(page, frame, names, prefix):
    search = frame.get_by_placeholder('搜索图片名称')
    description = '等待本批图片出现在图片列表'
    for query in (prefix, ''):
        search.fill(query)
        search.press('Enter')
        try:
            wait_upload_state(page, lambda: all(image_card(frame, name).count() == 1 for name in names),
                              description, timeout_ms=15000)
            return frame.locator('[class*="PicList_pic_background"]:visible').count() == len(names)
        except ValueError as error:
            if not str(error).startswith(description + '超时'):
                raise
            if query:
                print('批次搜索未找到全部图片，清空搜索核对已上传列表；不会重传。', flush=True)
    counts = [(name, image_card(frame, name).count()) for name in names]
    missing = [name for name, count in counts if count == 0]
    ambiguous = [name for name, count in counts if count > 1]
    raise ValueError(f'图片已上传，但列表未能唯一定位本批文件；未找到：{missing}；重复匹配：{ambiguous}。请检查图片列表分页或刷新，程序不会自动重传')


def select_image_batch(page, frame, uploads, interval_ms=3000, inserted=None):
    names = [upload['name'] for upload in uploads]
    if not names:
        raise ValueError('待选择的图片批次为空')
    prefix = names[0].rsplit('-', 1)[0] + '-'
    if any(not name.startswith(prefix) for name in names):
        raise ValueError('图片不属于同一个上传批次')
    only_batch = find_image_batch(page, frame, names, prefix)
    pause_for_upload_verification(page)
    page.wait_for_timeout(interval_ms)
    select_all = frame.get_by_role('checkbox', name='全选', exact=True)
    if only_batch and select_all.count() == 1 and select_all.is_visible():
        select_all.check(timeout=5000)
    else:
        select_all = frame.get_by_text('全选', exact=True)
        if only_batch and select_all.count() == 1 and select_all.is_visible():
            select_all.click(timeout=5000)
        else:
            print('列表含其他图片或未显示全选，仅勾选本批文件，完成后统一确认。', flush=True)
            for name in names:
                card = image_card(frame, name)
                checkbox = card.get_by_role('checkbox')
                if checkbox.count() != 1:
                    checkbox = card.locator('input[type="checkbox"]')
                if checkbox.count() != 1:
                    raise ValueError('无法唯一定位本批图片的复选框：' + name)
                pause_for_upload_verification(page)
                page.wait_for_timeout(interval_ms)
                check_image_checkbox(card, checkbox)
    # Main-image real-time pickers can insert on check and have no confirm.
    if inserted is not None and inserted():
        return
    confirm = frame.get_by_role('button', name=re.compile(r'^确定[（(]' + str(len(names)) + r'[）)]$'))
    try:
        expect(confirm).to_be_enabled(timeout=5000)
    except AssertionError as error:
        if inserted is not None and inserted():
            return
        raise ValueError('勾选后数量与本批图片不一致，当前入口可能限制选择数量；未确认插入') from error
    page.wait_for_timeout(interval_ms)
    pause_for_upload_verification(page)
    confirm.click(timeout=8000)


def upload_main_images(page, paths, interval_ms=3000):
    ensure_category_viewport(page)
    group = page.locator('#sell-field-mainImagesGroup')
    if group.locator('img').count():
        raise ValueError('目标已有主图，停止以避免覆盖或重复上传')
    group.evaluate("n => n.scrollIntoView({block: 'start', behavior: 'instant'})")
    uploads = main_image_uploads(paths)
    print(f'主图：一次批量上传 {len(uploads)} 张，搜索本批后全选并确认', flush=True)
    group.locator('.empty-container').first.click()
    frame = active_main_image_frame(page)
    upload_image_batch(page, frame, uploads)
    select_image_batch(page, frame, uploads, interval_ms,
                       inserted=lambda: group.locator('img').count() == len(uploads))
    wait_upload_state(page, lambda: group.locator('img').count() == len(uploads), '等待整批主图回读', timeout_ms=60000)
    close_main_image_picker(page)
    urls = group.locator('img').evaluate_all('ns => ns.map(n => n.src.replace(/\\.jpg_.*/, ".jpg"))')
    if any(not url.startswith('https://img.alicdn.com/') for url in urls):
        raise ValueError('上传后的图片地址不符合预期')
    return urls


def import_description(page, urls, settings):
    paragraphs = settings.get('description_paragraphs')
    if not paragraphs:
        raise ValueError('缺少目标详情文字')
    markup = ''.join('<p>' + html.escape(p) + '</p>' for p in paragraphs)
    markup += ''.join('<p><img src="' + html.escape(u, quote=True) + '" /></p>' for u in urls)
    page.get_by_text('源码导入', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name='源码', exact=True).click()
    dialog.locator('textarea.cke_source').fill(markup)
    dialog.get_by_role('button', name='导入', exact=True).click()
    expect(dialog).to_be_hidden(timeout=30000)
    area = page.locator('#struct-descRepublicOfSell')
    expect(area).to_contain_text(paragraphs[-1])
    for url in urls:
        if not area.locator('img').evaluate_all('(ns,u)=>ns.some(n=>n.src.startsWith(u))', url):
            raise ValueError('详情图回读缺失')


def detail_assets(product, directory):
    manifest = json.loads((directory / 'images.json').read_text(encoding='utf-8'))
    entries = sorted((m for m in manifest if m['group'] == 'detail'), key=lambda m: m['index'])
    if not entries or [m['url'] for m in entries] != product.get('detail_images', []):
        raise ValueError('原商品图文详情图片下载清单不完整')
    paths = []
    for index, entry in enumerate(entries, 1):
        source = (directory / entry['file']).resolve()
        if directory.resolve() not in source.parents or not source.is_file():
            raise ValueError('详情图片文件不存在或超出任务目录')
        target = directory / 'upload' / f'detail-{index:03d}.jpg'
        target.parent.mkdir(exist_ok=True)
        with Image.open(source) as image:
            ImageOps.exif_transpose(image).convert('RGB').save(target, 'JPEG', quality=95)
        paths.append(target)
    return paths


def new_detail_image_urls(area, before):
    # Subtract the initial URL multiset so editor icons, placeholders and
    # pre-existing content are not counted as this batch's uploaded images.
    remaining = Counter(before)
    added = []
    for url in area.locator('img').evaluate_all('ns => ns.map(n => n.src)'):
        if remaining[url]:
            remaining[url] -= 1
        else:
            added.append(url)
    return added


def upload_detail_images(page, paths):
    area = page.locator('#struct-descRepublicOfSell')
    before = area.locator('img').evaluate_all('ns => ns.map(n => n.src)')
    if before:
        print(f'详情区域已有 {len(before)} 个图片元素，保留现有内容，本次仅核对新增详情图。', flush=True)
    uploads = main_image_uploads(paths)
    print(f'详情图：第二批一次上传 {len(uploads)} 张；与主图间隔 10 秒', flush=True)
    page.wait_for_timeout(10000)
    pause_for_upload_verification(page)
    page.locator('#panel_edit').get_by_text('图片', exact=True).click(timeout=8000)
    dialog = page.get_by_role('dialog').filter(visible=True)
    outer = dialog.locator('iframe:visible')
    expect(outer).to_have_count(1, timeout=10000)
    frame = outer.content_frame
    upload_image_batch(page, frame, uploads, frame_element=outer)
    select_image_batch(page, frame, uploads)
    wait_upload_state(page, lambda: len(new_detail_image_urls(area, before)) == len(uploads) and outer.count() == 0,
                      '等待整批详情图片插入并关闭弹框', timeout_ms=30000)
    return new_detail_image_urls(area, before)


def fill_review_draft(context, product, settings, directory):
    from target_plan import make_plan
    plan = make_plan(product, settings)
    if not plan['target_configuration_complete']:
        raise ValueError('目标配置不完整：' + ', '.join(plan['needs_configuration']))
    paths = selected_assets(plan, settings, directory)
    detail_paths = detail_assets(product, directory)
    page = prepare_basic_draft(context, product, settings)
    try:
        fill_skus(page, plan)
        fill_shipping(page, plan)
        if settings.get('dispatch_time'):
            page.get_by_role('radio', name=settings['dispatch_time'], exact=True).check()
        urls = upload_main_images(page, paths)
        import_description(page, [], settings)
        detail_urls = upload_detail_images(page, detail_paths)
        rows = verify_skus(page, plan)
        from category_adapter import expected_attribute_values
        expect(page.locator('#struct-p-20000').get_by_role('combobox')).to_have_value(expected_attribute_values(settings)['品牌'])
        report = {'status': 'editor_filled_not_submitted', 'source_item_id': plan['source_item_id'],
                  'brand': settings['brand'], 'skus': rows, 'total_stock': sum(int(r['stock']) for r in rows),
                  'shipping': plan['shipping'], 'main_images': urls,
                  'detail_images': detail_urls,
                  'scope': '标题/品牌/目标SKU/主图/详情/物流；未提交',
                  'remaining_review': ['产品产地及其他类目属性须与实际商品核对，发货地使用目标配置', '平台默认服务承诺须确认']}
        if settings.get('generic_mode'):
            report['skipped_attributes'] = settings.get('skipped_attributes', {})
            report['reference_fallbacks'] = settings.get('reference_fallbacks', {})
            report['defaulted_attributes'] = settings.get('defaulted_attributes', {})
            from category_adapter import property_fields
            report['category_attributes'] = property_fields(page)
            available = {f['name'] for f in report['category_attributes']}
            report['attributes_without_target_field'] = [name for name in settings.get('attributes', {}) if name not in available]
        (directory / 'draft-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        page.screenshot(path=str(directory / 'draft-filled.png'), full_page=True)
        print('编辑页已填写并核对，未保存或提交。仍须核对报告中的类目属性与服务承诺。')
        return page
    except Exception:
        page.screenshot(path=str(directory / 'draft-error.png'), full_page=True)
        raise
