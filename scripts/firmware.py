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


def run(*args):
    subprocess.run(args, check=True)


def wireless(name):
    return bool(re.search(
        r'(^|[-_])(wifi|wireless|wpad|hostapd|wpa|iwinfo|mac80211|cfg80211|'
        r'ath[0-9a-z]*|rt2[0-9a-z-]*|'
        r'brcm[0-9a-z-]*|b43|rtl8[0-9a-z-]*|iwlwifi)([-_]|$)|'
        r'^iw(-|$)|^libiwinfo|^wireless-regdb$|^wireless-tools$|^kmod-mt76|^kmod-mt79|'
        r'^kmod-cfg80211|^kmod-mac80211|'
        r'^mt79.*-wo-firmware$', name))


def prepare(device):
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


def configure(device):
    d = DEVICES[device]
    lines = [f'CONFIG_TARGET_{d["target"]}=y',
             f'CONFIG_TARGET_{d["target"]}_{d["subtarget"]}=y',
             f'CONFIG_TARGET_{d["target"]}_{d["subtarget"]}_DEVICE_{d["profile"]}=y',
             'CONFIG_TARGET_ROOTFS_SQUASHFS=y', 'CONFIG_TARGET_ROOTFS_INITRAMFS=y',
             '# CONFIG_ALL_KMODS is not set', '# CONFIG_ALL_NONSHARED is not set']
    lines += [f'CONFIG_PACKAGE_{p}=y' for p in REQUIRED]
    Path('.config').write_text('\n'.join(lines) + '\n')
    run('make', 'defconfig')
    if d['no_wifi']:
        # Read all known package names, including unselected defaults.
        names = set(re.findall(r'^Package: (.+)$', Path('tmp/.packageinfo').read_text(), re.M))
        disabled = {p for p in names | selected() if wireless(p)}
        text = Path('.config').read_text()
        text = '\n'.join(line for line in text.splitlines()
                         if not any(line.startswith(f'CONFIG_PACKAGE_{p}=') for p in disabled))
        Path('.config').write_text(text + '\n' + '\n'.join(
            f'# CONFIG_PACKAGE_{p} is not set' for p in sorted(disabled)) + '\n')
        run('make', 'defconfig')
    config = Path('.config').read_text()
    assert f'CONFIG_TARGET_{d["target"]}_{d["subtarget"]}_DEVICE_{d["profile"]}=y' in config
    for package in REQUIRED:
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
        packages = {line.split()[0] for line in manifest.read_text().splitlines() if line.strip()}
        assert not (missing := set(REQUIRED) - packages), f'{manifest}: missing {missing}'
        if d['no_wifi']:
            assert not (bad := sorted(p for p in packages if wireless(p))), f'{manifest}: wireless {bad}'
    print(f'Verified {device}: {len(profile["images"])} images and {len(manifests)} manifests')


if __name__ == '__main__':
    action, device = sys.argv[1:]
    assert device in DEVICES
    {'prepare': prepare, 'configure': configure, 'verify': verify}[action](device)
