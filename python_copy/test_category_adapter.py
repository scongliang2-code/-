"""DOM regression tests in a local, headless Chrome; no seller requests."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright, Locator, ElementHandle, TimeoutError as PlaywrightTimeoutError
from category_adapter import (property_fields, wait_for_property_fields, fill_attributes,
                              expected_attribute_values, verify_attribute_values)
from draft_basics import select_virtual_option, confirm_category, ensure_category_viewport
from copy_product import collect_source_attributes
from attribute_plan import resolve_attributes
from reference_page import read_reference_page, reference_kind
from upload_pacing import upload_verification_visible
from draft_product import (verify_skus, fill_shipping, main_image_uploads, choose_image_card, upload_main_images, finish_image_upload, choose_local_upload_file,
                           active_main_image_frame, close_main_image_picker)


class CategoryAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_local_upload_uses_intercepted_chooser_input_in_iframe(self):
        self.page.set_content('<iframe id="mainImagesGroup" name="pictures"></iframe>')
        inner = self.page.frame(name='pictures')
        inner.set_content('''<input type="file" id="unused" hidden>
          <input type="file" id="actual" hidden onchange="document.body.dataset.filename=this.files[0].name">
          <button onclick="document.querySelector('#actual').click()">本地上传</button>''')
        choose_local_upload_file(self.page, self.page.frame_locator('#mainImagesGroup'),
                                 {'name': 'chosen.jpg', 'mimeType': 'image/jpeg', 'buffer': b'image'})
        self.assertEqual(inner.locator('body').get_attribute('data-filename'), 'chosen.jpg')
        self.assertEqual(inner.locator('#unused').evaluate('n => n.files.length'), 0)
        self.assertEqual(inner.locator('#actual').evaluate('n => n.files.length'), 1)

    def test_local_upload_existing_unique_input_needs_no_chooser(self):
        self.page.set_content('<iframe name="pictures"></iframe>')
        inner = self.page.frame(name='pictures')
        inner.set_content('<input type="file" hidden>')
        choose_local_upload_file(self.page, self.page.frame_locator('iframe'),
                                 {'name': 'direct.jpg', 'mimeType': 'image/jpeg', 'buffer': b'image'})
        self.assertEqual(inner.locator('input').evaluate('n=>n.files[0].name'), 'direct.jpg')

    def test_local_upload_button_opens_panel_without_chooser(self):
        self.page.set_content('<iframe name="pictures"></iframe>')
        inner = self.page.frame(name='pictures')
        inner.set_content('''<button onclick="setTimeout(()=>{
          const input=document.createElement('input'); input.type='file';input.hidden=true;
          document.body.append(input);},100)">本地上传</button>''')
        choose_local_upload_file(self.page, self.page.frame_locator('iframe'),
                                 {'name': 'panel.jpg', 'mimeType': 'image/jpeg', 'buffer': b'image'})
        self.assertEqual(inner.locator('input').evaluate('n=>n.files[0].name'), 'panel.jpg')

    def test_local_upload_missing_input_stops_without_reclick(self):
        self.page.set_content('<iframe name="pictures"></iframe>')
        inner = self.page.frame(name='pictures')
        inner.set_content('''<button onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1">本地上传</button>''')
        with self.assertRaisesRegex(ValueError, '不会重复点击'):
            choose_local_upload_file(self.page, self.page.frame_locator('iframe'),
                                     {'name': 'missing.jpg', 'mimeType': 'image/jpeg', 'buffer': b'image'}, timeout_ms=500)
        self.assertEqual(inner.locator('button').get_attribute('data-clicks'), '1')

    def test_image_picker_escape_runs_in_parent_document(self):
        self.page.set_content('''<input placeholder="最多允许输入30个汉字（60字符）">
          <iframe id="mainImagesGroup" name="pictures" srcdoc="<input>"></iframe>
          <script>document.addEventListener('keydown', e => {
            if(e.key === 'Escape') document.querySelector('iframe').style.display='none';
          });</script>''')
        self.page.frame(name='pictures').locator('input').focus()
        close_main_image_picker(self.page, timeout_ms=2000)
        self.assertEqual(self.page.locator('iframe#mainImagesGroup:visible').count(), 0)

    def test_image_picker_already_closed_needs_no_click(self):
        self.page.set_content('<iframe id="mainImagesGroup" style="display:none"></iframe>')
        close_main_image_picker(self.page, timeout_ms=300)

    def test_upload_finish_scrolls_outer_frame_and_inner_footer(self):
        self.page.set_content('''<div style="height:1800px"></div>
          <iframe id="mainImagesGroup" name="pictures" style="width:800px;height:300px"></iframe>
          <div style="height:1000px"></div>''')
        frame = self.page.frame(name='pictures')
        frame.set_content('''<div style="height:1200px"></div><button
          onclick="this.style.display='none';document.querySelector('input').style.display='block'">完成</button>
          <input placeholder="搜索图片名称" style="display:none">''')
        finish_image_upload(self.page, self.page.frame_locator('#mainImagesGroup'), timeout_ms=2000)
        self.assertFalse(frame.get_by_role('button', name='完成').is_visible())
        self.assertTrue(frame.get_by_placeholder('搜索图片名称').is_visible())

    def test_upload_finish_accepts_closed_panel_after_click_timeout(self):
        self.page.set_content('<iframe id="mainImagesGroup" name="pictures"></iframe>')
        frame = self.page.frame(name='pictures')
        frame.set_content('''<button onclick="this.remove();document.querySelector('input').style.display='block'">完成</button>
          <input placeholder="搜索图片名称" style="display:none">''')
        original = Locator.click
        clicks = []

        def click_then_timeout(locator, **kwargs):
            clicks.append(locator)
            original(locator, **kwargs)
            raise PlaywrightTimeoutError('click took effect at timeout')

        with patch.object(Locator, 'click', click_then_timeout):
            finish_image_upload(self.page, self.page.frame_locator('#mainImagesGroup'), timeout_ms=2000)
        self.assertEqual(len(clicks), 1)
        self.assertTrue(frame.get_by_placeholder('搜索图片名称').is_visible())

    def test_upload_verification_ignores_hidden_old_frame(self):
        self.page.set_content('''<iframe name="old" style="display:none" srcdoc="安全验证"></iframe>
          <div style="display:none">操作过于频繁</div>''')
        self.assertFalse(upload_verification_visible(self.page))
        self.page.locator('iframe').evaluate("n => n.style.display='block'")
        self.assertTrue(upload_verification_visible(self.page))

    def test_images_upload_one_batch_then_select_and_close(self):
        self.page.route('https://img.alicdn.com/**', lambda route: route.fulfill(status=204))
        self.page.set_content('''<input placeholder="最多允许输入30个汉字（60字符）"
          onclick="document.querySelector('iframe').style.display='none'">
          <div id="sell-field-mainImagesGroup"><button class="empty-container"
            onclick="document.querySelector('iframe').style.display='block'">上传</button></div>
          <iframe id="mainImagesGroup" name="pictures" style="display:none;width:900px;height:510px"></iframe>''')
        inner = self.page.frame(name='pictures')
        inner.set_content('''<button onclick="document.querySelector('#upload').style.display='block';document.querySelector('#success').textContent='';document.querySelector('input[type=file]').click()">本地上传</button>
          <div id="upload" style="display:none"><input type="file" multiple onchange="uploaded(this.files)">
            <span id="success"></span><button onclick="finishUpload()">完成</button></div>
          <input placeholder="搜索图片名称"><div id="cards"></div>
          <button onclick="window.allClicks++;document.querySelector('#confirm').textContent='确定（'+names.length+'）'">全选</button>
          <button id="confirm" onclick="insertAll()">确定（0）</button>
          <script>window.batches=[];window.names=[];window.allClicks=0;window.cardClicks=0;
            function insertAll() {for(const current of names) {
              const img=parent.document.createElement('img');img.src='https://img.alicdn.com/'+current;
              parent.document.querySelector('#sell-field-mainImagesGroup').append(img);
            }}
            function uploaded(files) { batches.push(files.length);names=Array.from(files,f=>f.name);
              document.querySelector('#upload').style.display='block';
              document.querySelector('#success').textContent=files.length+' 个文件上传成功'; }
            function finishUpload() {
              document.querySelector('#upload').style.display='none';
              document.querySelector('#cards').replaceChildren();
              for (const current of names) {
              const card=document.createElement('div');card.className='PicList_pic_background';
              const text=document.createElement('span');text.textContent=current;card.append(text);
              card.onclick=()=>window.cardClicks++;
              document.querySelector('#cards').append(card); }
            }</script>''')
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f'{i}.jpg' for i in range(2)]
            for path in paths:
                path.write_bytes(b'image')
            with patch.object(self.page, 'wait_for_timeout', wraps=self.page.wait_for_timeout) as delay:
                urls = upload_main_images(self.page, paths)
            self.assertIn(((3000,), {}), [(c.args, c.kwargs) for c in delay.call_args_list])
        self.assertEqual(inner.evaluate('batches'), [2])
        self.assertEqual(inner.evaluate('[allClicks, cardClicks]'), [1, 0])
        self.assertEqual(len(urls), 2)
        self.assertEqual(self.page.locator('iframe:visible').count(), 0)

    def test_missing_editable_attributes_default_to_none(self):
        self.page.set_content('''<label for="series">系列</label><div id="struct-p-1"><input id="series" required></div>
          <label for="purpose">用途</label><div id="struct-p-2"><input id="purpose" role="combobox" required></div>
          <label for="size">容量</label><div id="struct-p-3"><input id="size" type="number"></div>
          <label for="kind">类型</label><div id="struct-p-4"><select id="kind"><option value="">请选择</option></select></div>''')
        settings = {'brand': '无品牌', 'attributes': {}, 'fill_empty_attributes_with_none': True}
        fields = fill_attributes(self.page, settings)
        self.assertEqual({f['name']: f['value'] for f in fields}, {'系列': '无', '用途': '无', '容量': '', '类型': ''})
        self.assertEqual(settings['defaulted_attributes'], {'系列': '无', '用途': '无'})
        verify_attribute_values(fields, expected_attribute_values(settings))

    def test_none_default_preserves_manual_source_reference_and_existing_values(self):
        names = ['材质', '用途', '系列', '规格说明']
        initial = ['', '', '', '原值']
        self.page.set_content(''.join(f'<label for="f{i}">{name}</label><div id="struct-p-{i}"><input id="f{i}" value="{initial[i]}"></div>'
                                      for i, name in enumerate(names)))
        settings, _ = resolve_attributes({'source_attributes': [{'name': '用途', 'value': '家用'}]}, {
            'brand': '无品牌', 'attributes': {'材质': '棉'}, 'reference_attributes': {'系列': '经典'},
            'fill_empty_attributes_with_none': True})
        fields = fill_attributes(self.page, settings)
        self.assertEqual({f['name']: f['value'] for f in fields}, {'材质': '棉', '用途': '家用', '系列': '经典', '规格说明': '原值'})
        self.assertNotIn('defaulted_attributes', settings)

    def test_none_default_rejected_by_control_stays_empty(self):
        self.page.set_content('''<label for="series">系列</label><div id="struct-p-1">
          <input id="series" role="combobox" required onblur="this.value=''"></div>''')
        settings = {'brand': '无品牌', 'attributes': {}, 'fill_empty_attributes_with_none': True,
                    'allow_missing_attributes': True}
        fields = fill_attributes(self.page, settings)
        self.assertEqual(fields[0]['value'], '')
        self.assertNotIn('defaulted_attributes', settings)

    def test_failed_manual_value_is_not_replaced_by_none(self):
        self.page.set_content('''<label for="series">系列</label><div id="struct-p-1">
          <input id="series" role="combobox" required></div>''')
        settings = {'brand': '无品牌', 'attributes': {'系列': '手动系列'},
                    'attribute_origins': {'系列': '手动配置'}, 'fill_empty_attributes_with_none': True}
        with patch('category_adapter.try_attribute_value', return_value=False):
            fields = fill_attributes(self.page, settings)
        self.assertEqual(fields[0]['value'], '')
        self.assertNotIn('defaulted_attributes', settings)

    def test_reference_form_is_read_without_editing_or_submitting(self):
        url = 'https://item.upload.taobao.com/sell/v2/publish.htm?itemId=1088557871659'
        context = self.browser.new_context()
        self.addCleanup(context.close)
        context.route(url, lambda route: route.fulfill(content_type='text/html; charset=utf-8', body='''
          <label for="pack">外包装种类</label><div id="struct-p-1"><input id="pack" value="散装"></div>
          <button onclick="document.body.dataset.submitted='yes'">提交宝贝信息</button>'''))
        report = read_reference_page(context, url)
        self.assertEqual(report['attributes'], {'外包装种类': '散装'})
        reference = context.pages[-1]
        self.assertEqual(reference.locator('#pack').input_value(), '散装')
        self.assertIsNone(reference.locator('body').get_attribute('data-submitted'))

    def test_reference_shop_link_does_not_supply_form_attributes(self):
        self.assertEqual(reference_kind('https://shop123.taobao.com/'), 'shop')
        with self.assertRaises(ValueError):
            reference_kind('https://example.com/')

    def test_unmatched_source_option_uses_reference_option(self):
        self.page.set_content('''<label for="pack">外包装种类</label><div id="struct-p-1">
          <select id="pack" required><option value="">请选择</option><option value="42">散装</option></select></div>''')
        settings = {'brand': '无品牌', 'attributes': {'外包装种类': '食用农产品'},
                    'reference_attributes': {'外包装种类': '散装'}}
        fields = fill_attributes(self.page, settings)
        self.assertEqual(fields[0]['value'], '散装')
        self.assertEqual(expected_attribute_values(settings)['外包装种类'], '散装')
        self.assertEqual(settings['reference_fallbacks']['外包装种类']['original_value'], '食用农产品')

    def test_unmatched_reference_also_leaves_empty(self):
        self.page.set_content('''<label for="pack">外包装种类</label><div id="struct-p-1">
          <select id="pack" required><option value="">请选择</option></select></div>''')
        settings = {'brand': '无品牌', 'attributes': {'外包装种类': '食用农产品'},
                    'reference_attributes': {'外包装种类': '散装'}}
        fields = fill_attributes(self.page, settings)
        self.assertEqual(fields[0]['value'], '')
        self.assertIn('外包装种类', settings['skipped_attributes'])

    def test_late_source_parameters_are_collected_without_fixed_names(self):
        self.page.set_content('''<div style="height:3000px"></div><script>
          window.__ICE_APP_CONTEXT__ = {loaderData:{home:{data:{res:{item:{itemId:'123'}}}}}};
          setTimeout(() => {
            const section = document.createElement('div'); section.className = 'paramsInfoArea_new';
            section.innerHTML = `<div class="emphasisParamsInfoItem_new">
              <span class="emphasisParamsInfoItemTitle_new">纯棉</span>
              <span class="emphasisParamsInfoItemSubTitle_new">材质</span></div>`;
            document.body.append(section);
          }, 200);
          setTimeout(() => document.querySelector('.paramsInfoArea_new').insertAdjacentHTML('beforeend',
            `<div class="generalParamsInfoItem_new"><span class="generalParamsInfoItemTitle_new">适用季节</span>
              <span class="generalParamsInfoItemSubTitle_new">春秋</span></div>`), 500);
          </script>''')
        values = collect_source_attributes(self.page, '123', timeout_ms=3000, stable_ms=600)
        self.assertEqual({a['name']: a['value'] for a in values}, {'材质': '纯棉', '适用季节': '春秋'})
        self.assertEqual(self.page.evaluate('scrollY'), 0)

    def test_generic_required_fields_fill_from_source_and_native_select(self):
        self.page.set_content('''<label for="material">材质</label><div id="struct-p-1">
          <select id="material" required><option value="">请选择</option><option value="42">纯棉</option></select></div>
          <label for="capacity">容量</label><div id="struct-p-2"><input id="capacity" required></div>''')
        settings, _ = resolve_attributes({'source_attributes': [
            {'name': '材质', 'value': '纯棉'}, {'name': '容量', 'value': '500毫升'}]},
            {'brand': '无品牌', 'attributes': {}})
        result = fill_attributes(self.page, settings)
        self.assertEqual({f['name']: f['value'] for f in result}, {'材质': '纯棉', '容量': '500毫升'})

    def test_image_picker_ignores_hidden_iframe_with_same_id(self):
        self.page.set_content('''<iframe id="mainImagesGroup" name="old" style="display:none"></iframe>
          <iframe id="mainImagesGroup" name="current"></iframe>''')
        for name in ['old', 'current']:
            self.page.frame(name=name).set_content('<input placeholder="搜索图片名称">')
        active_main_image_frame(self.page).get_by_placeholder('搜索图片名称').fill('new.jpg')
        self.assertEqual(self.page.frame(name='old').locator('input').input_value(), '')
        self.assertEqual(self.page.frame(name='current').locator('input').input_value(), 'new.jpg')

    def test_image_picker_waits_for_overlap_to_end(self):
        self.page.set_content('''<iframe id="mainImagesGroup" name="old"></iframe>
          <iframe id="mainImagesGroup" name="current" srcdoc='<input placeholder="搜索图片名称">'></iframe>
          <script>setTimeout(() => document.querySelector('[name=old]').style.display='none', 250)</script>''')
        active_main_image_frame(self.page, timeout_ms=2000).get_by_placeholder('搜索图片名称').fill('new.jpg')
        self.assertEqual(self.page.frame(name='current').locator('input').input_value(), 'new.jpg')

    def test_image_picker_rejects_multiple_visible_frames(self):
        self.page.set_content('<iframe id="mainImagesGroup"></iframe><iframe id="mainImagesGroup"></iframe>')
        with self.assertRaisesRegex(ValueError, '一个可见窗口'):
            active_main_image_frame(self.page, timeout_ms=300)

    def test_image_picker_close_then_reopen_retains_hidden_frames(self):
        self.page.set_content('''<input placeholder="最多允许输入30个汉字（60字符）"
          onclick="setTimeout(() => document.querySelectorAll('iframe').forEach(n => n.style.display='none'), 200)">
          <iframe id="mainImagesGroup" name="old"></iframe>''')
        close_main_image_picker(self.page, timeout_ms=2000)
        self.page.evaluate('''() => {
          const frame = document.createElement('iframe'); frame.id = 'mainImagesGroup';
          frame.name = 'new'; frame.srcdoc = '<input placeholder="搜索图片名称">';
          document.body.append(frame);
        }''')
        active_main_image_frame(self.page).get_by_placeholder('搜索图片名称').fill('second.jpg')
        self.assertEqual(self.page.locator('iframe#mainImagesGroup').count(), 2)
        self.assertEqual(self.page.frame(name='new').locator('input').input_value(), 'second.jpg')

    def test_image_picker_does_not_reopen_when_close_failed(self):
        self.page.set_content('''<input placeholder="最多允许输入30个汉字（60字符）">
          <iframe id="mainImagesGroup"></iframe>''')
        with self.assertRaisesRegex(ValueError, '未关闭'):
            close_main_image_picker(self.page, timeout_ms=300)

    def test_main_image_upload_names_are_unique_per_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f'main-{i}.jpg' for i in range(2)]
            for i, path in enumerate(paths):
                path.write_bytes(f'image-{i}'.encode())
            first, second = main_image_uploads(paths), main_image_uploads(paths)
            self.assertEqual(len({p['name'] for p in first + second}), 4)
            self.assertEqual([p['buffer'] for p in first], [p.read_bytes() for p in paths])
            self.assertTrue(all(p['mimeType'] == 'image/jpeg' for p in first))

    def test_image_card_click_handles_overlay_and_list_refresh(self):
        self.page.set_content('<iframe id="mainImagesGroup" name="pictures"></iframe>')
        inner = self.page.frame(name='pictures')
        inner.set_content('''<style>.PicList_pic_background {
            width:180px;height:120px;position:relative;margin:5px}
            .cover {position:absolute;inset:0}</style>
            <div class="PicList_pic_background"><img><span>old.jpg</span></div>
            <div class="PicList_pic_background"><img><span>old.jpg</span></div>
            <div id="current" class="PicList_pic_background" onclick="document.body.dataset.chosen='new'">
              <img><span>new-batch.jpg</span><div class="cover"></div></div>''')
        original = Locator.click
        refreshed = False

        def refresh_then_click(locator, **kwargs):
            nonlocal refreshed
            if not refreshed:
                refreshed = True
                inner.locator('#current').evaluate('n => n.outerHTML = n.outerHTML')
            return original(locator, **kwargs)

        with patch.object(Locator, 'click', refresh_then_click):
            choose_image_card(self.page.frame_locator('#mainImagesGroup'), 'new-batch.jpg', timeout_ms=2000)
        self.assertEqual(inner.locator('body').get_attribute('data-chosen'), 'new')

    def test_image_card_does_not_choose_arbitrary_duplicate(self):
        self.page.set_content('''<div class="PicList_pic_background" onclick="document.body.dataset.chosen='yes'"><span>same.jpg</span></div>
          <div class="PicList_pic_background" onclick="document.body.dataset.chosen='yes'"><span>same.jpg</span></div>''')
        with self.assertRaises(AssertionError):
            choose_image_card(self.page, 'same.jpg', timeout_ms=300)
        self.assertIsNone(self.page.locator('body').get_attribute('data-chosen'))

    def test_category_next_uses_real_window_instead_of_oversized_viewport(self):
        self.page.set_viewport_size({'width': 1600, 'height': 1400})
        self.page.set_content('''<style>html,body {overflow:hidden}
          footer {position:fixed;right:0;bottom:0;width:180px}</style>
          <footer><button onclick="document.body.dataset.next='yes'">确认，下一步</button></footer>''')
        confirm_category(self.page, timeout_ms=2000)
        self.assertEqual(self.page.locator('body').get_attribute('data-next'), 'yes')
        size = self.page.evaluate('() => ({inner:innerHeight, outer:outerHeight})')
        self.assertLessEqual(size['inner'], size['outer'])

    def test_publish_footer_stays_inside_physical_window(self):
        self.page.set_viewport_size({'width': 1600, 'height': 1400})
        self.page.set_content('''<div style="height:4000px">发布内容</div>
          <footer style="position:fixed;bottom:0;left:0">
          <button>提交宝贝信息</button><button>保存草稿</button></footer>''')
        ensure_category_viewport(self.page)
        for text in ('提交宝贝信息', '保存草稿'):
            box = self.page.get_by_role('button', name=text).bounding_box()
            self.assertGreaterEqual(box['y'], 0)
            self.assertLessEqual(box['y'] + box['height'], self.page.evaluate('innerHeight'))
        self.assertLessEqual(self.page.evaluate('innerHeight'), self.page.evaluate('outerHeight'))

    def test_category_next_does_not_bypass_disabled_button(self):
        self.page.set_content('''<button disabled onclick="document.body.dataset.next='yes'">确认，下一步</button>''')
        with self.assertRaises(PlaywrightTimeoutError):
            confirm_category(self.page, timeout_ms=300)
        self.assertIsNone(self.page.locator('body').get_attribute('data-next'))

    def test_category_next_scrolls_nested_container(self):
        self.page.set_content('''<div style="height:180px;overflow:auto">
          <div style="height:2000px"></div>
          <button onclick="document.body.dataset.next='yes'">确认，下一步</button></div>''')
        confirm_category(self.page, timeout_ms=2000)
        self.assertEqual(self.page.locator('body').get_attribute('data-next'), 'yes')

    def test_delayed_fields_and_selected_value(self):
        self.page.set_content('''<script>setTimeout(()=>document.body.innerHTML=`
        <div class="sell-component-info-wrapper-wrap">
         <span class="sell-component-info-wrapper-label">产地</span>
         <span class="sell-component-info-wrapper-required">*</span>
         <div id="struct-p-1"><span class="next-select-values"><em>中国大陆</em></span><input value=""></div>
        </div>`,300)</script>''')
        fields = wait_for_property_fields(self.page, timeout_ms=3000, stable_ms=200)
        self.assertEqual(fields[0]['name'], '产地')
        self.assertEqual(fields[0]['value'], '中国大陆')
        self.assertTrue(fields[0]['required'])

    def test_label_associations_and_unchecked_checkbox(self):
        self.page.set_content('''<label for="variety">品种：</label>
        <div id="struct-p-1"><input id="variety" required></div>
        <span id="label2">包装方式</span><div id="struct-p-2"><input aria-labelledby="label2" type="checkbox" value="盒装"></div>''')
        fields = property_fields(self.page)
        self.assertEqual([f['name'] for f in fields], ['品种', '包装方式'])
        self.assertEqual(fields[1]['value'], '')

    def test_unnamed_field_does_not_pass(self):
        self.page.set_content('<div id="struct-p-9"><input></div>')
        with self.assertRaisesRegex(ValueError, '无法识别名称：struct-p-9'):
            wait_for_property_fields(self.page, timeout_ms=400)

    def test_empty_page_does_not_pass(self):
        self.page.set_content('<p>加载中</p>')
        with self.assertRaisesRegex(ValueError, '未发现可见属性控件'):
            wait_for_property_fields(self.page, timeout_ms=400)

    def test_chooser_known_ids_without_editor_labels(self):
        self.page.route('**/*', lambda route: route.fulfill(body='''
            <div id="struct-p-21299"><input></div>
            <div id="struct-p-6939376"><input></div>''', content_type='text/html'))
        self.page.goto('https://item.upload.taobao.com/sell/ai/category.htm')
        fields = wait_for_property_fields(self.page, timeout_ms=2000, stable_ms=200)
        self.assertEqual([f['name'] for f in fields], ['产地', '包装方式'])
        self.assertTrue(all(f['required'] for f in fields))
        with self.assertRaisesRegex(ValueError, '请在类目属性中补充：产地、包装方式'):
            fill_attributes(self.page, {'brand': '无品牌', 'attributes': {}})

    def test_chooser_fallback_does_not_apply_to_other_pages(self):
        self.page.set_content('<div id="struct-p-21299"><input></div>')
        self.assertEqual(property_fields(self.page)[0]['name'], '')

    def test_missing_required_is_still_blocked(self):
        self.page.set_content('<label for="kind">包装方式</label><div id="struct-p-1"><input id="kind" required></div>')
        with self.assertRaisesRegex(ValueError, '请在类目属性中补充：包装方式'):
            fill_attributes(self.page, {'brand':'无品牌','attributes':{}})

    def test_user_approved_empty_attributes_are_skipped(self):
        self.page.set_content(''.join(
            f'<label for="field{i}">{name}</label><div id="struct-p-{i}"><input id="field{i}" required></div>'
            for i, name in enumerate(['材质', '适用空间', '功能'])))
        fields = fill_attributes(self.page, {'brand': '无品牌', 'attributes': {}, 'allow_missing_attributes': True})
        self.assertEqual(len(fields), 3)
        self.assertTrue(all(f['value'] == '' for f in fields))

    def test_selected_origin_is_not_reopened_but_missing_fields_still_block(self):
        self.page.set_content('''
          <label for="origin">产地</label><div id="struct-p-21299">
            <span class="next-select-values"><em>中国大陆</em></span>
            <input id="origin" role="combobox" required onclick="document.body.dataset.opened='yes'">
          </div>
          <label for="kind">包装方式</label><div id="struct-p-2"><input id="kind" required></div>''')
        with self.assertRaisesRegex(ValueError, '请在类目属性中补充：包装方式'):
            fill_attributes(self.page, {'brand': '无品牌', 'attributes': {'产地': '中国大陆'}})
        self.assertIsNone(self.page.locator('body').get_attribute('data-opened'))

    def test_different_existing_value_is_updated(self):
        self.page.set_content('''<label for="kind">品种</label>
            <div id="struct-p-2"><input id="kind" value="旧品种" required></div>''')
        fields = fill_attributes(self.page, {'brand': '无品牌', 'attributes': {'品种': '新品种'}})
        self.assertEqual(fields[0]['value'], '新品种')

    def test_brand_option_ignores_duplicate_labels_and_partial_matches(self):
        self.page.set_content('''<span title="无品牌">无品牌</span><p>无品牌</p>
          <div class="options-item" onclick="document.body.dataset.selected='exact'"><span>无品牌</span></div>
          <div class="options-item" onclick="document.body.dataset.selected='partial'">无品牌/无注册商标</div>''')
        select_virtual_option(self.page, '无品牌')
        self.assertEqual(self.page.locator('body').get_attribute('data-selected'), 'exact')

    def test_missing_option_does_not_click_matching_page_text(self):
        self.page.set_content('''<span title="无品牌" onclick="document.body.dataset.clicked='yes'">无品牌</span>
            <div role="option">其他品牌</div>''')
        with self.assertRaisesRegex(ValueError, '未找到唯一选项'):
            select_virtual_option(self.page, '无品牌', timeout_ms=600)
        self.assertIsNone(self.page.locator('body').get_attribute('data-clicked'))

    def test_origin_list_shrinks_after_snapshot(self):
        self.page.set_content('<div id="options">' + ''.join(
            f'<div role="option">旧选项{i}</div>' for i in range(12)) + '</div>')
        original = Locator.element_handles
        refreshed = False

        def snapshot_then_refresh(locator):
            nonlocal refreshed
            rows = original(locator)
            if not refreshed:
                refreshed = True
                self.page.locator('#options').evaluate('''n => n.innerHTML =
                  `<div role="option" onclick="document.body.dataset.selected='中国大陆'">中国大陆</div>`''')
            return rows

        with patch.object(Locator, 'element_handles', snapshot_then_refresh):
            select_virtual_option(self.page, '中国大陆', timeout_ms=2000)
        self.assertTrue(refreshed)
        self.assertEqual(self.page.locator('body').get_attribute('data-selected'), '中国大陆')

    def test_origin_option_replaced_before_click(self):
        self.page.set_content('''<div role="option" title="中国大陆">产地选项</div>''')
        original = ElementHandle.click
        refreshed = False

        def click_after_refresh(handle, **kwargs):
            nonlocal refreshed
            if not refreshed:
                refreshed = True
                self.page.locator('[role=option]').evaluate('''n => n.outerHTML =
                  `<div role="option" title="中国大陆" onclick="document.body.dataset.selected='中国大陆'">产地选项</div>`''')
            return original(handle, **kwargs)

        with patch.object(ElementHandle, 'click', click_after_refresh):
            select_virtual_option(self.page, '中国大陆', timeout_ms=2000)
        self.assertEqual(self.page.locator('body').get_attribute('data-selected'), '中国大陆')

    def test_origin_search_results_arrive_after_old_options(self):
        self.page.set_content('''<div id="options"><div role="option">其他产地</div></div>
          <script>setTimeout(() => document.querySelector('#options').innerHTML =
          `<div role="option" onclick="document.body.dataset.selected='中国大陆'">中国大陆</div>`, 500)</script>''')
        select_virtual_option(self.page, '中国大陆', timeout_ms=2000)
        self.assertEqual(self.page.locator('body').get_attribute('data-selected'), '中国大陆')

    def test_virtual_list_does_not_skip_middle_options(self):
        self.page.set_content('''<div id="list" style="height:100px;overflow-y:auto">
          <div style="height:1000px"><div role="option">开始</div></div></div>
          <script>document.querySelector('#list').onscroll = e => {
            if (e.target.scrollTop > 0 && e.target.scrollTop < 200) {
              e.target.firstElementChild.innerHTML =
              `<div role="option" style="position:sticky;top:0" onclick="document.body.dataset.selected='广西'">广西</div>`;
            }
          }</script>''')
        select_virtual_option(self.page, '广西', timeout_ms=2000)
        self.assertEqual(self.page.locator('body').get_attribute('data-selected'), '广西')

    def test_duplicate_options_are_rejected(self):
        self.page.set_content('<div role="option">中国大陆</div><div role="option">中国大陆</div>')
        with self.assertRaisesRegex(ValueError, '存在多个同名选项'):
            select_virtual_option(self.page, '中国大陆', timeout_ms=1000)

    def test_other_dropdowns_refresh_and_ignore_hidden_options(self):
        for value, css_class, role in [
            ('无品牌', 'options-item', ''), ('广西壮族自治区', 'options-item', ''),
            ('梧州市', 'next-menu-item', ''), ('食用农产品', 'options-item', ''),
            ('散装', 'next-menu-item', ''), ('包邮', '', 'option'),
        ]:
            with self.subTest(value=value):
                self.page.set_content('''<div id="options"><div role="option">加载中</div></div>''')
                self.page.evaluate('''({value, cssClass, role}) => setTimeout(() => {
                  const box = document.querySelector('#options');
                  box.innerHTML = '';
                  for (const hidden of [true, false]) {
                    const row = document.createElement('div');
                    row.className = cssClass;
                    if (role) row.setAttribute('role', role);
                    if (hidden) row.style.display = 'none';
                    const label = document.createElement('span');
                    label.title = value;
                    label.textContent = value;
                    row.append(label, document.createTextNode(' ›'));
                    row.onclick = () => document.body.dataset.selected = value;
                    box.append(row);
                  }
                }, 200)''', {'value': value, 'cssClass': css_class, 'role': role})
                select_virtual_option(self.page, value, timeout_ms=2000)
                self.assertEqual(self.page.locator('body').get_attribute('data-selected'), value)

    def test_shipping_cascade_and_template_refresh(self):
        self.page.set_content('''
          <label><input type="radio">大陆及港澳台</label>
          <label><input type="radio">单一发货地</label>
          <div id="struct-shippingArea"><input role="combobox" onclick="showProvince()"><span id="region"></span></div>
          <div id="struct-tbExtractWay">
            <label><input type="checkbox">使用物流配送</label>
            <input role="combobox" oninput="showTemplate()"><span id="template"></span>
          </div><div id="options"></div>
          <script>
          function option(value, action) {
            const row = document.createElement('div'); row.setAttribute('role', 'option');
            row.innerHTML = `<span title="${value}">${value}</span> ›`;
            row.onclick = action; document.querySelector('#options').replaceChildren(row);
          }
          function showProvince() { option('广西', () => {
            document.querySelector('#options').replaceChildren();
            setTimeout(() => option('梧州', () => {
              document.querySelector('#region').textContent = '广西 / 梧州';
              document.querySelector('#options').replaceChildren();
            }), 250);
          }); }
          function showTemplate() {
            option('旧模板', () => document.body.dataset.wrong = 'yes');
            setTimeout(() => option('包邮', () => {
              document.querySelector('#template').textContent = '包邮';
              document.querySelector('#options').replaceChildren();
            }), 250);
          }
          </script>''')
        fill_shipping(self.page, {'shipping': {
            'shipping_origin': {'province': '广西', 'city': '梧州'},
            'shipping_template': '包邮', 'carrier': '普通快递'}})
        self.assertEqual(self.page.locator('#region').inner_text(), '广西 / 梧州')
        self.assertEqual(self.page.locator('#template').inner_text(), '包邮')
        self.assertIsNone(self.page.locator('body').get_attribute('data-wrong'))

    def test_search_text_is_not_a_selected_attribute(self):
        self.page.set_content('''<label for="brand">品牌</label><div id="struct-p-20000">
            <input id="brand" role="combobox"><div class="options-search"><input value="无品牌"></div></div>''')
        self.assertEqual(property_fields(self.page)[0]['value'], '')
        with patch('category_adapter.select_virtual_option'):
            with self.assertRaisesRegex(ValueError, '属性回读不一致：品牌'):
                fill_attributes(self.page, {'brand': '无品牌', 'attributes': {}})

    def test_duplicate_rendered_selected_values_are_one_value(self):
        self.page.set_content('''<label for="origin">产地</label><div id="struct-p-21299">
            <input id="origin" value="中国大陆"><span class="next-select-values"><em>中国大陆</em></span></div>''')
        self.assertEqual(property_fields(self.page)[0]['value'], '中国大陆')

    def test_editable_combo_accepts_direct_entry_when_option_missing(self):
        self.page.set_content('''<label for="brand">品牌</label><div id="struct-p-20000">
            <input id="brand" role="combobox" aria-autocomplete="none"></div>''')
        settings = {'brand': '无品牌', 'attributes': {}}
        with patch('category_adapter.select_virtual_option', side_effect=ValueError('missing')):
            fill_attributes(self.page, settings)
        self.assertEqual(self.page.locator('#brand').input_value(), '无品牌')
        self.assertEqual(expected_attribute_values(settings)['品牌'], '无品牌')
        self.assertNotIn('品牌', settings.get('skipped_attributes', {}))

    def test_unmatched_required_dropdown_is_empty_and_other_fields_continue(self):
        self.page.set_content('''<label for="pack">外包装种类</label><div id="struct-p-1">
          <input id="pack" role="combobox" required readonly>
          <div class="options-search"><span class="next-input"><input value="搜索残留"></span></div></div>
          <label for="material">材质</label><div id="struct-p-2"><input id="material" required></div>''')
        settings = {'brand': '无品牌', 'attributes': {'外包装种类': '食用农产品', '材质': '纯棉'}}
        with patch('category_adapter.select_virtual_option', side_effect=ValueError('missing')):
            fields = fill_attributes(self.page, settings)
        self.assertEqual({f['name']: f['value'] for f in fields}, {'外包装种类': '', '材质': '纯棉'})
        self.assertEqual(self.page.locator('.options-search input').input_value(), '')
        self.assertEqual(settings['skipped_attributes']['外包装种类']['requested_value'], '食用农产品')
        verify_attribute_values(fields, expected_attribute_values(settings))
        self.page.locator('#pack').evaluate("n => n.value = '不应保留的值'")
        with self.assertRaisesRegex(ValueError, '属性回读不一致'):
            verify_attribute_values(property_fields(self.page), expected_attribute_values(settings))

    def test_custom_value_rejected_on_blur_is_not_claimed_as_filled(self):
        self.page.set_content('''<label for="series">系列</label><div id="struct-p-1">
          <input id="series" role="combobox" required onblur="this.value=''"></div>''')
        settings = {'brand': '无品牌', 'attributes': {'系列': '新系列'}}
        with patch('category_adapter.select_virtual_option', side_effect=ValueError('missing')):
            fields = fill_attributes(self.page, settings)
        self.assertEqual(fields[0]['value'], '')
        self.assertIn('系列', settings['skipped_attributes'])

    def test_existing_dropdown_option_is_preferred_to_direct_input(self):
        self.page.set_content('''<label for="kind">品种</label><div id="struct-p-1">
          <input id="kind" role="combobox" required></div>
          <div role="option" onclick="document.querySelector('#kind').value='黄心木薯';document.body.dataset.selected='yes'">黄心木薯</div>''')
        settings = {'brand': '无品牌', 'attributes': {'品种': '黄心木薯'}}
        fill_attributes(self.page, settings)
        self.assertEqual(self.page.locator('body').get_attribute('data-selected'), 'yes')

    def test_unmatched_native_dropdown_clears_previous_selection(self):
        self.page.set_content('''<label for="kind">适用类型</label><div id="struct-p-1">
          <select id="kind" required><option value="1">旧值</option></select></div>''')
        settings = {'brand': '无品牌', 'attributes': {'适用类型': '不存在的值'}}
        fields = fill_attributes(self.page, settings)
        self.assertEqual(fields[0]['value'], '')
        self.assertIn('适用类型', settings['skipped_attributes'])

    def test_sku_readback_rejects_wrong_stock(self):
        self.page.set_content('''<table><tr class="sku-table-row"><td>
            <textarea data-real="true">规格一</textarea>
            <div id="x-skuPrice"><input value="12.00"></div>
            <div id="x-skuStock"><input value="1"></div></td></tr></table>''')
        plan = {'skus': [{'values': [{'value': '规格一'}], 'price': '12.00', 'target_stock': 88}]}
        with self.assertRaisesRegex(ValueError, 'SKU 回读不一致'):
            verify_skus(self.page, plan)
        self.page.locator('[id$="-skuStock"] input').fill('88')
        self.assertEqual(verify_skus(self.page, plan)[0]['stock'], '88')
