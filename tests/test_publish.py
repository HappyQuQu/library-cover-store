import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('publisher', Path(__file__).parents[1] / 'scripts' / 'publish.py')
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
REPO = 'HappyQuQu/library-cover-store'
SHA = 'a' * 40


class FakeGitHub:
    repository = REPO

    def __init__(self):
        self.releases, self.catalog, self.uploads = [], None, 0

    def read_catalog(self):
        return self.catalog, 'previous' if self.catalog else None

    def pages(self, path):
        if path == '/releases':
            return self.releases
        release_id = int(path.split('/')[2])
        return next(release['assets'][:] for release in self.releases if release['id'] == release_id)

    def request(self, path, method='GET', data=None, optional=False, binary=False):
        if path == '/releases':
            release = {**data, 'id': len(self.releases) + 1, 'assets': []}
            self.releases.append(release)
            return release
        release_id = int(path.split('/')[2])
        from urllib.parse import parse_qs, urlparse
        name = parse_qs(urlparse(path).query)['name'][0]
        release = next(release for release in self.releases if release['id'] == release_id)
        asset = {'name': name, 'state': 'uploaded', 'download_count': 0,
                 'digest': f'sha256:{publisher.digest(data)}',
                 'browser_download_url': f"https://github.com/{REPO}/releases/download/{release['tag_name']}/{name}"}
        release['assets'].append(asset)
        self.uploads += 1
        return asset

    def write_catalog(self, catalog, previous_sha, source_sha):
        self.catalog = catalog


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def template(self, folder='胶片放映', name='胶片放映'):
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        data = {'name': name, 'description': None, 'width': 1920, 'height': 1080,
                'canvasJson': json.dumps({'objects': []}), 'isPublic': False}
        (directory / 'template.json').write_text(json.dumps(data, ensure_ascii=False))
        (directory / 'preview.jpg').write_bytes(b'\xff\xd8\xfftest-preview')
        return directory

    def run_publish(self, client, limit=100):
        return publisher.publish(publisher.scan_templates(self.root), client, SHA, limit, pause=lambda seconds: None)

    def test_package_matches_export_and_is_reproducible(self):
        self.template()
        item = publisher.scan_templates(self.root)[0]
        self.assertEqual(item['description'], '')
        archive = publisher.make_package(item)
        self.assertEqual(archive, publisher.make_package(item))
        with zipfile.ZipFile(io.BytesIO(archive)) as zip_file:
            self.assertEqual(zip_file.namelist(), ['template.json', 'preview.jpg'])
            self.assertEqual(json.loads(zip_file.read('template.json'))['name'], '胶片放映')

    def test_incremental_publish_retains_old_version_counts_and_skips_packaging_unchanged(self):
        directory = self.template()
        client = FakeGitHub()
        first = self.run_publish(client)
        client.releases[0]['assets'][0]['download_count'] = 17
        with patch.object(publisher, 'make_package', side_effect=AssertionError('Unchanged templates must not be repackaged')):
            second = self.run_publish(client)
        self.assertEqual(client.uploads, 1)
        self.assertEqual(second['templates'][0]['downloadCount'], 17)
        self.assertEqual(second['templates'][0]['downloadSources'], [{'releaseId': client.releases[0]['id'], 'downloadCount': 17}])
        (directory / 'preview.jpg').write_bytes(b'\xff\xd8\xffupdated')
        third = self.run_publish(client)
        self.assertEqual(client.uploads, 2)
        self.assertEqual(third['templates'][0]['id'], first['templates'][0]['id'])
        self.assertNotEqual(third['templates'][0]['revision'], first['templates'][0]['revision'])
        client.releases[0]['assets'][1]['download_count'] = 3
        self.assertEqual(self.run_publish(client)['templates'][0]['downloadCount'], 20)

    def test_rollover_and_queue_resume(self):
        self.template('一')
        self.template('二')
        client = FakeGitHub()
        with patch.object(publisher, 'BATCH_SIZE', 1):
            first = self.run_publish(client, limit=1)
            self.assertEqual(first['pendingCount'], 1)
            self.assertEqual(len(first['templates']), 1)
            second = self.run_publish(client, limit=1)
        self.assertEqual(second['pendingCount'], 0)
        self.assertEqual(len(second['templates']), 2)
        self.assertEqual([r['tag_name'] for r in client.releases], ['store-00001', 'store-00002'])

    def test_pending_update_keeps_existing_catalog_entry(self):
        directory = self.template()
        client = FakeGitHub()
        original = self.run_publish(client)['templates'][0]
        (directory / 'preview.jpg').write_bytes(b'\xff\xd8\xffupdated')
        pending = self.run_publish(client, limit=0)
        self.assertEqual(pending['pendingCount'], 1)
        self.assertEqual(pending['templates'][0]['revision'], original['revision'])

    def test_removed_template_is_unlisted_but_history_remains(self):
        directory = self.template()
        client = FakeGitHub()
        self.run_publish(client)
        client.releases[0]['assets'][0]['download_count'] = 9
        import shutil
        shutil.rmtree(directory)
        self.assertEqual(self.run_publish(client)['templates'], [])
        self.template()
        self.assertEqual(self.run_publish(client)['templates'][0]['downloadCount'], 9)

    def test_invalid_sources_are_rejected_before_publish(self):
        directory = self.template()
        (directory / 'preview.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        with self.assertRaises(ValueError):
            publisher.scan_templates(self.root)
        (directory / 'preview.png').unlink()
        data = json.loads((directory / 'template.json').read_text())
        data['canvasJson'] = {'objects': []}
        (directory / 'template.json').write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            publisher.scan_templates(self.root)

    def test_thousands_are_indexed_without_building_zips(self):
        for index in range(1001):
            self.template(f'template-{index}')
        with patch.object(publisher, 'make_package', side_effect=AssertionError('Scan must not package')):
            items = publisher.scan_templates(self.root)
        self.assertEqual(len(items), 1001)
        self.assertEqual(len({item['id'] for item in items}), 1001)

    def test_large_catalog_uses_github_object_and_raw_media_types(self):
        client = publisher.GitHub(REPO, 'test-only-token')
        large_catalog = {'templates': [{'name': 'Example'}]}
        with patch.object(client, 'request', side_effect=[{'sha': 'blob-sha', 'content': '', 'encoding': 'none'}, large_catalog]) as request:
            catalog, sha = client.read_catalog()
        self.assertEqual(catalog, large_catalog)
        self.assertEqual(sha, 'blob-sha')
        self.assertEqual(request.call_args_list[0].kwargs['accept'], 'application/vnd.github.object+json')
        self.assertEqual(request.call_args_list[1].kwargs['accept'], 'application/vnd.github.raw+json')

    def test_metadata_is_preserved_in_catalog_and_exported_package(self):
        directory = self.template()
        data = json.loads((directory / 'template.json').read_text())
        data.update({'version': '1.2.0', 'author': {'name': 'HappyQuQu', 'url': 'https://github.com/HappyQuQu'},
                     'changelog': '改善海报裁切', 'minAppVersion': '1.5.0'})
        (directory / 'template.json').write_text(json.dumps(data, ensure_ascii=False))
        client = FakeGitHub()
        catalog = self.run_publish(client)
        entry = catalog['templates'][0]
        for field in ('version', 'author', 'changelog', 'minAppVersion'):
            self.assertEqual(entry[field], data[field])
        with zipfile.ZipFile(io.BytesIO(publisher.make_package(publisher.scan_templates(self.root)[0]))) as archive:
            self.assertEqual(json.loads(archive.read('template.json'))['author'], data['author'])

    def test_legacy_metadata_defaults_and_invalid_fields(self):
        self.assertEqual(publisher.metadata({}), {'version': '1.0.0', 'changelog': ''})
        for data in ({'version': '1.0'}, {'version': '01.0.0'}, {'author': 'someone'},
                     {'author': {'name': 'Author', 'url': 'javascript:alert(1)'}}, {'minAppVersion': 'latest'}, {'changelog': []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                publisher.metadata(data)


if __name__ == '__main__':
    unittest.main()
