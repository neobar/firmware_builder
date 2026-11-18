import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('firmware', Path(__file__).resolve().parents[1] / 'scripts/firmware.py')
firmware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firmware)


class ManifestChecks(unittest.TestCase):
    def setUp(self):
        self.cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        os.chdir(self.temp.name)
        Path('tmp').mkdir()
        # ABI values are from the pinned source's toolchain/ustream-ssl recipes.
        Path('tmp/.packageinfo').write_text(
            'Package: libgcc\nABI-Version: 1\n\n'
            'Package: libustream-mbedtls\nABI-Version: 20201210\n\n'
            'Package: libexample2\nABI-Version: 3\n\n'
            'Package: kmod-example\nABI-Version: 99\n\n'
            'Package: luci-app-vantage\nABI-Version: 2\n')
        self.directory = Path('bin/targets/qualcommbe/ipq53xx')
        self.directory.mkdir(parents=True)
        (self.directory / 'profiles.json').write_text(json.dumps({'profiles': {
            'jdcloud_re-cs-08': {'images': [{'name': 'test-sysupgrade.bin'}]}}}))
        (self.directory / 'test-sysupgrade.bin').write_bytes(b'fixture')
        self.manifest = self.directory / 'test.manifest'
        self.names = firmware.re_cs_08_roots() | {'luci-app-firewall', 'luci-app-package-manager', 'luci-theme-bootstrap'}
        self.names -= {'libgcc', 'libustream-mbedtls'}
        self.names |= {'libgcc1', 'libustream-mbedtls20201210'}

    def tearDown(self):
        os.chdir(self.cwd)
        self.temp.cleanup()

    def write_manifest(self, names):
        self.manifest.write_text(''.join(f'{name} - 1.0-r1\n' for name in sorted(names)))

    def test_abi_suffixed_required_libraries_pass_full_verification(self):
        self.write_manifest(self.names)
        firmware.verify('jdcloud-re-cs-08')

    def test_missing_library_still_fails(self):
        self.write_manifest(self.names - {'libgcc1'})
        with self.assertRaisesRegex(AssertionError, 'libgcc'):
            firmware.verify('jdcloud-re-cs-08')

    def test_unknown_suffix_does_not_satisfy_required_library(self):
        self.write_manifest((self.names - {'libgcc1'}) | {'libgcc999', 'libgcc-extra'})
        with self.assertRaisesRegex(AssertionError, 'libgcc'):
            firmware.verify('jdcloud-re-cs-08')

    def test_extra_app_is_rejected_even_with_declared_abi_suffix(self):
        self.write_manifest(self.names | {'luci-app-vantage2'})
        with self.assertRaisesRegex(AssertionError, 'Nonstandard LuCI'):
            firmware.verify('jdcloud-re-cs-08')

    def test_staging_abi_overrides_metadata(self):
        path = Path('staging_dir/target-aarch64_cortex-a53_musl/pkginfo/libgcc.version')
        path.parent.mkdir(parents=True)
        path.write_text('2\n')
        self.write_manifest((self.names - {'libgcc1'}) | {'libgcc2'})
        firmware.verify('jdcloud-re-cs-08')

    def test_digit_ending_names_and_kmods_follow_openwrt_suffix_rules(self):
        self.write_manifest({'libexample2-3', 'kmod-example99'})
        self.assertEqual(firmware.manifest_packages(self.manifest), {'libexample2', 'kmod-example99'})


if __name__ == '__main__':
    unittest.main()
