# Router firmware builder

为 `neobar/firmware_builder` 准备的 GitHub Actions 全源码固件构建配置。

## 构建设备

| Actions 选项 | 源码 | Target / Subtarget | 设备 Profile |
| --- | --- | --- | --- |
| bpi-r4-pro-8x | openwrt/openwrt | mediatek/filogic | bananapi_bpi-r4-pro-8x |
| zbt-z8803be | openwrt/openwrt | mediatek/filogic | zbtlink_zbt-z8803be |
| gemtek-xr1710g | openwrt/openwrt + PR #26296 | airoha/an7581 | gemtek_xr1710g-ubi |
| jdcloud-re-cs-08 | VIKINGYFY/immortalwrt，owrt 分支 | qualcommbe/ipq53xx | jdcloud_re-cs-08 |

仅构建 BPi-R4 Pro 8X，不构建 4E。
所有镜像均要求内置 `kmod-nft-tproxy`、`kmod-nft-socket`、`openssh-sftp-server`、`kmod-nft-bridge`、`kmod-tun`、`luci`。

BPi-R4 Pro 禁用无线驱动、无线固件、wpad/hostapd、iw/iwinfo 等相关包，也移除 `mt7988-wo-firmware`。
为防止选择完整 LuCI 后重新引入无线包，构建时只对 BPi 修改 LuCI Network/Status 的 iwinfo 依赖。
LuCI 有线网络、状态和防火墙管理保留；无线状态功能不可用。有线 PHY 固件保留。
构建在 `make defconfig` 后检查选包，并在编译后检查安装包 manifest；发现残留无线包或缺少指定包即失败。

## 运行

RE-CS-08 使用 `configs/re-cs-08-packages.json` 中的 OpenWrt 默认基础/路由包，
加上 RE-CS-08 必要的 PPE、PHY、交换端口、存储/启动支持及本项目要求的软件包。
配置时显式关闭所有其他包的默认选择，然后由 Kconfig 自动拉取所需依赖，
避免 ImmortalWrt 的默认插件及其独立服务被带入镜像。
保留 OpenWrt 标准 LuCI 的 `luci-app-firewall`、`luci-app-package-manager` 和 Bootstrap 主题；
移除 autoreboot、fullconenat-sonic、gecoosac、homeproxy、mini-diskmanager、natmapt、partexp、
router-tool、samba4、upnp、vantage、wolultra 等额外应用和 Vantage 主题。
基础选包同时恢复为 OpenWrt 的 dnsmasq、mbedTLS 和 apk-mbedtls。
最终配置及安装 manifest 均禁止非标准 LuCI 应用/主题和常见额外服务。

RE-CS-08 仅更新 packages、luci 两个 feed，并仅安装必要包及其依赖的菜单定义，
避免无关的 video/Qt、fwknopd 和 squeezelite 菜单循环依赖阻止 Kconfig 配置。
默认列表中的虚拟包 `nftables` 明确落实为 firewall4 需要的 `nftables-json`。

1. 将本目录全部文件提交到仓库根目录，包含隐藏目录 `.github`。
2. 在仓库的 **Actions → Build router firmware → Run workflow** 选择设备。
3. `all` 会分别构建上述四个项目。每项独立运行，单项失败不取消其他设备。同一设备选择的新构建会取消仍在运行的旧构建。
4. 成功后从该次运行的 **Artifacts** 下载 `firmware-设备名-运行编号`。
5. `diagnostics-设备名-运行编号` 保存配置、源码及 feed 版本、补丁与日志，保留 14 天。

支持手动触发；向 main 提交 workflow、设备定义、脚本或补丁变更时，自动构建全部设备。运行不需要额外 secrets，workflow 只申请 `contents: read`。
固件产物包含 profile 对应镜像、可用的启动文件、manifest 和 sha256sums；不上传 packages 子目录。

## 源码与补丁

源码提交固定在 `devices.json`，设备支持和内核补丁不会跟随分支静默漂移。
Feeds 按源码中的 `feeds.conf.default` 更新，实际 feed 提交记录在 `source-versions.txt`。
因此源码固定，但不同日期的 feeds 可能不同；本配置不承诺二进制可复现。
升级源码提交时须重新检查设备 Profile、内核版本和补丁兼容性。

### XR1710G

内置 [OpenWrt PR #26296](https://github.com/openwrt/openwrt/pull/26296) 的三个固定提交：

- `2c26fb74ef25ddf887d1de056a209cdf3d721ac5`：共用 DTSI。
- `74bf4348b0f5a73206a8afe563805216c1a9e143`：U-Boot chainloader。
- `1b981abdd1931f7bde8defc2965260572bc6bfec`：设备支持。

使用 PR 的 UBI 2.0 布局。首次安装和旧布局迁移请遵循 PR 的硬件安装说明；不要把 sysupgrade 镜像直接用于不兼容的布局。
该 PR 另行记录了 10GbE PCS/PHY 链路问题；本项目只包含指定 PR 的设备支持，不额外加入实验性 PCS/PHY 补丁。

### 长时间 UDP / 硬件卸载修复

所有设备加入 [issue #17915 指定评论](https://github.com/openwrt/openwrt/issues/17915#issuecomment-6617121046)
引用的 Arnau Valls 内核补丁：
[邮件原文](https://lore.kernel.org/netfilter-devel/20261103222243.445644-1-vallsfustearnau@gmail.com/)、
[邮件镜像](https://lists.openwall.net/netdev/2026/11/03/920)。

补丁在 `FLOW_BLOCK_UNBIND` 时、解绑驱动回调前执行设备流清理、第二次 GC 与 offload flush。
代码与原邮件一致，仅调整补丁上下文行号以适配 Linux 6.18.60，放入 `generic/pending-6.18`，由正常内核补丁流程应用。
编译后还会检查实际内核源码中该代码位于驱动解绑之前。

## 验证范围

交付前已验证 workflow 的 actionlint 检查、Python 语法、XR1710G 三个补丁在固定 OpenWrt 提交上的应用、
修复补丁与两套源码已有相关 generic 补丁叠加后的应用、LuCI 无线依赖移除。
完整 Linux 交叉编译、最终 Kconfig/manifest 检查由首次 Actions 运行完成；静态检查不代表固件已通过实机测试。
