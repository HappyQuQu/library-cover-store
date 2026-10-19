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

    def test_classification_refresh_does_not_repackage_or_change_revisions(self):
        self.template()
        client = FakeGitHub()
        original = self.run_publish(client)['templates'][0]
        index = {'version': 1, 'templates': {'胶片放映': {'recommendedUses': ['library', 'collection'], 'tags': ['复古', '电影']}}}
        (self.root / 'classifications.json').write_text(json.dumps(index))
        with patch.object(publisher, 'make_package', side_effect=AssertionError('Classification does not alter packages')):
            classified = self.run_publish(client)['templates'][0]
        self.assertEqual(classified['classification'], {'recommendedUses': ['library', 'collection'], 'tags': ['复古', '电影'], 'artworkType': 'none', 'artworkCount': 0})
        self.assertEqual(classified['revision'], original['revision'])
        self.assertEqual(classified['packageSha256'], original['packageSha256'])
        self.assertEqual(client.uploads, 1)
        index['templates']['胶片放映']['tags'] = ['胶片']
        (self.root / 'classifications.json').write_text(json.dumps(index))
        self.assertEqual(self.run_publish(client)['templates'][0]['classification']['tags'], ['胶片'])
        self.assertEqual(client.uploads, 1)

    def test_classification_index_requires_complete_valid_coverage(self):
        self.template()
        for entry in ({}, {'missing': {'recommendedUses': ['library'], 'tags': []}},
                      {'胶片放映': {'recommendedUses': ['invalid'], 'tags': []}},
                      {'胶片放映': {'recommendedUses': ['library'], 'tags': ['duplicate', 'duplicate']}}):
            with self.subTest(entry=entry):
                (self.root / 'classifications.json').write_text(json.dumps({'version': 1, 'templates': entry}))
                with self.assertRaises(ValueError):
                    publisher.scan_templates(self.root)

    def test_artwork_summary_distinguishes_sources_bindings_and_decoration(self):
        self.assertEqual(publisher.artwork_summary({'objects': [{'src': 'decorative.jpg'}]}), {'artworkType': 'none', 'artworkCount': 0})
        canvas = {'objects': [{'objects': [{'posterVar': 'post3'}, {'posterVar': 'post9', 'visible': False}]},
                              {'objects': [{'posterVar': 'post8'}], 'opacity': 0}]}
        self.assertEqual(publisher.artwork_summary(canvas), {'artworkType': 'poster', 'artworkCount': 3})
        canvas['posterSource'] = 'backdrop'
        self.assertEqual(publisher.artwork_summary(canvas), {'artworkType': 'backdrop', 'artworkCount': 3})
        canvas['posterSource'] = 'poster'
        canvas['posterBackground'] = {'source': 'backdrop'}
        self.assertEqual(publisher.artwork_summary(canvas), {'artworkType': 'mixed', 'artworkCount': 3})
        self.assertEqual(publisher.artwork_summary({'posterBackground': {'source': 'backdrop'}}), {'artworkType': 'backdrop', 'artworkCount': 1})

    def test_legacy_metadata_defaults_and_invalid_fields(self):
        self.assertEqual(publisher.metadata({}), {'version': '1.0.0', 'changelog': ''})
        for data in ({'version': '1.0'}, {'version': '01.0.0'}, {'author': 'someone'},
                     {'author': {'name': 'Author', 'url': 'javascript:alert(1)'}}, {'minAppVersion': 'latest'}, {'changelog': []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                publisher.metadata(data)

    def add_font(self, directory, content=b'\x00\x01\x00\x00' + b'font-data' * 4):
        folder = directory / 'fonts'
        folder.mkdir(exist_ok=True)
        filename = f'fonts/{publisher.digest(content)}.ttf'
        (directory / filename).write_bytes(content)
        data = json.loads((directory / 'template.json').read_text())
        data['canvasJson'] = json.dumps({'objects': [{'type': 'group', 'objects': [{'type': 'text', 'fontFamily': 'Portable Font'}]}]})
        data['fonts'] = [{'family': 'Portable Font', 'file': filename, 'sha256': publisher.digest(content)}]
        (directory / 'template.json').write_text(json.dumps(data))
        return data, filename

    def test_font_files_are_packaged_reproducibly_and_change_store_revisions(self):
        directory = self.template()
        data, filename = self.add_font(directory)
        item = publisher.scan_templates(self.root)[0]
        package = publisher.make_package(item)
        self.assertEqual(package, publisher.make_package(item))
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertIn(filename, archive.namelist())
            self.assertEqual(publisher.digest(archive.read(filename)), data['fonts'][0]['sha256'])
            self.assertEqual(json.loads(archive.read('template.json'))['fonts'], data['fonts'])
        self.add_font(directory, b'\x00\x01\x00\x00' + b'new-version' * 4)
        (directory / filename).unlink()
        self.assertNotEqual(publisher.scan_templates(self.root)[0]['revision'], item['revision'])

    def test_fonts_can_exceed_old_package_limit_while_template_content_stays_small(self):
        directory = self.template()
        self.add_font(directory, b'\x00\x01\x00\x00' + b'x' * (6 * 1024 * 1024))
        item = publisher.scan_templates(self.root)[0]
        with zipfile.ZipFile(io.BytesIO(publisher.make_package(item))) as archive:
            self.assertGreater(archive.getinfo(item['fontPaths'][0][0]).file_size, 5 * 1024 * 1024)
        with patch.object(publisher, 'MAX_BYTES', 1024), self.assertRaises(ValueError):
            publisher.scan_templates(self.root)

    def test_invalid_font_dependencies_are_rejected(self):
        directory = self.template()
        data, filename = self.add_font(directory)
        for change in ({'file': '../outside.ttf'}, {'sha256': '0' * 64}, {'family': 'Not Used'}, {'family': '../Bad'}, {'file': 'fonts/missing.ttf'}):
            invalid = {**data, 'fonts': [{**data['fonts'][0], **change}]}
            (directory / 'template.json').write_text(json.dumps(invalid))
            with self.subTest(change=change), self.assertRaises(ValueError):
                publisher.scan_templates(self.root)
        (directory / 'template.json').write_text(json.dumps(data))
        (directory / 'fonts' / 'unlisted.ttf').write_bytes(b'\x00\x01\x00\x00font-data')
        with self.assertRaises(ValueError):
            publisher.scan_templates(self.root)
        (directory / 'fonts' / 'unlisted.ttf').unlink()
        (directory / filename).unlink()
        outside = self.root / '.outside.ttf'
        outside.write_bytes(b'\x00\x01\x00\x00font-data')
        (directory / filename).symlink_to(outside)
        with self.assertRaises(ValueError):
            publisher.scan_templates(self.root)


if __name__ == '__main__':
    unittest.main()
