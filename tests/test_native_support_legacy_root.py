"""Only the installer-owned bootstrap alias may reach the named legacy pack."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.core.errors import OmhError
from omh.install.installer import install_skill_pack
from omh.paths import OmhPaths


def snapshot(root):
    return {p.relative_to(root).as_posix(): (('link', str(p.readlink())) if p.is_symlink() else ('file', p.read_bytes()) if p.is_file() else ('dir',)) for p in root.rglob('*')}


class NativeSupportLegacyRootTests(unittest.TestCase):
    def test_bootstrap_default_store_fallback_matches_legacy_migration_only_when_named_pack_absent(self):
        for named_exists in (False, True, 'link'):
            with self.subTest(named_exists=named_exists), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                default = OmhPaths(root / '.omh', root / 'hermes')
                original = install_skill_pack(default)
                named = OmhPaths(root / 'named-store', default.hermes_home)
                if named_exists is True:
                    install_skill_pack(named)
                elif named_exists == 'link':
                    foreign = root / 'foreign-store'
                    foreign.mkdir()
                    named.omh_home.symlink_to(foreign, target_is_directory=True)
                alias = root / 'generations/bootstrap-legacy/skills'
                alias.parent.mkdir(parents=True)
                alias.symlink_to(default.skills_dir, target_is_directory=True)
                paths = OmhPaths(named.omh_home, named.hermes_home, managed_skills_dir=alias)
                before = snapshot(root)
                with patch.dict(os.environ, {'HOME': str(root), 'USERPROFILE': str(root), 'OMH_HOME': str(default.omh_home)}):
                    if named_exists:
                        message = 'symlink' if named_exists == 'link' else 'legacy link'
                        with self.assertRaisesRegex(OmhError, message):
                            install_skill_pack(paths)
                        self.assertEqual(snapshot(root), before)
                    else:
                        result = install_skill_pack(paths)
                        self.assertEqual(result['support_files'], original['support_files'])
                        self.assertTrue(named.manifest_path.is_file())

    def test_bootstrap_alias_updates_the_same_legacy_pack_without_changing_support_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = OmhPaths(root / 'omh', root / 'hermes')
            initial = install_skill_pack(home)
            alias = root / 'generations/bootstrap-legacy/skills'
            alias.parent.mkdir(parents=True)
            alias.symlink_to(home.skills_dir, target_is_directory=True)
            expected = {rel: (home.skills_dir / rel).read_bytes() for rel in initial['support_files']}
            result = install_skill_pack(OmhPaths(home.omh_home, home.hermes_home, managed_skills_dir=alias))
            self.assertEqual(result['support_files'], initial['support_files'])
            self.assertEqual({rel: (home.skills_dir / rel).read_bytes() for rel in expected}, expected)
            self.assertTrue(alias.is_symlink())

    def test_bootstrap_alias_cannot_redirect_to_a_foreign_pack_or_link_chain(self):
        for kind in ('foreign', 'chain', 'unnamed-alias', 'ancestor', 'leaf'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                home = OmhPaths(root / 'omh', root / 'hermes')
                install_skill_pack(home)
                foreign = root / 'foreign'
                foreign.mkdir()
                (foreign / 'sentinel').write_bytes(b'preserve foreign bytes')
                alias = root / 'generations/bootstrap-legacy/skills'
                if kind == 'ancestor':
                    (root / 'generations').symlink_to(foreign, target_is_directory=True)
                alias.parent.mkdir(parents=True, exist_ok=True)
                target = home.skills_dir
                if kind == 'foreign':
                    target = foreign
                elif kind == 'chain':
                    target = root / 'chain'
                    target.symlink_to(home.skills_dir, target_is_directory=True)
                elif kind == 'unnamed-alias':
                    alias = root / 'alias'
                elif kind == 'leaf':
                    leaf = home.skills_dir / 'operator/omh-show-reel/toolkit/scripts/timeline.py'
                    leaf.unlink()
                    leaf.symlink_to(foreign / 'sentinel')
                alias.symlink_to(target, target_is_directory=True)
                before = snapshot(root)
                with self.assertRaisesRegex(OmhError, 'symlink|legacy|link'):
                    install_skill_pack(OmhPaths(home.omh_home, home.hermes_home, managed_skills_dir=alias))
                self.assertEqual(snapshot(root), before, 'refusal changed a body, manifest, or foreign target')
