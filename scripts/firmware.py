#!/usr/bin/env python3
"""Source preparation, config and image manifest checks; run from buildroot."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICES = json.loads((ROOT / 'devices.json').read_text())
REQUIRED = ('kmod-nft-tproxy', 'kmod-nft-socket', 'openssh-sftp-server',
            'kmod-nft-bridge', 'kmod-tun', 'luci')
RE_CS_08_POLICY = json.loads((ROOT / 'configs/re-cs-08-packages.json').read_text())


def run(*args):
    subprocess.run(args, check=True)


def required_packages(device):
    return REQUIRED + tuple(DEVICES[device].get('required_packages', ()))


def setup_feeds(device):
    if DEVICES[device]['repository'] != 'VIKINGYFY/immortalwrt':
        return
    path = Path('feeds.conf')
    source = path if path.exists() else Path('feeds.conf.default')
    lines = [line for line in source.read_text().splitlines()
             if len(parts := line.split()) >= 3 and parts[0].startswith('src-')
             and parts[1] in {'packages', 'luci'}]
    assert {line.split()[1] for line in lines} == {'packages', 'luci'}
    path.write_text('\n'.join(lines) + '\n')


def install_feeds(device):
    if DEVICES[device]['repository'] == 'VIKINGYFY/immortalwrt':
        # Install only package roots and their feed dependencies. Installing every
        # optional package also imports broken, unrelated Kconfig definitions.
        packages = re_cs_08_roots() if device == 'jdcloud-re-cs-08' else required_packages(device)
        run('./scripts/feeds', 'install', *sorted(packages))
    else:
        run('./scripts/feeds', 'install', '-a')


def wireless(name):
    return bool(re.search(
        r'(^|[-_])(wifi|wireless|wpad|hostapd|wpa|iwinfo|mac80211|cfg80211|'
        r'ath[0-9a-z]*|rt2[0-9a-z-]*|'
        r'brcm[0-9a-z-]*|b43|rtl8[0-9a-z-]*|iwlwifi)([-_]|$)|'
        r'^iw(-|$)|^libiwinfo|^wireless-regdb$|^wireless-tools$|^kmod-mt76|^kmod-mt79|'
        r'^kmod-cfg80211|^kmod-mac80211|'
        r'^mt79.*-wo-firmware$', name))


def prepare(device):
    if DEVICES[device]['repository'] == 'VIKINGYFY/immortalwrt':
        for patch in sorted((ROOT / 'patches/immortalwrt').glob('*.patch')):
            run('git', 'apply', '--check', str(patch))
            run('git', 'apply', str(patch))
    if device == 'gemtek-xr1710g':
        for patch in sorted((ROOT / 'patches/xr1710g').glob('*.patch')):
            run('git', 'apply', '--check', str(patch))
            run('git', 'apply', str(patch))
    target = DEVICES[device]['target']
    makefile = Path(f'target/linux/{target}/Makefile').read_text()
    assert re.search(r'KERNEL_PATCHVER\s*:?=\s*6\.18\b', makefile), 'Revalidate patch for changed kernel'
    destination = Path('target/linux/generic/pending-6.18')
    assert destination.is_dir()
    for patch in (ROOT / 'patches/kernel').glob('*.patch'):
        shutil.copy2(patch, destination / patch.name)
    if DEVICES[device]['no_wifi']:
        # These runtime deps otherwise force iwinfo back on when selecting luci.
        for module in ('luci-mod-network', 'luci-mod-status'):
            path = Path(f'feeds/luci/modules/{module}/Makefile')
            text = path.read_text()
            assert '+rpcd-mod-iwinfo' in text, f'Review changed LuCI dependencies: {path}'
            text = text.replace('+rpcd-mod-iwinfo', '').replace('+libiwinfo', '')
            text = re.sub(r'^PKG_BUILD_DEPENDS:=iwinfo\s*$', '', text, flags=re.M)
            path.write_text(text)


def selected():
    return set(re.findall(r'^CONFIG_PACKAGE_(.+)=[ym]$', Path('.config').read_text(), re.M))


def defconfig():
    result = subprocess.run(['make', 'defconfig'], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    print(result.stdout, end='', flush=True)
    result.check_returncode()
    assert 'recursive dependency detected' not in result.stdout, 'Kconfig contains recursive dependencies'


def re_cs_08_roots():
    return set(RE_CS_08_POLICY['openwrt_defaults'] + RE_CS_08_POLICY['hardware_support']) | set(REQUIRED)


def re_cs_08_seed(lines, names):
    roots = re_cs_08_roots()
    assert not (missing := roots - names), f'Unknown package roots: {sorted(missing)}'
    seed = [line for line in lines if not line.startswith('CONFIG_PACKAGE_')]
    seed += ['# CONFIG_ALL is not set', 'CONFIG_USE_APK=y']
    seed += [f'# CONFIG_PACKAGE_{p} is not set' for p in sorted(names - roots)]
    seed += [f'CONFIG_PACKAGE_{p}=y' for p in sorted(roots)]
    return '\n'.join(seed) + '\n'


def check_re_cs_08(packages):
    roots = re_cs_08_roots()
    assert not (missing := roots - packages), f'Missing RE-CS-08 package roots: {sorted(missing)}'
    allowed = set(RE_CS_08_POLICY['standard_luci_apps'] + RE_CS_08_POLICY['standard_luci_themes'])
    extras = {p for p in packages if p.startswith(('luci-app-', 'luci-theme-')) and p not in allowed}
    assert not extras, f'Nonstandard LuCI applications/themes: {sorted(extras)}'
    # These are optional services, not dependencies of the standard router/LuCI set.
    forbidden = {'autocore', 'automount', 'cpufreq', 'qca-ppe-qos', 'default-settings',
                 'default-settings-chn', 'homeproxy', 'sing-box', 'samba4-server',
                 'miniupnpd', 'miniupnpd-nftables', 'natmap', 'natmapt', 'gecoosac',
                 'dnsmasq-full', 'luci-theme-vantage'}
    assert not (bad := packages & forbidden), f'Unexpected optional services: {sorted(bad)}'


def manifest_packages(manifest):
    # Config symbols use base names, but installed packages may carry ABI
    # suffixes. Follow include/feeds.mk's FormatABISuffix using build metadata;
    # never strip arbitrary trailing digits or accept a mere name prefix.
    versions = {}
    package = None
    for line in Path('tmp/.packageinfo').read_text().splitlines():
        key, separator, value = line.partition(':')
        if separator and key == 'Package':
            package = value.strip()
        elif separator and key == 'ABI-Version' and package:
            versions[package] = value.strip()
    # Actual staging records take precedence over configure-time metadata.
    for record in Path('staging_dir').glob('target-*/pkginfo/*.version'):
        versions[record.stem] = record.read_text().strip()
    aliases = {}
    for package, abi in versions.items():
        if abi and not package.startswith('kmod-'):
            suffix = ('-' if package[-1].isdigit() else '') + abi
            aliases[package + suffix] = package
    names = {line.split()[0] for line in manifest.read_text().splitlines() if line.strip()}
    return {aliases.get(name, name) for name in names}


def configure(device):
    d = DEVICES[device]
    lines = [f'CONFIG_TARGET_{d["target"]}=y',
             f'CONFIG_TARGET_{d["target"]}_{d["subtarget"]}=y',
             f'CONFIG_TARGET_{d["target"]}_{d["subtarget"]}_DEVICE_{d["profile"]}=y',
             'CONFIG_TARGET_ROOTFS_SQUASHFS=y', 'CONFIG_TARGET_ROOTFS_INITRAMFS=y',
             '# CONFIG_ALL_KMODS is not set', '# CONFIG_ALL_NONSHARED is not set']
    lines += [f'CONFIG_PACKAGE_{p}=y' for p in required_packages(device)]
    Path('.config').write_text('\n'.join(lines) + '\n')
    defconfig()
    if device == 'jdcloud-re-cs-08':
        # Start from OpenWrt roots, not the fork's package defaults. Kconfig
        # selects dependencies of these roots; unrelated default-y packages stay off.
        names = set(re.findall(r'^Package: (.+)$', Path('tmp/.packageinfo').read_text(), re.M))
        Path('.config').write_text(re_cs_08_seed(lines, names))
        defconfig()
        check_re_cs_08(selected())
    if d['no_wifi']:
        # Read all known package names, including unselected defaults.
        names = set(re.findall(r'^Package: (.+)$', Path('tmp/.packageinfo').read_text(), re.M))
        disabled = {p for p in names | selected() if wireless(p)}
        text = Path('.config').read_text()
        text = '\n'.join(line for line in text.splitlines()
                         if not any(line.startswith(f'CONFIG_PACKAGE_{p}=') for p in disabled))
        Path('.config').write_text(text + '\n' + '\n'.join(
            f'# CONFIG_PACKAGE_{p} is not set' for p in sorted(disabled)) + '\n')
        defconfig()
    config = Path('.config').read_text()
    assert f'CONFIG_TARGET_{d["target"]}_{d["subtarget"]}_DEVICE_{d["profile"]}=y' in config
    for package in required_packages(device):
        assert f'CONFIG_PACKAGE_{package}=y' in config, f'Missing built-in package: {package}'
    if d['no_wifi']:
        assert not (bad := sorted(p for p in selected() if wireless(p))), f'Wireless packages selected: {bad}'
    run('sh', '-c', './scripts/diffconfig.sh > config.seed')


def verify(device):
    d = DEVICES[device]
    directory = Path(f'bin/targets/{d["target"]}/{d["subtarget"]}')
    profiles = json.loads((directory / 'profiles.json').read_text())['profiles']
    profile = profiles[d['profile']]
    assert profile.get('images'), 'No firmware images were generated'
    for entry in profile['images']:
        assert (directory / entry['name']).is_file(), entry['name']
    manifests = list(directory.glob('*.manifest'))
    assert manifests, 'No installed package manifest found'
    for manifest in manifests:
        packages = manifest_packages(manifest)
        assert not (missing := set(required_packages(device)) - packages), f'{manifest}: missing {missing}'
        if device == 'jdcloud-re-cs-08':
            check_re_cs_08(packages)
        if d['no_wifi']:
            assert not (bad := sorted(p for p in packages if wireless(p))), f'{manifest}: wireless {bad}'
    print(f'Verified {device}: {len(profile["images"])} images and {len(manifests)} manifests')


if __name__ == '__main__':
    action, device = sys.argv[1:]
    assert device in DEVICES
    {'feeds': setup_feeds, 'install-feeds': install_feeds, 'prepare': prepare,
     'configure': configure, 'verify': verify}[action](device)
