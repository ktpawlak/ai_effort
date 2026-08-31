Kernel config needed:

CONFIG_OF_OVERLAY=y
CONFIG_OF_CONFIGFS=y     # exposes the configfs interface
CONFIG_CONFIGFS_FS=y

Steps:

# 1. Mount configfs (usually already mounted)
mount -t configfs none /sys/kernel/config

# 2. Compile your overlay source to a .dtbo
dtc -@ -I dts -O dtb -o my_overlay.dtbo my_overlay.dts
#   -@  generates __symbols__ needed for overlays to resolve phandles

# 3. Create an overlay directory and load the blob
mkdir /sys/kernel/config/device-tree/overlays/myoverlay
cat my_overlay.dtbo > /sys/kernel/config/device-tree/overlays/myoverlay/dtbo

# 4. Check status (should say "applied")
cat /sys/kernel/config/device-tree/overlays/myoverlay/status

# 5. To remove/unapply
rmdir /sys/kernel/config/device-tree/overlays/myoverlay

Overlay  .dts  skeleton:

/dts-v1/;
/plugin/;

&{/soc/i2c@...} {          // or a label like &i2c1
    my_device@50 {
        compatible = "vendor,mydev";
        reg = <0x50>;
    };
};

