import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image
from playwright.sync_api import sync_playwright
from draft_product import detail_assets, upload_detail_images, new_detail_image_urls


class DetailAssetTests(unittest.TestCase):
    def test_existing_images_cannot_count_as_new_uploads(self):
        area = MagicMock()
        area.locator.return_value.evaluate_all.return_value = ['icon', 'old', 'new']
        self.assertEqual(new_detail_image_urls(area, ['icon', 'old']), ['new'])
        area.locator.return_value.evaluate_all.return_value = ['icon', 'old']
        self.assertEqual(new_detail_image_urls(area, ['icon', 'old']), [])

    def test_duplicate_urls_are_counted_by_occurrence(self):
        area = MagicMock()
        area.locator.return_value.evaluate_all.return_value = ['same', 'same']
        self.assertEqual(new_detail_image_urls(area, ['same']), ['same'])

    def test_uses_detail_images_in_source_order_and_preserves_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new('RGB', (20, 100)).save(root / 'detail.png')
            Image.new('RGB', (10, 10)).save(root / 'main.png')
            manifest = [{'group': 'main', 'index': 1, 'url': 'main', 'file': 'main.png'},
                        {'group': 'detail', 'index': 2, 'url': 'second', 'file': 'detail.png'},
                        {'group': 'detail', 'index': 1, 'url': 'first', 'file': 'detail.png'}]
            (root / 'images.json').write_text(json.dumps(manifest))
            paths = detail_assets({'detail_images': ['first', 'second']}, root)
            self.assertEqual([p.name for p in paths], ['detail-001.jpg', 'detail-002.jpg'])
            for path in paths:
                with Image.open(path) as image:
                    self.assertEqual(image.size, (20, 100))
            with self.assertRaisesRegex(ValueError, '清单不完整'):
                detail_assets({'detail_images': ['first', 'second', 'missing']}, root)

    def test_no_detail_images_does_not_fall_back_to_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'images.json').write_text('[]')
            with self.assertRaisesRegex(ValueError, '清单不完整'):
                detail_assets({'detail_images': []}, root)


class DetailUploadDomTests(unittest.TestCase):
    def test_uploads_one_batch_before_confirming_and_closing(self):
        with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page()
                page.set_content('''<div id="panel_edit"><button id="open">图片</button></div>
                  <div id="struct-descRepublicOfSell"><img id="existing" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///w=="></div><div role="dialog" hidden><iframe></iframe></div>''')
                inner = '''<input id="file" type="file" multiple hidden>
                  <button onclick="file.click()">本地上传</button><div id="success"></div>
                  <button id="finish" hidden>完成</button>
                  <input id="search" placeholder="搜索图片名称" hidden>
                  <div id="cards"></div><button id="all">全选</button>
                  <button id="confirm" disabled>确定（0）</button>
                  <script>
                    const confirm = document.querySelector('#confirm');
                    const selected=[]; window.batches=[];
                    file.onchange=()=>{batches.push(file.files.length);success.textContent=file.files.length+' 个文件上传成功';finish.hidden=false;};
                    finish.onclick=()=>{finish.hidden=true;search.hidden=false;};
                    window.allClicks=0;window.cardClicks=0;
                    search.onkeydown=e=>{if(e.key==='Enter') {
                      cards.replaceChildren();
                      for(const f of Array.from(file.files).filter(f=>f.name.startsWith(search.value))) {
                        const card=document.createElement('div');card.className='PicList_pic_background';card.textContent=f.name;
                        card.onclick=()=>window.cardClicks++;cards.append(card);
                      }
                    }};
                    document.querySelector('#all').onclick=()=>{
                      allClicks++;selected.push(...Array.from(cards.children,n=>n.textContent));
                      confirm.disabled=false;confirm.textContent='确定（'+selected.length+'）';
                    };
                    confirm.onclick=()=>{
                      const p=parent.document;
                      for(const name of selected) {const img=p.createElement('img');
                      img.src='data:image/png;base64,iVBORw0KGgo=';
                      img.alt=name;
                      p.querySelector('#struct-descRepublicOfSell').append(img);}
                      p.querySelector('[role=dialog]').hidden=true;
                    };
                  </script>'''
                page.evaluate('''markup => {
                  document.querySelector('#open').onclick=()=>{
                    document.querySelector('[role=dialog]').hidden=false;
                    document.querySelector('iframe').srcdoc=markup;
                  };
                }''', inner)
                paths = [Path(tmp) / f'{n}.jpg' for n in (1, 2)]
                for path in paths:
                    Image.new('RGB', (10, 30)).save(path)
                with patch.object(page, 'wait_for_timeout', side_effect=lambda ms: None):
                    urls = upload_detail_images(page, paths)
                self.assertEqual(len(urls), 2)
                self.assertEqual(page.locator('#existing').count(), 1)
                names = page.locator('#struct-descRepublicOfSell img:not(#existing)').evaluate_all('ns=>ns.map(n=>n.alt)')
                self.assertEqual(len(set(names)), 2)
                self.assertEqual(page.get_by_role('dialog').count(), 0)
                self.assertEqual(page.frames[1].evaluate('batches'), [2])
                self.assertEqual(page.frames[1].evaluate('[allClicks, cardClicks]'), [1, 0])
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
