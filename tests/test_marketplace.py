import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse
import zipfile

SPEC = importlib.util.spec_from_file_location('marketplace', Path(__file__).resolve().parents[1] / 'scripts/marketplace.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

COMMIT = 'a' * 40
SOURCE = {'repository': 'Tracht-Digital-Solutions/example-chatgpt-plugin', 'path': 'plugin'}
CONFIG = {'name': 'tracht-digital-plugins', 'displayName': 'Tracht Digital Plugins', 'sources': [SOURCE]}


class FakeGitHub:
    def __init__(self):
        self.metadata = {'private': False, 'visibility': 'public'}
        self.release = {'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False}
        self.tree = {'truncated': False, 'tree': []}
        self.blobs = {}
        self.repos = []
        self.calls = []
        self.add('plugin/plugin.json', m.json_bytes({
            'name': 'example-plugin', 'version': '1.2.3', 'description': 'Useful workflow',
            'extensions': {'com.openai': {'interface': {'category': 'Productivity', 'shortDescription': 'Useful workflow', 'logo': './assets/icon.svg'}}}
        }))
        self.add('plugin/assets/icon.svg', b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"/>')
        self.add('plugin/skills/example/SKILL.md', b'---\nname: example\ndescription: Example workflow.\n---\nHelp with the workflow.\n')
        self.add('LICENSE', b'Upstream license\n')
        self.add('NOTICE', b'Upstream attribution\n')
        self.add('README.md', b'Not a plugin file\n')

    def add(self, name, content, mode='100644'):
        sha = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
        self.tree['tree'] = [x for x in self.tree['tree'] if x['path'] != name]
        self.tree['tree'].append({'path': name, 'mode': mode, 'type': 'blob', 'sha': sha, 'size': len(content)})
        self.blobs[sha] = {'encoding': 'base64', 'content': base64.b64encode(content).decode()}

    def get(self, path):
        self.calls.append(path)
        if path.startswith('/users/'):
            return self.repos
        if '/releases/' in path:
            return self.release
        if '/commits/' in path:
            return {'sha': COMMIT}
        if '/git/trees/' in path:
            return self.tree
        if '/git/blobs/' in path:
            return self.blobs[path.rsplit('/', 1)[1]]
        return self.metadata


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()

    def temporary(self):
        return tempfile.TemporaryDirectory(prefix='test-', dir=Path(__file__).resolve().parents[1])

    def collect(self):
        return m.collect(copy.deepcopy(CONFIG), self.api)

    def test_real_assembly_records_origin_preserves_assets_and_notices(self):
        output = self.collect()
        catalog = json.loads(output['.agents/plugins/marketplace.json'])
        self.assertEqual(catalog['plugins'][0]['source'], {'source': 'local', 'path': './plugins/example-plugin'})
        self.assertIn('plugins/example-plugin/UPSTREAM_LICENSE', output)
        self.assertIn('plugins/example-plugin/UPSTREAM_NOTICE', output)
        self.assertIn('plugins/example-plugin/assets/icon.svg', output)
        self.assertNotIn('plugins/example-plugin/README.md', output)
        lock = json.loads(output['catalog.lock.json'])['plugins'][0]
        self.assertEqual(lock['commit'], COMMIT)
        self.assertEqual(lock['tag'], 'v1.2.3')
        self.assertEqual(lock['files']['UPSTREAM_LICENSE'], hashlib.sha256(b'Upstream license\n').hexdigest())

    def test_idempotent_bytes(self):
        self.assertEqual(self.collect(), self.collect())

    def test_private_repository_fails_before_fetching_content(self):
        self.api.metadata = {'private': True, 'visibility': 'private'}
        with self.assertRaisesRegex(ValueError, 'private'):
            self.collect()
        self.assertEqual(len(self.api.calls), 1)

    def test_prerelease_and_draft_rejected(self):
        for flag in ('draft', 'prerelease'):
            with self.subTest(flag=flag):
                self.api = FakeGitHub()
                self.api.release[flag] = True
                with self.assertRaisesRegex(ValueError, 'stable'):
                    self.collect()

    def test_version_mismatch_rejected(self):
        self.api.release['tag_name'] = 'v2.0.0'
        with self.assertRaisesRegex(ValueError, 'version differ'):
            self.collect()

    def test_truncated_tree_rejected(self):
        self.api.tree['truncated'] = True
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            self.collect()

    def test_symlink_rejected(self):
        self.api.add('plugin/linked', b'/etc/passwd', mode='120000')
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            self.collect()

    def test_path_traversal_rejected(self):
        self.api.add('plugin/../../escape', b'oops')
        with self.assertRaisesRegex(ValueError, 'Unsafe path'):
            self.collect()

    def test_case_collisions_rejected(self):
        self.api.add('plugin/PLUGIN.json', b'{}')
        with self.assertRaisesRegex(ValueError, 'colliding'):
            self.collect()

    def test_corrupt_blob_rejected(self):
        sha = self.api.tree['tree'][0]['sha']
        self.api.blobs[sha]['content'] = base64.b64encode(b'x' * self.api.tree['tree'][0]['size']).decode()
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.collect()

    def test_missing_asset_rejected(self):
        self.api.tree['tree'] = [x for x in self.api.tree['tree'] if not x['path'].endswith('icon.svg')]
        with self.assertRaisesRegex(ValueError, 'Missing local asset'):
            self.collect()

    def test_secret_and_dependency_files_rejected(self):
        for path in ('.env', 'node_modules/unsafe/index.js', 'private.key'):
            with self.subTest(path=path):
                self.api = FakeGitHub()
                self.api.add('plugin/' + path, b'secret')
                with self.assertRaises(ValueError):
                    self.collect()

    def test_oversized_file_rejected(self):
        self.api.tree['tree'][0]['size'] = m.MAX_FILE + 1
        with self.assertRaisesRegex(ValueError, 'size limit'):
            self.collect()

    def test_duplicate_plugin_identity_rejected(self):
        config = copy.deepcopy(CONFIG)
        config['sources'].append({'repository': 'Tracht-Digital-Solutions/another-plugin', 'path': 'plugin'})
        with self.assertRaisesRegex(ValueError, 'Duplicate plugin name'):
            m.collect(config, self.api)

    def test_topic_discovery_deduplicates_and_ignores_private_archived_forks(self):
        config = copy.deepcopy(CONFIG)
        config['sources'][0]['tag'] = 'v1.2.3'
        config['discovery'] = {'owners': ['Tracht-Digital-Solutions'], 'topic': 'chatgpt-plugin', 'path': 'plugin'}
        self.api.repos = [
            {'full_name': SOURCE['repository'], 'topics': ['chatgpt-plugin']},
            {'full_name': 'Tracht-Digital-Solutions/private', 'topics': ['chatgpt-plugin'], 'private': True},
            {'full_name': 'Tracht-Digital-Solutions/archived', 'topics': ['chatgpt-plugin'], 'archived': True},
            {'full_name': 'Tracht-Digital-Solutions/fork', 'topics': ['chatgpt-plugin'], 'fork': True},
            {'full_name': 'Tracht-Digital-Solutions/unrelated', 'topics': []},
        ]
        self.assertEqual(m.config_sources(config, self.api), [dict(SOURCE, tag='v1.2.3')])

    def test_discovery_paginates(self):
        class PagedAPI:
            def get(self, path):
                if urllib.parse.parse_qs(urllib.parse.urlparse(path).query)['page'] == ['1']:
                    return [{'full_name': f'Tracht-Digital-Solutions/other-{i}', 'topics': []} for i in range(100)]
                return [{'full_name': SOURCE['repository'], 'topics': ['chatgpt-plugin']}]
        config = dict(CONFIG, sources=[], discovery={'owners': ['Tracht-Digital-Solutions']})
        self.assertEqual(m.config_sources(config, PagedAPI()), [SOURCE])

    def test_exclude_removes_discovered_repository(self):
        config = dict(CONFIG, sources=[], exclude=['Tracht-Digital-Solutions/Unwanted'], discovery={'owners': ['Tracht-Digital-Solutions']})
        self.api.repos = [
            {'full_name': SOURCE['repository'], 'topics': ['chatgpt-plugin']},
            {'full_name': 'Tracht-Digital-Solutions/unwanted', 'topics': ['chatgpt-plugin']},
        ]
        self.assertEqual(m.config_sources(config, self.api), [SOURCE])

    def test_invalid_config_path_rejected(self):
        for path in ('../escape', '/absolute', 'a\\b', 'C:/escape', 'CON/file', 'trailing./file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                m.config_sources(dict(CONFIG, sources=[dict(SOURCE, path=path)]), self.api)

    def test_atomic_publication_preserves_existing_snapshot_on_collect_error(self):
        with self.temporary() as tmp:
            root = Path(tmp)
            m.install(root, self.collect())
            before = (root / 'catalog.lock.json').read_bytes()
            self.api.metadata['private'] = True
            with self.assertRaises(ValueError):
                m.install(root, self.collect())
            self.assertEqual(before, (root / 'catalog.lock.json').read_bytes())
            self.assertEqual(m.validate_snapshot(root), 1)

    def test_offline_validation_detects_tampering_and_extra_files(self):
        with self.temporary() as tmp:
            root = Path(tmp)
            m.install(root, self.collect())
            target = root / 'plugins/example-plugin/assets/icon.svg'
            original = target.read_bytes()
            target.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                m.validate_snapshot(root)
            target.write_bytes(original)
            (root / 'plugins/example-plugin/extra.txt').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                m.validate_snapshot(root)

    def test_bundle_is_reproducible_and_contains_native_marketplace(self):
        self.api.add('plugin/scripts/helper.sh', b'#!/bin/sh\nexit 0\n', mode='100755')
        with self.temporary() as tmp:
            root = Path(tmp)
            m.install(root, self.collect())
            (root / 'README.md').write_text('Read me')
            (root / 'LICENSE').write_text('MIT')
            archive = m.bundle(root)
            before = archive.read_bytes()
            self.assertEqual(before, m.bundle(root).read_bytes())
            with zipfile.ZipFile(archive) as z:
                self.assertIn('.agents/plugins/marketplace.json', z.namelist())
                self.assertIn('plugins/example-plugin/plugin.json', z.namelist())
                self.assertNotIn('scripts/marketplace.py', z.namelist())
                self.assertEqual(z.getinfo('plugins/example-plugin/scripts/helper.sh').external_attr >> 16, 0o100755)

    def test_empty_discovery_does_not_erase_catalog(self):
        with self.assertRaisesRegex(ValueError, 'No plugin sources'):
            m.collect(dict(CONFIG, sources=[]), self.api)

    def test_repository_bootstrap_is_valid_and_cannot_claim_self_contained_bundle(self):
        root = Path(__file__).resolve().parents[1]
        self.assertGreaterEqual(m.validate_snapshot(root), 1)
        lock = json.loads((root / 'catalog.lock.json').read_text(encoding='utf-8'))
        if any(r.get('bootstrap') for r in lock['plugins']):
            with self.assertRaisesRegex(ValueError, 'Run sync'):
                m.bundle(root)


if __name__ == '__main__':
    unittest.main()
