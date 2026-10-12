################################################################################
#
# pop-cursor-theme
#
################################################################################

POP_CURSOR_THEME_VERSION = 3.5.1
POP_CURSOR_THEME_SOURCE = v$(POP_CURSOR_THEME_VERSION).tar.gz
POP_CURSOR_THEME_SITE = https://github.com/pop-os/icon-theme/archive/refs/tags
POP_CURSOR_THEME_LICENSE = CC-BY-SA-4.0
POP_CURSOR_THEME_LICENSE_FILES = COPYING LICENSE

# Pop/cursors holds each cursor under every name it answers to (left_ptr,
# default, arrow, the hash names Qt/GTK ask for) as full copies: 41 MB for 58
# distinct images. Install each image once and the other names as symlinks
# (12 MB); skip the Windows .ani/.cur files.
#
# Pop/scalable is installed too: 584 symbolic SVGs (+ 18 links), 2.3 MB.
# COSMIC's icon theme inherits Pop and relies on it for icons it does not draw
# (help-info-symbolic, view-grid/list-symbolic, network-vpn-symbolic,
# media-playlist-*-symbolic, link-symbolic, …); without them those buttons
# rendered blank (the Store's release-notes button on behoweather, 26.10.25).
# Pop's fixed-size PNG folders (68 MB) are not needed by COSMIC and stay out.
define POP_CURSOR_THEME_INSTALL_TARGET_CMDS
	rm -rf $(TARGET_DIR)/usr/share/icons/Pop/cursors
	mkdir -p $(TARGET_DIR)/usr/share/icons/Pop/cursors
	cd $(@D)/Pop/cursors && \
	for f in *; do \
		case "$$f" in *.ani|*.cur) continue ;; esac; \
		sum=$$(sha256sum "$$f" | cut -d' ' -f1); \
		first=$$(grep -m1 "^$$sum " $(@D)/.cursor-sums 2>/dev/null | cut -d' ' -f2); \
		if [ -n "$$first" ]; then \
			ln -sf "$$first" $(TARGET_DIR)/usr/share/icons/Pop/cursors/"$$f"; \
		else \
			$(INSTALL) -m 0644 "$$f" $(TARGET_DIR)/usr/share/icons/Pop/cursors/"$$f"; \
			echo "$$sum $$f" >> $(@D)/.cursor-sums; \
		fi; \
	done
	rm -f $(@D)/.cursor-sums
	rm -rf $(TARGET_DIR)/usr/share/icons/Pop/scalable
	cp -a $(@D)/Pop/scalable $(TARGET_DIR)/usr/share/icons/Pop/scalable
	$(INSTALL) -D -m 0644 $(POP_CURSOR_THEME_PKGDIR)/Pop-index.theme \
		$(TARGET_DIR)/usr/share/icons/Pop/index.theme
	$(INSTALL) -D -m 0644 $(POP_CURSOR_THEME_PKGDIR)/default-index.theme \
		$(TARGET_DIR)/usr/share/icons/default/index.theme
endef

$(eval $(generic-package))
