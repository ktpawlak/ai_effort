# Kernel config trimming guide — `arm64-config.flavour.qcom`
### Reference platform: Qualcomm **X1E80100 (Snapdragon X Elite) — Hamoa IoT EVK**

Target: **Qualcomm arm64 robotics platform**. Base is the Ubuntu *generic*
arm64 config (~6,900 modules, ~3,500 built-ins). It carries drivers for nearly
every NIC, WiFi chip, TV tuner, sound codec, sensor and filesystem in the tree —
the vast majority irrelevant to a fixed qcom robotics board. Recommendations
below are **validated against the live unit** (only 177 modules loaded — see §0).

**Must keep working (verified in use):** camera (CAMSS + IMX412 + IRIS codec),
sound (SND_SOC_X1E80100 + WCD938x/WSA884x), USB + **USB4/Thunderbolt** +
**Type-C/UCSI**, PCIe (WiFi/NVMe), **UFS (rootfs)** + NVMe + MMC, **CAN
(mcp251xfd)**, **USB-Ethernet (ax88179)**, ath12k WiFi, QCA Bluetooth, qcom
clk/pinctrl/regulator/rpmsg/remoteproc/SCM/SMEM/MHI/thermal. Full `KEEP-LIST`
at the bottom.

How to apply: set the symbol to `# CONFIG_x is not set`, then run
`make olddefconfig` and rebuild. Disabling a *master* switch (vendor /
`ARCH_*` / subsystem) automatically drops all its children, so start there.

Legend per item: **C** cautious · **N** normal · **A** aggressive.

---

## 0. Ground truth from the live platform (192.168.1.123)

The reference unit is a **Qualcomm X1E80100 (Snapdragon X Elite, "Hamoa") IoT
EVK**, Ubuntu 26.04, kernel `7.0.0-1006-qcom`, DT `qcom,hamoa-iot-evk` /
`qcom,x1e80100`. Only **177 modules** are actually loaded. Probed hardware:

| Function | Actual part / driver | Action |
|---|---|---|
| Boot/root storage | **UFS** (Kioxia THGJFJT…, `ufs_qcom`/`ufshcd*`) + **NVMe** (Kioxia BG5) | **KEEP both** |
| SD | `mmc0` host present (built-in `sdhci_msm`) | **KEEP MMC** |
| WiFi | WCN785x FastConnect 7800 PCIe → **`ath12k`** only | keep ATH12K; drop ath10k/11k & other WLAN vendors |
| Bluetooth | QCA over UART (`hci1`, `btqca`/`hci_uart`) + USB `0cf3:e700` (`btusb`) | keep BT_QCA + btusb; drop btintel/btmtk/btrtl/btbcm |
| Ethernet | **USB ASIX AX88179** (`ax88179_178a`) — no on-board/PCIe NIC | **KEEP `USB_NET_AX88179_178A`**; drop all PCI NIC vendors |
| **CAN bus** | `can0` = **Microchip MCP251xFD over SPI** (`mcp251xfd`) | **KEEP `CAN_MCP251XFD` + `CAN_DEV` + `CAN_RAW`** |
| Camera | qcom **CAMSS** + **Sony IMX412** sensor (`imx412`) + MIPI-CSI2 phy | keep CAMSS + `VIDEO_IMX412`; drop other sensors |
| Video codec | **qcom IRIS** (`qcom_iris`, new Venus successor) | keep `VIDEO_QCOM_IRIS` (and/or VENUS) |
| Display | **`msm_dpu`** → DP-1/2/3, eDP-1, Writeback; `panel_edp`; `phy_qcom_edp` | keep DRM_MSM + PANEL_EDP + edp phy |
| USB-C / DP altmode | Type-C/UCSI/`pmic_glink`, **Parade `ps883x`** + **NXP `ptn3222`** redrivers, `gpio_sbu_mux` | **KEEP** these (no DRM bridge chips used) |
| USB4 | **`thunderbolt`** loaded (X Elite USB4) | keep `USB4` if you use TB/USB4 docks |
| Audio | `snd_soc_x1e80100` + **WCD938x** + **WSA884x** + LPASS macros + HDMI codec + q6apm/SoundWire/slimbus | keep these qcom codecs only |
| IMU/IIO sensors | **none populated** (`industrialio` loaded, 0 devices) | the ~524 IIO drivers are droppable on THIS board — but keep a slot if you add an IMU |
| RTC / power key / LED | `rtc_pm8xxx`, `pm8941_pwrkey`, `leds_qcom_lpg` | keep these PMIC drivers |
| Thermal | 67 zones/cooling devices (`qcom_tsens`, `qcom_spmi_temp_alarm`) | keep qcom thermal |
| Userland | aarch64; 64-bit | `CONFIG_COMPAT` (aarch32) likely droppable — verify no 32-bit apps |

