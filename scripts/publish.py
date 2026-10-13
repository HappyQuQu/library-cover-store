#!/usr/bin/env python3
"""Validate exported templates and incrementally publish individually counted ZIPs."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import zipfile

MAX_BYTES = 5 * 1024 * 1024
BATCH_SIZE = 200
MAX_UPLOADS_PER_RUN = 100
RESERVED = {'scripts', 'tests', 'doc', 'docs', 'examples', 'node_modules'}
ASSET_PATTERN = re.compile(r'^([a-f0-9]{64})--([a-f0-9]{64})\.zip$')
RELEASE_PATTERN = re.compile(r'^store-(\d{5,})$')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def metadata(data):
    result = {'version': data.get('version', '1.0.0'), 'changelog': data.get('changelog', '')}
    version_pattern = r'(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})'
    if not isinstance(result['version'], str) or len(result['version']) > 32 or not re.fullmatch(version_pattern, result['version']):
        raise ValueError('version 必须是 1.0.0 格式的版本号')
    if not isinstance(result['changelog'], str) or len(result['changelog']) > 10000:
        raise ValueError('changelog 必须是字符串，最多 10000 字符')
    if data.get('minAppVersion') is not None:
        minimum = data['minAppVersion']
        if not isinstance(minimum, str) or len(minimum) > 32 or not re.fullmatch(version_pattern, minimum):
            raise ValueError('minAppVersion 必须是 1.0.0 格式的版本号')
        result['minAppVersion'] = minimum
    if data.get('author') is not None:
        author = data['author']
        if not isinstance(author, dict) or not isinstance(author.get('name'), str) or not author['name'].strip() or len(author['name'].strip()) > 100:
            raise ValueError('author 必须包含 1–100 字符的 name')
        result['author'] = {'name': author['name'].strip()}
        if 'url' in author:
            url = author['url']
            if not isinstance(url, str) or len(url) > 500 or not url.startswith(('http://', 'https://')) or urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).hostname:
                raise ValueError('author.url 必须是 HTTP 或 HTTPS 地址')
            result['author']['url'] = url
    return result


def scan_templates(root):
    result = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name.startswith('.') or directory.name in RESERVED:
            continue
        if directory.is_symlink():
            raise ValueError(f'{directory.name}: 模板文件夹不能是软链接')
        if len(directory.name) > 200:
            raise ValueError(f'{directory.name}: 文件夹名称不能超过 200 字符')
        template_file = directory / 'template.json'
        previews = [p for p in directory.iterdir() if p.name in ('preview.jpg', 'preview.jpeg', 'preview.png', 'preview.webp')]
        if not template_file.is_file() or len(previews) != 1:
            raise ValueError(f'{directory.name}: 需要 template.json 和唯一的 preview.jpg/png/webp')
        preview_file = previews[0]
        if template_file.is_symlink() or preview_file.is_symlink():
            raise ValueError(f'{directory.name}: 模板文件不能是软链接')
        if template_file.stat().st_size + preview_file.stat().st_size > MAX_BYTES:
            raise ValueError(f'{directory.name}: JSON 和预览图合计不能超过 5 MB')
        raw_json, preview = template_file.read_bytes(), preview_file.read_bytes()
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            raise ValueError(f'{directory.name}: template.json 必须是对象')
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError(f'{directory.name}: name 必须为 1–100 字符')
        description = data.get('description', '')
        if description is None:
            description = ''
        if not isinstance(description, str) or len(description) > 10000:
            raise ValueError(f'{directory.name}: description 必须是字符串，最多 10000 字符')
        for field, maximum in [('width', 4000), ('height', 6000)]:
            if type(data.get(field)) is not int or not 100 <= data[field] <= maximum:
                raise ValueError(f'{directory.name}: {field} 必须在 100–{maximum} 之间')
        if not isinstance(data.get('canvasJson'), str):
            raise ValueError(f'{directory.name}: canvasJson 必须是应用导出的 JSON 字符串')
        canvas = json.loads(data['canvasJson'])
        if not isinstance(canvas, dict) or not isinstance(canvas.get('objects'), list):
            raise ValueError(f'{directory.name}: canvasJson 缺少 objects 数组')
        for field in ('isPublic', 'isLocked'):
            if field in data and type(data[field]) is not bool:
                raise ValueError(f'{directory.name}: {field} 必须是布尔值')
        valid_image = (preview.startswith(b'\xff\xd8\xff') or preview.startswith(b'\x89PNG\r\n\x1a\n')
                       or (preview.startswith(b'RIFF') and preview[8:12] == b'WEBP'))
        if not valid_image:
            raise ValueError(f'{directory.name}: 预览图必须是 JPEG、PNG 或 WebP 图片')
        template_id = digest(directory.name.encode('utf-8'))
        revision = digest(raw_json + b'\0' + preview)
        result.append({**metadata(data), 'id': template_id, 'revision': revision, 'name': name, 'description': description,
                       'width': data['width'], 'height': data['height'], 'folder': directory.name,
                       'previewFile': preview_file.name, 'templatePath': template_file, 'previewPath': preview_file,
                       'assetName': f'{template_id}--{revision}.zip'})
    return result


def make_package(item):
    # Build only missing/changed packages. Keep memory bounded to one template.
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for filename, file in [('template.json', item['templatePath']), (item['previewFile'], item['previewPath'])]:
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.read_bytes())
    package = stream.getvalue()
    if len(package) > MAX_BYTES:
        raise ValueError(f"{item['folder']}: ZIP 超过 5 MB")
    return package


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('无效的 GITHUB_REPOSITORY')
        self.repository, self.token = repository, token

    def request(self, path, method='GET', data=None, optional=False, binary=False, accept='application/vnd.github+json'):
        host = 'https://uploads.github.com' if binary else 'https://api.github.com'
        url = f'{host}/repos/{self.repository}{path}'
        headers = {'Authorization': f'Bearer {self.token}', 'Accept': accept,
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'library-cover-store-publisher'}
        if data is not None:
            headers['Content-Type'] = 'application/zip' if binary else 'application/json'
            body = data if binary else json.dumps(data).encode('utf-8')
        else:
            body = None
        try:
            with urlopen(Request(url, data=body, headers=headers, method=method), timeout=90) as response:
                return json.load(response)
        except HTTPError as error:
            if optional and error.code == 404:
                return None
            raise RuntimeError(f'GitHub API {method} {path}: HTTP {error.code}。稍后重新运行，已上传版本不会重复上传。') from None

    def pages(self, path):
        result, page = [], 1
        while True:
            separator = '&' if '?' in path else '?'
            batch = self.request(f'{path}{separator}per_page=100&page={page}')
            result.extend(batch)
            if len(batch) < 100:
                return result
            page += 1

    def read_catalog(self):
        item = self.request('/contents/catalog.json?ref=catalog', optional=True, accept='application/vnd.github.object+json')
        if not item:
            return None, None
        # GitHub supports files above 1 MB only through object/raw media types.
        if item.get('content'):
            data = json.loads(base64.b64decode(item['content']))
        else:
            data = self.request('/contents/catalog.json?ref=catalog', accept='application/vnd.github.raw+json')
        return data, item['sha']

    def write_catalog(self, catalog, previous_sha, source_sha):
        if not self.request('/git/ref/heads/catalog', optional=True):
            self.request('/git/refs', 'POST', {'ref': 'refs/heads/catalog', 'sha': source_sha})
        data = {'message': 'chore: synchronize template catalog and download counts', 'branch': 'catalog',
                'content': base64.b64encode((json.dumps(catalog, ensure_ascii=False, indent=2) + '\n').encode()).decode()}
        if previous_sha:
            data['sha'] = previous_sha
        self.request('/contents/catalog.json', 'PUT', data)


def publish(items, github, source_sha, upload_limit=MAX_UPLOADS_PER_RUN, pause=time.sleep):
    if not re.fullmatch(r'[a-f0-9]{40}', source_sha):
        raise ValueError('无效的 source commit SHA')
    previous, previous_sha = github.read_catalog()
    previous_entries = {item['id']: item for item in (previous or {}).get('templates', [])}
    releases = [release for release in github.pages('/releases') if RELEASE_PATTERN.fullmatch(release['tag_name']) and not release['draft']]
    releases.sort(key=lambda release: int(RELEASE_PATTERN.fullmatch(release['tag_name'])[1]))
    assets, counts = {}, {}
    for release in releases:
        release['_assets'] = github.pages(f"/releases/{release['id']}/assets")
        for asset in release['_assets']:
            match = ASSET_PATTERN.fullmatch(asset['name'])
            if not match or asset.get('state') != 'uploaded':
                continue
            assets[asset['name']] = asset
            counts[match[1]] = counts.get(match[1], 0) + asset['download_count']
    uploads = 0
    for item in items:
        if item['assetName'] in assets or uploads >= upload_limit:
            continue
        target = releases[-1] if releases and len(releases[-1]['_assets']) < BATCH_SIZE else None
        if target is None:
            number = int(RELEASE_PATTERN.fullmatch(releases[-1]['tag_name'])[1]) + 1 if releases else 1
            tag = f'store-{number:05d}'
            target = github.request('/releases', 'POST', {'tag_name': tag, 'target_commitish': source_sha,
                'name': f'模板商店 · 第 {number} 批', 'body': '每个附件是一套独立模板。旧版本用于保留累计下载次数，请勿删除或覆盖。',
                'draft': False, 'prerelease': False, 'make_latest': 'false'})
            target['_assets'] = []
            releases.append(target)
            pause(2)
        # Never overwrite an asset: its download counter belongs to that revision.
        path = f"/releases/{target['id']}/assets?name={quote(item['assetName'])}&label={quote(item['name'])}"
        package = make_package(item)
        item['packageSha256'] = digest(package)
        asset = github.request(path, 'POST', package, binary=True)
        target['_assets'].append(asset)
        assets[item['assetName']] = asset
        uploads += 1
        print(f"已发布: {item['folder']} → {target['tag_name']}")
        pause(2)
    entries, pending = [], 0
    for item in items:
        asset = assets.get(item['assetName'])
        if asset is None:
            pending += 1
            old = previous_entries.get(item['id'])
            if old:
                entries.append({**old, 'downloadCount': counts.get(item['id'], old['downloadCount'])})
            continue
        old = previous_entries.get(item['id'])
        checksum = item.get('packageSha256')
        if not checksum and old and old['revision'] == item['revision']:
            checksum = old['packageSha256']
        asset_digest = asset.get('digest', '') or ''
        if not checksum:
            checksum = asset_digest[7:] if re.fullmatch(r'sha256:[a-f0-9]{64}', asset_digest) else digest(make_package(item))
        if asset_digest and asset_digest != f'sha256:{checksum}':
            raise ValueError(f"{item['folder']}: Release 附件校验不一致，请勿手工替换附件")
        entry = {key: item[key] for key in ('id', 'revision', 'name', 'description', 'width', 'height', 'folder')}
        entry['packageSha256'] = checksum
        entry.update({key: item[key] for key in ('version', 'author', 'changelog', 'minAppVersion') if key in item})
        entry.update({'previewUrl': f"https://raw.githubusercontent.com/{github.repository}/{source_sha}/{quote(item['folder'], safe='')}/{item['previewFile']}",
                      'packageUrl': asset['browser_download_url'], 'downloadCount': counts.get(item['id'], 0)})
        entries.append(entry)
    catalog = {'version': 1, 'repository': github.repository,
               'generatedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
               'pendingCount': pending, 'templates': entries}
    github.write_catalog(catalog, previous_sha, source_sha)
    print(f'目录已更新：{len(entries)} 套可用，{uploads} 个新版本，{pending} 个等待下次发布。')
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['check', 'build', 'publish'])
    parser.add_argument('--output', default='.store-build')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    items = scan_templates(root)
    print(f'校验通过：{len(items)} 套模板')
    if args.mode == 'build':
        output = root / args.output
        output.mkdir(parents=True, exist_ok=True)
        for item in items:
            (output / item['assetName']).write_bytes(make_package(item))
        print(f'已生成 ZIP：{output}')
    if args.mode == 'publish':
        token = os.environ.get('GH_TOKEN')
        repository = os.environ.get('GITHUB_REPOSITORY')
        if not token or not repository:
            raise ValueError('publish 需要 GH_TOKEN 和 GITHUB_REPOSITORY；本地验证请使用 check 或 build')
        source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        publish(items, GitHub(repository, token), source_sha)


if __name__ == '__main__':
    main()
