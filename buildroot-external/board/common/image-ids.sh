# Sourced by the post-image scripts. Two builds of one commit must give the
# same disk images, so every ID that genimage or mkfs would otherwise pick at
# random is derived from the root filesystem's UUID (itself derived from
# os-release, see external.mk): one build, one set of IDs; every build new ones.

# image_uuid SEED NAME -> a UUID (version-4 shaped) unique to SEED and NAME
image_uuid() {
    printf '%s/%s' "$1" "$2" | sha256sum |
        sed -E 's/^(.{8})(.{4}).(.{3}).(.{3})(.{12}).*/\1-\2-4\3-8\4-\5/'
}