### Corrections to the generic analysis below (apply these overrides)
- **CAN is REAL** → reverse §9: do **not** drop SPI CAN; keep `CAN_MCP251XFD`.
- **ASIX USB-Ethernet is REAL** → keep `USB_NET_AX88179_178A` (it is the only
  wired NIC). You may still drop the *PCI* `NET_VENDOR_*` list — there is no
  PCIe/onboard NIC.
- **UFS + MMC + NVMe all REAL** → keep all three storage stacks.
- **Thunderbolt/USB4, Type-C/UCSI, DP redrivers (ps883x, ptn3222) REAL** → keep;
  these were not in the generic KEEP-LIST.
- **WiFi is ath12k only** → within ATH, you may drop ATH10K/ATH11K.
- **Sensors: none on the EVK** → IIO trimming is safe *for this board*, but a
  shipping robot usually adds an IMU/ToF; keep the specific part you fit.
- Everything else in the generic guide (DVB/TV, server NICs/HBAs, FireWire,
  RapidIO, VME, Hyper-V/Xen, foreign-SoC audio/media, legacy FS) is confirmed
  unused and safe to drop.

---

## 1. Foreign architecture / SoC platform support

The config is already `ARCH_*`-gated, but three non-qcom platforms are still on:

- **C** `CONFIG_ARCH_BST` — Black Sesame; not qcom.
- **C** `CONFIG_ARCH_MICROCHIP`, `CONFIG_ARCH_LAN969X` — Microchip SoC.
  Disabling these drops their pinctrl/clk/net leftovers.
- **C** Cross-arch emulation / foreign binfmt you don't need:
  `CONFIG_COMPAT` only if you run **no** 32-bit aarch32 userspace (many robotics
  stacks are pure 64-bit → cautious-to-normal).

Most other vendor SoC drivers (Tegra, Exynos, Rockchip, MediaTek, i.MX…) are
already off via `ARCH_*`; only a handful of arch-independent stragglers remain
(TI=29, Xilinx=15, HiSilicon=10, Broadcom=10) — handled in their subsystems.

## 2. Legacy / unused buses & interconnects

All obviously absent on a modern qcom robotics SoC:

- **C** `CONFIG_FIREWIRE*` / IEEE1394 (5 syms)
- **C** `CONFIG_RAPIDIO*` (9) — datacenter fabric
- **C** `CONFIG_VME_*` (4) — VMEbus
- **C** `CONFIG_PCCARD` / `CONFIG_PCMCIA` — laptop card slots
- **C** `CONFIG_HSI` / HSI clients (2)
- **C** `CONFIG_ISDN` / `CONFIG_MISDN*` (16) — telephony
- **C** `CONFIG_HAMRADIO`, `CONFIG_AX25`, `CONFIG_NETROM`, `CONFIG_ROSE` —
  packet radio
- **N** `CONFIG_MTD_*` raw/parallel NAND + `CONFIG_JFFS2_FS`/`CONFIG_UBIFS_FS`
  /`CONFIG_MTD_CFI`/`CONFIG_MTD_DOC*` — bare NOR/NAND flash; qcom robotics boots
  from UFS/eMMC/NVMe, not parallel flash. (Keep `MTD` only if you use SPI-NOR.)

## 3. Networking — biggest single win (~1,800 modules)

