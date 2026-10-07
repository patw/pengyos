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
# (12 MB); skip the Windows .ani/.cur files. index.theme names the theme
# without Pop's icon directories, which are not installed.
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
	$(INSTALL) -D -m 0644 $(POP_CURSOR_THEME_PKGDIR)/Pop-index.theme \
		$(TARGET_DIR)/usr/share/icons/Pop/index.theme
	$(INSTALL) -D -m 0644 $(POP_CURSOR_THEME_PKGDIR)/default-index.theme \
		$(TARGET_DIR)/usr/share/icons/default/index.theme
endef

$(eval $(generic-package))
