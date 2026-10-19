"""Check the committed community designs and their generated import packages."""
import importlib.util
import io
import json
from pathlib import Path
import re
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('publisher', ROOT / 'scripts' / 'publish.py')
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class CommunityTemplateTests(unittest.TestCase):
    def test_all_templates_have_reviewed_classifications(self):
        items = publisher.scan_templates(ROOT)
        self.assertEqual(len(items), 68)
        by_folder = {item['folder']: item['classification'] for item in items}
        self.assertEqual(by_folder['柔雾居中标题']['artworkType'], 'none')
        self.assertEqual(by_folder['横幅背景海报列']['artworkType'], 'mixed')
        self.assertEqual(by_folder['合集画册索引']['artworkType'], 'poster')
        self.assertEqual(by_folder['横景竖封双幕留白']['artworkType'], 'backdrop')
        self.assertEqual(by_folder['横景竖封双幕留白']['artworkCount'], 2)
        self.assertEqual(by_folder['横版合集放映时间轴']['artworkType'], 'backdrop')
        for item in items:
            self.assertTrue(item['classification']['recommendedUses'])
            self.assertTrue(item['classification']['tags'])

    def test_community_designs_keep_editable_bindings_and_portable_packages(self):
        items = publisher.scan_templates(ROOT)
        community = [item for item in items if item['name'].startswith(('精选 · ', '试作 · ', '深色试作 · '))]
        self.assertTrue(community, 'The committed community collection must be checked')
        for item in community:
            with self.subTest(template=item['folder']):
                data = json.loads(item['templatePath'].read_text(encoding='utf-8'))
                canvas = json.loads(data['canvasJson'])
                self.assertEqual((canvas['width'], canvas['height']), (data['width'], data['height']))
                objects = canvas['objects']
                ids = [obj['id'] for obj in objects]
                self.assertEqual(len(ids), len(set(ids)), 'Layer IDs must remain unique')
                bindings = {obj.get('textVar') for obj in objects}
                self.assertTrue({'{{primary_title}}', '{{secondary_title}}'} <= bindings)
                posters = [obj for obj in objects if obj.get('posterVar')]
                self.assertTrue(posters or canvas.get('posterBackground', {}).get('source') == 'backdrop',
                                'Community designs must retain editable poster or backdrop bindings')
                for poster in posters:
                    self.assertIsNotNone(re.fullmatch(r'post[1-9]', poster['posterVar']))
                    self.assertFalse(poster.get('clipPath', {}).get('absolutePositioned', False),
                                     'Decorative masks must move with their poster')
                with zipfile.ZipFile(io.BytesIO(publisher.make_package(item))) as archive:
                    self.assertEqual(json.loads(archive.read('template.json')), data)
                    self.assertEqual(archive.read(item['previewFile']), item['previewPath'].read_bytes())


if __name__ == '__main__':
    unittest.main()