### 3a. NIC drivers (`NET_VENDOR_*`, ~1,073 mods)
Almost every vendor is enabled. Keep only what the board has.
- **N** Keep: `NET_VENDOR_QUALCOMM`. **Keep `USB_NET_AX88179_178A`** — the
  reference unit's only wired NIC is a USB ASIX AX88179 dongle. (The PCI
  `NET_VENDOR_ASIX`/`SMSC`/`REALTEK` cards can still go — no PCIe/onboard NIC.)
- **C** Disable datacenter/server NICs: `NET_VENDOR_MELLANOX`, `CHELSIO`,
  `EMULEX`, `QLOGIC`, `BROCADE`, `CAVIUM`, `NETRONOME`, `SOLARFLARE`,
  `PENSANDO`, `FUNGIBLE`, `HUAWEI`, `GOOGLE`, `AMAZON`, `META`, `MICROSOFT`,
  `CISCO`, `MYRI`, `ALTEON`, `TEHUTI`, `WANGXUN`, `MUCSE`, `ADAPTEC`,
  `ALACRITECH`, `AGERE`.
- **C** Disable legacy desktop NICs: `NET_VENDOR_3COM`, `DEC`, `DLINK`,
  `NATSEMI`, `8390`, `SIS`, `VIA`, `SILAN`, `RDC`, `PACKET_ENGINES`, `OKI`,
  `SEEQ`, `I825XX`, `SUN`, `WIZNET`.
- **C** Disable foreign-SoC NICs: `NET_VENDOR_FREESCALE`, `HISILICON`,
  `MARVELL`, `RENESAS`, `SAMSUNG`, `SOCIONEXT`, `XILINX`, `TI`, `NVIDIA`,
  `AMD`, `ROCKER`, `NI`, `LITEX`, `EZCHIP`, `CORTINA`, `VERTEXCOM`, `ARC`,
  `ADI`, `MICROSEMI`, `CADENCE`.
- **C** `CONFIG_NET_DSA` + all 52 DSA switch drivers — enterprise managed
  switches; not on a robot.
- **N** Ethernet PHYs (109): keep `QCOM_NET_PHYLIB`, the Atheros/Marvell/Micrel
  PHY actually wired to your MAC; drop the rest.

### 3b. WiFi (`WLAN_VENDOR_*`, ~417 mods)
- **N** Keep only `WLAN_VENDOR_ATH` and within it **`ATH12K`** (WCN785x /
  FastConnect 7800). You may drop `ATH10K`/`ATH11K`/`ATH9K` etc.
- **C** Disable `WLAN_VENDOR_INTEL`, `BROADCOM`, `MEDIATEK`, `REALTEK`,
  `RALINK`, `MARVELL`, `ATMEL`, `ADMTEK`, `INTERSIL`, `MICROCHIP`, `PURELIFI`,
  `RSI`, `SILABS`, `ST`, `TI`, `ZYDAS`, `QUANTENNA`.
  (Keep one USB-WiFi vendor only if you ship a specific dongle.)

### 3c. Networking protocol stack (~629 syms)
- **N** `CONFIG_IP_VS*` (IPVS load balancer), `CONFIG_NET_SCH_*` exotic qdiscs,
  `CONFIG_NET_CLS_*` exotic classifiers — keep `fq_codel`/`fq`/`htb` only.
- **A** Trim `CONFIG_NETFILTER` Xtables matches/targets you don't use, `NF_CONN*`
  helpers, `CONFIG_IP_SCTP`, `CONFIG_TIPC`, `CONFIG_ATM`, `CONFIG_L2TP`,
  `CONFIG_NET_SCHED` entirely if you do no QoS. **Risk:** container/firewall
  tooling (Docker/ROS networking) may expect iptables/nft + conntrack — verify.
- **A** `CONFIG_BRIDGE`, `CONFIG_VLAN_8021Q`, `CONFIG_MACVLAN`, `CONFIG_VXLAN`,
  `CONFIG_BONDING` — drop only if your network topology is fixed; ROS multi-host
  / container setups often need bridge+veth.

