# Live platform observations — config trimming effort

Probed: 2026-06-30 via `ssh ubuntu@192.168.1.123`.

## Identity
- Model: **Qualcomm Technologies, Inc. Hamoa IoT EVK**
- SoC: **X1E80100** (Snapdragon X Elite) — DT compatible:
  `qcom,hamoa-iot-evk` / `qcom,hamoa-iot-som` / `qcom,x1e80100`
- OS: Ubuntu 26.04 LTS, kernel `7.0.0-1006-qcom` aarch64
- Loaded modules: **177** (vs ~6,900 the generic config builds) →
  see `live-loaded-modules.txt`

## Hardware inventory (what is actually present / in use)

### Storage
- **UFS** = root/boot: `sda` 238G + `sdb` 64M (Kioxia THGJFJT1E45BATPC),
  driver `ufs_qcom`/`ufshcd_core`/`ufshcd_pltfrm`. **rootfs lives here.**
- **NVMe**: `nvme0n1` 238G (KIOXIA KBG50ZNS256G / BG5 DRAM-less), `nvme` driver.
- **MMC**: `mmc0` host present (built-in sdhci_msm; no module).
- No SATA / ATA ports.

### PCIe (lspci)
- 4× SC8380XP PCIe root complexes (qcom).
- `0004:01:00.0` Qualcomm **WCN785x Wi-Fi 7 / FastConnect 7800** → `ath12k`.
- `0006:01:00.0` KIOXIA NVMe BG5.
- No discrete/3rd-party PCI NIC or other add-in cards.

### Networking
- `wlP4p1s0` — WiFi, driver `ath12k_wifi7_pci` (ath12k). Only ath12k loaded.
- `enx000ec6817901` — **USB ASIX AX88179** GbE (`ax88179_178a` + `usbnet`).
  This is the ONLY wired NIC. (lsusb: `0b95:1790 ASIX AX88179`.)
- `can0` — **Microchip MCP251xFD CAN-FD over SPI** (`mcp251xfd`, bus `spi0.0`,
  `can_dev`). CAN bus is real on this platform.

### Bluetooth
- `hci1` QCA over UART (`btqca`/`hci_uart`); `hci0`.
- USB BT `0cf3:e700` Qualcomm Atheros (`btusb`).
- Extra vendor helpers auto-loaded but unused: btintel, btmtk, btrtl, btbcm.

### Camera / video
- qcom **CAMSS** (`qcom_camss`, msm_vfe0..3, /dev/video0..15, /dev/media0).
- Sensor: **Sony IMX412** (`imx412`), `phy_qcom_mipi_csi2`.
- Codec: **qcom IRIS** (`qcom_iris`) → /dev/video16 decoder, video17 encoder.

### Display
- DRM driver **`msm_dpu`**: connectors DP-1, DP-2, DP-3, eDP-1, Writeback-1.
- `panel_edp`, `phy_qcom_edp`, `drm_display_helper`, `drm_dp_aux_bus`.
- No external DRM bridge chips used; DP path uses native + redrivers (below).

### USB / USB-C / USB4
- `dwc3`, `dwc3_qcom_legacy`, `xhci_plat_hcd`.
- Type-C/PD: `typec`, `typec_ucsi`, `ucsi_glink`, `pmic_glink`,
  `pmic_glink_altmode`, `gpio_sbu_mux`.
- DP-altmode redrivers/phys: **Parade `ps883x`**, **NXP `phy_nxp_ptn3222`**,
  `phy_qcom_qmp_combo`, `phy_qcom_eusb2_repeater`, `phy_snps_eusb2`.
- **`thunderbolt`** (USB4) loaded.

### Audio
- SoC card `snd_soc_x1e80100` ("X1E80100-EVK").
- Codecs: **WCD938x** (`snd_soc_wcd938x*`), **WSA884x** (`snd_soc_wsa884x`),
  LPASS macros (rx/tx/va/wsa), `snd_soc_hdmi_codec`.
- DSP/bus: `q6apm`, `q6prm`, `q6dsp`, `soundwire_qcom`, `slimbus`.
- Inputs: Headset Jack, DP0 Jack, DP1 Jack.

### Sensors (IIO)
- `industrialio` loaded but **0 IIO devices populated** on this EVK.

### Power / misc
- RTC `rtc_pm8xxx`; power key `pm8941_pwrkey`; LEDs `leds_qcom_lpg` +
  `led_class_multicolor`; battery `qcom_battmgr`.
- Thermal: 67 zones/cooling devices (`qcom_tsens`, `qcom_spmi_temp_alarm`).
- Debug: coresight (etm4x/funnel/replicator/tpdm) loaded.
- Crypto: ARM CE accel (aes_ce, ghash_ce, sm3/sm4_ce) + `qcrypto`/`qcom_rng`.
- `dm_multipath`, `binfmt_misc` loaded; userland 64-bit (COMPAT likely droppable).

## Implications for trimming (deltas vs first-pass generic guide)
1. KEEP UFS, NVMe, MMC (all present; UFS = rootfs).
2. KEEP `CAN_MCP251XFD` + CAN core — CAN bus in use.
3. KEEP `USB_NET_AX88179_178A` — sole wired NIC.
4. KEEP USB4/Thunderbolt, Type-C/UCSI, ps883x + ptn3222 redrivers.
5. WiFi: ath12k only → ath10k/ath11k droppable.
6. Camera codec is IRIS (not Venus); sensor IMX412.
7. IIO sensor tree (~520 drivers) safe to drop on this EVK; keep a slot if a
   shipping robot adds an IMU/ToF.
8. Confirmed-absent (safe to drop): all PCI/server NICs + DSA, 16 non-ath WiFi
   vendors, DVB/TV (188 mods), FireWire/RapidIO/VME/PCMCIA/ISDN/HAM, Hyper-V/Xen,
   datacenter SCSI HBAs + PATA, HD-Audio + foreign-SoC audio/media, legacy/cluster
   filesystems, leftover ARCH_BST/MICROCHIP/LAN969X.

See `config-trimming-guide.md` for the full subsystem breakdown and KEEP-LIST.
