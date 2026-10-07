################################################################################
#
# ipp-usb
#
################################################################################

IPP_USB_VERSION = 0.9.34
IPP_USB_SITE = $(call github,OpenPrinting,ipp-usb,$(IPP_USB_VERSION))
IPP_USB_LICENSE = BSD-2-Clause
IPP_USB_LICENSE_FILES = LICENSE
IPP_USB_GOMOD = github.com/OpenPrinting/ipp-usb
# cgo: libusb and avahi-client, through pkg-config.
IPP_USB_DEPENDENCIES = host-pkgconf libusb avahi
IPP_USB_TAGS = nethttpomithttp2

# The udev rule + service that start it for a plugged-in IPP-USB device, its
# configuration and the per-device quirks (upstream Makefile's install).
define IPP_USB_INSTALL_DATA
	$(INSTALL) -D -m 0644 -t $(TARGET_DIR)/usr/lib/udev/rules.d $(@D)/systemd-udev/*.rules
	$(INSTALL) -D -m 0644 -t $(TARGET_DIR)/usr/lib/systemd/system $(@D)/systemd-udev/*.service
	$(INSTALL) -D -m 0644 $(@D)/ipp-usb.conf $(TARGET_DIR)/etc/ipp-usb/ipp-usb.conf
	$(INSTALL) -D -m 0644 -t $(TARGET_DIR)/usr/share/ipp-usb/quirks $(@D)/ipp-usb-quirks/*
endef
IPP_USB_POST_INSTALL_TARGET_HOOKS += IPP_USB_INSTALL_DATA

$(eval $(golang-package))