### 3d. Bluetooth + NFC device drivers (141)
- **N** Keep BT core + `BT_HCIUART`/`BT_QCA` (qcom BT over UART, `hci1`) and
  `BT_HCIBTUSB`/btusb (the USB BT at `0cf3:e700`). You may drop the other vendor
  helper modules auto-pulled in: `BT_HCIBTINTEL`, `BT_MTK`, `BT_RTL`, `BT_BCM`.
- **C** `CONFIG_NFC` and all NFC device drivers (31) — unlikely on a robot.

## 4. Media / V4L / DVB (~611 mods)

Camera **must** keep: `VIDEO_DEV`, `MEDIA_CAMERA_SUPPORT`, `VIDEO_QCOM_CAMSS`,
`V4L2`, **`VIDEO_IMX412`** (the EVK's Sony IMX412 sensor) + `PHY_QCOM_MIPI_CSI2`,
and the qcom codec — on X1E80100 this is **`VIDEO_QCOM_IRIS`** (`qcom_iris`),
not the older Venus. Drop the other ~200 CMOS sensor drivers.

- **C** `CONFIG_MEDIA_ANALOG_TV_SUPPORT`, `CONFIG_MEDIA_DIGITAL_TV_SUPPORT`,
  all `CONFIG_DVB_*` (**188 modules**), all TV tuners, ATSC/ISDB/DVB-S/T/C
  frontends, analog/digital TV USB devices — no broadcast TV on a robot.
- **C** `CONFIG_MEDIA_RADIO_SUPPORT` + `CONFIG_RADIO_*` (11), `CONFIG_MEDIA_SDR_SUPPORT`.
- **N** `CONFIG_MEDIA_USB_SUPPORT` webcam/UVC: keep `USB_VIDEO_CLASS` only if you
  use a UVC USB camera; otherwise drop. Drop the em28xx/cx231xx/au0828 hybrid
  TV capture families regardless.
- **N** `CONFIG_RC_CORE` + IR decoders (7) — infrared remotes; rarely on robots.
- **C** Foreign media platform drivers (Allegro, Amphion, Aspeed, Mediatek,
  Renesas, Rockchip, Samsung, Sunxi, TI, Verisilicon, Xilinx, StarFive) — keep
  only `# Qualcomm media platform drivers`.

Set `MEDIA_SUPPORT_FILTER` and uncheck TV/radio/SDR/analog at the top level —
this is the cleanest way to drop the whole DVB/tuner tree at once.

## 5. Graphics / DRM (~429 mods)

Keep `DRM_MSM` and the panel/bridge actually on your board.
- **C** Foreign display controllers: `DRM_AST`, `DRM_HDLCD`, `DRM_KOMEDA`
  (ARM Mali DP), `DRM_KMB`, plus any GPU driver that isn't MSM.
- **N** `CONFIG_DRM_*` bridge chips (~40, Lontium/TI/Toshiba/Parade/Analogix…)
  and `CONFIG_DRM_PANEL_*` (~120): **board-dependent — keep the one(s) your
  display path uses** (qcom robotics HDMI often uses `DRM_LONTIUM_LT9611` or
  `DRM_TI_SN65DSI83`). Drop all others.
- **N** Legacy FB: `CONFIG_FB_*` hardware framebuffers (most are PCI/foreign),
  keep `FB`, `DRM_FBDEV_EMULATION` only.
- **C** `CONFIG_DRM_HYPERV`, `CONFIG_DRM_VKMS`, `CONFIG_DRM_VMWGFX` virt GPUs.

## 6. Sound (~627 mods)

Keep `SND_SOC_QCOM` (+ qcom codecs: `SND_SOC_WCD*`, `SND_SOC_WSA*`,
`SND_SOC_LPASS*`), `SND_SOC_DMIC`, `SND_USB_AUDIO` (USB mic/speaker on robots).
- **C** `CONFIG_SND_HDA_*` (HD-Audio / PCI codecs) unless you have an HDA bus.
- **C** Foreign SoC ASoC: Freescale, Tegra, Intel, AMD, Mediatek, STM32,
  Spreadtrum, Xilinx, Atmel, TI Davinci, etc. — keep only Qualcomm.
- **N** `CONFIG_SND_SOC_*` CODEC drivers (huge list): keep just the codecs on
  your board; drop the rest. Drop `SND_SOC_SOF_*` (Intel/AMD DSP).
- **C** `CONFIG_SND_PCMCIA`, `CONFIG_SND_ISA`, legacy `SND_*` MIDI/OPL3/sequencer
  if no MIDI.

## 7. Input / HID (~511 mods)

- **N** HID special drivers (140): keep generic + `HID_MULTITOUCH`, the gamepad
  you use; drop vendor gaming/peripheral drivers (Sony, Nintendo, Logitech G,
  Razer, Corsair, Wacom…) unless needed for teleop.
- **C** `CONFIG_INPUT_JOYDEV` joysticks/gamepads — keep only if used for teleop.
- **N** Touchscreen/keyboard/mouse drivers for chips not on the board (300+).
- **C** `CONFIG_INPUT_TABLET`, legacy `CONFIG_GAMEPORT`, `CONFIG_SERIO_*` for
  PS/2 / parallel.

## 8. Sensors — IIO (~524 mods)  ⚠ robotics-sensitive

Robots **do** use IMUs/accel/gyro/mag/light/ToF — be selective here.
**On this EVK no IIO sensor is populated**, so the whole ~524-driver tree is
safe to drop *for the reference unit*. If your carrier adds sensors:
- **N** Keep the exact IMU/sensor parts you fit (e.g. `BMI160`, `ICM42600`,
  `LSM6DSx`, `MPU6050`, `AK8975`, `VL53L0X`). Drop the rest.
- **C** `CONFIG_IIO_HEALTH*` (heart-rate), `CONFIG_IIO_CHEMICAL_*` (gas),
  `CONFIG_IIO_POTENTIOSTAT`, humidity, color/proximity parts you don't fit.
- **C** SSP / HID-sensor / SCMI-sensor framework if unused.

## 9. Smaller per-driver clusters (mostly **N**, keep board parts)

- **N** RTC (119): keep your one RTC (often qcom PMIC `RTC_DRV_PM8XXX`); drop the
  rest of the I2C/SPI RTC chip list.
- **N** LED (109): keep the LED controller you fit; drop the rest.
- **N** GPIO expanders — I2C/SPI/MFD/PCI/USB expander chips (~80). Keep only the
  expander on your board; drop the rest. **C** for `CONFIG_GPIO_*` on foreign
  SoCs.
- **N** Watchdog: keep `QCOM_WDT`. **C** all `# PCI-based` and `# USB-based`
  watchdog cards.
- **N** Multifunction (MFD), regulators, hwmon, power-supply, thermal: keep qcom
  + the discrete parts on your board; drop foreign-SoC and unrelated chip drivers.
- **KEEP `CAN_MCP251XFD`** (+ `CAN_DEV`, `CAN_RAW`, `CAN_BCM`) — the EVK's
  `can0` is a Microchip MCP251xFD CAN-FD controller on SPI and **is in use**.
  Drop only the *other* USB/SPI CAN dongles you don't ship.
- **C** `CONFIG_W1` (1-wire) masters/slaves if no 1-wire devices.
- **C** EEPROM/`MISC` device drivers for unrelated chips.

## 10. Storage controllers

Keep: `BLK_DEV_NVME`, `SCSI`, `BLK_DEV_SD`, `UFS`/`SCSI_UFS_QCOM`, `MMC_SDHCI*`,
`USB_STORAGE`, `ATA`/`AHCI` only if you have SATA.
- **C** `CONFIG_PATA_*` (parallel IDE/PATA SFF, ~30) — none on a qcom robot.
- **C** Legacy/foreign SATA `CONFIG_SATA_*` SFF controllers except the one you use.
- **C** Server `CONFIG_SCSI_*` HBAs (megaraid, mpt3sas, aacraid, qla2xxx,
  lpfc, hisi_sas, pmcraid…) — datacenter RAID/FC, not robotics.

## 11. Virtualization & cloud guest

- **C** `CONFIG_HYPERV*` (8) — Microsoft Hyper-V guest.
- **C** `CONFIG_XEN*` (35) — Xen guest.
- **A** `CONFIG_KVM` / `CONFIG_VHOST_*` host virtualization — drop unless you run
  VMs/containers needing vhost-net on the robot.
- **N** `CONFIG_VFIO*` unless you do device passthrough.

## 12. Filesystems

Keep: `EXT4`, `F2FS`, `SQUASHFS`, `EROFS`, `VFAT`/`FAT`/`EXFAT`, `OVERLAY_FS`,
`TMPFS`, `CONFIGFS`, `DEBUGFS`, `FUSE`, `NFS` client (if used).
- **C** Foreign/legacy FS — `JFS`, `XFS`(unless used), `GFS2`, `OCFS2`,
  `NILFS2`, `NTFS_FS` (old), `ADFS`, `AFFS`, `HFS`, `HFSPLUS`, `BEFS`, `EFS`,
  `VXFS`, `MINIX_FS`, `OMFS`, `QNX4FS`, `QNX6FS`, `UFS`, `CRAMFS`, `ROMFS`,
  `SYSV`, `JFFS2`, `UBIFS`. Cluster FS (GFS2/OCFS2) are pure datacenter.
- **N** `CONFIG_BTRFS_FS`, `CONFIG_NTFS3_FS` — keep only if your storage uses them.
- **C** Exotic network FS: `CEPH`, `CIFS`/SMB (keep only if you mount shares),
  `AFS`, `CODA`, `NCP`, `9P` (keep 9P only for virtio dev workflows).

## 13. Aggressive / deep trims (verify each — may break features)

- **A** Debug/trace: `CONFIG_FTRACE` family, `CONFIG_KPROBES`,
  `CONFIG_DEBUG_INFO*` (huge build-size/perf), `CONFIG_KASAN`, `CONFIG_KCOV`,
  `CONFIG_PROVE_LOCKING`, `CONFIG_DEBUG_KMEMLEAK`. Keep minimal `DEBUG_FS` +
  `DYNAMIC_DEBUG`. **Risk:** ROS/profiling tools, BPF tracing.
- **A** `CONFIG_BPF_SYSCALL` ecosystem — keep; many robotics/observability and
  systemd features need it. Only trim BPF JIT extras.
- **A** Crypto: drop algorithms/test modules you don't use (`CRYPTO_*` ciphers
  like Camellia/CAST/Serpent/Twofish/Blowfish, `CRYPTO_USER`,
  `CRYPTO_*_TEST`). Keep AES/SHA/GCM + `CRYPTO_DEV_QCOM_RNG`/qce. **Risk:** disk
  encryption / TLS / module signing.
- **A** `CONFIG_USB_GADGET` precomposed configs — keep only the gadget functions
  you expose (often `g_ether`/ADB/`f_fs`); drop printer/midi/uvc-gadget/mass-
  storage gadget if unused.
- **A** Namespaces/cgroups subsets: keep all if you run containers (ROS 2 +
  Docker/Podman commonly do) — trimming these is high-risk.
- **A** `CONFIG_NUMA`, `CONFIG_HUGETLBFS`, `CONFIG_TRANSPARENT_HUGEPAGE`,
  `CONFIG_MEMORY_HOTPLUG`, `CONFIG_KSM` — server memory features; safe to drop on
  a fixed-RAM SoC but measure.
- **A** `CONFIG_PROFILING`, `CONFIG_PERF_EVENTS` extras, `CONFIG_GCOV` — drop GCOV;
  keep PERF if you profile.

---

## KEEP-LIST (verified against the live X1E80100 EVK — do not disable)
- SoC/firmware: `ARCH_QCOM`, `QCOM_SCM`, `QCOM_SMEM`, `QCOM_RPMH*`,
  `RPMSG_QCOM_*`, `QCOM_Q6V5*`/`REMOTEPROC` (`qcom_q6v5_pas`), `QRTR*`,
  `MHI_BUS` (ath12k/qrtr_mhi), `QCOM_PD_MAPPER`, `QCOM_PBS`, `QCOM_SPMI_*`,
  `QCOM_STATS`, `QCOM_SOCINFO`, `QCOM_LLCC`, `QCOM_OCMEM`, `QCOM_TEE`.
- Clocks/pinctrl/power: `COMMON_CLK_QCOM`, `QCOM_GDSC`, `PINCTRL_X1E80100` +
  `PINCTRL_*_LPASS_LPI`, `REGULATOR_QCOM_*`, `QCOM_CPUFREQ_*`,
  `INTERCONNECT_QCOM` (+ `ICC_BWMON`), `RTC_DRV_PM8XXX`, `QCOM_PM8941_PWRKEY`,
  `LEDS_QCOM_LPG`, `PMIC_GLINK`/`QCOM_BATTMGR`.
- Buses/IO: `PCIE_QCOM`, `PHY_QCOM_QMP*`, `SPI_QCOM_GENI` (CAN sits here),
  `I2C_QCOM_GENI`/`I2C_QCOM_CCI`, `QCOM_GPI_DMA`, `SPMI`, `QCOM_PDC`,
  `QCOM_GENI_SE`, `SERIAL_QCOM_GENI`, `SLIMBUS`, `SOUNDWIRE_QCOM`.
- Storage: `SCSI_UFS_QCOM` + `SCSI_UFSHCD_PLATFORM` (**UFS = rootfs**),
  `BLK_DEV_NVME`, `MMC_SDHCI_MSM` (mmc0 present).
- USB/USB-C/USB4: `USB_DWC3_QCOM`, `USB_XHCI_PLATFORM`, `TYPEC`, `TYPEC_UCSI`,
  `UCSI_PMIC_GLINK`, `PMIC_GLINK_ALTMODE`, `PHY_QCOM_QMP_COMBO`,
  `PHY_QCOM_EUSB2*`, `PHY_SNPS_EUSB2`, `PHY_NXP_PTN3222`, `DRM_AUX_BRIDGE`,
  `TYPEC_MUX_GPIO_SBU`, **`USB4`/`THUNDERBOLT`**, `USB_CONFIGFS`.
- Display: `DRM_MSM`, `DRM_PANEL_EDP`, `PHY_QCOM_EDP`, `DRM_DISPLAY_HELPER`,
  `DRM_DP_AUX_BUS`, `BACKLIGHT_PWM`. (Parade `ps883x` DP redriver is in PHY.)
- Camera/codec: `VIDEO_QCOM_CAMSS`, `VIDEO_QCOM_IRIS`, `VIDEO_IMX412`,
  `PHY_QCOM_MIPI_CSI2`, V4L2/videobuf2 core.
- Audio: `SND_SOC_X1E80100`, `SND_SOC_WCD938X*`, `SND_SOC_WSA884X`,
  `SND_SOC_LPASS_*_MACRO`, `SND_SOC_QCOM_COMMON/SDW`, `SND_SOC_HDMI_CODEC`,
  `SND_SOC_Q6APM*`/`Q6PRM`, `SND_USB_AUDIO`.
- Net: `WLAN_VENDOR_ATH`+`ATH12K`, `NET_VENDOR_QUALCOMM`,
  **`USB_NET_AX88179_178A`** (the wired NIC), `QRTR`, `CFG80211`/`MAC80211`.
- Bluetooth: `BT`, `BT_HCIUART`, `BT_QCA`, `BT_HCIBTUSB`, `BT_RFCOMM`, `BT_BNEP`.
- CAN: **`CAN_DEV`, `CAN_MCP251XFD`, `CAN_RAW`** (can0 in use).
- Crypto/thermal/watchdog: `CRYPTO_DEV_QCE`, `QCOM_RNG`, `QCOM_TSENS`,
  `QCOM_SPMI_TEMP_ALARM`, `QCOM_WDT`, `QCOM_EDAC`, `CORESIGHT*` (optional debug).
