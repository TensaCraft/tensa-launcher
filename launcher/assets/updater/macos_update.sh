#!/bin/sh

DMG_PATH=$1
APP_PATH=$2
PID=$3
MARKER=$4
STAGED="${APP_PATH}.new"
BACKUP="${APP_PATH}.bak"
MOUNT_POINT=
HAD_TARGET=0

detach_image() {
    if [ -n "$MOUNT_POINT" ]; then
        hdiutil detach "$MOUNT_POINT" >/dev/null 2>&1 || true
        MOUNT_POINT=
    fi
}

cleanup_marker_if_runnable() {
    if [ -d "$APP_PATH" ] && [ -n "$MARKER" ]; then
        rm -f -- "$MARKER"
    fi
}

restore_previous() {
    rm -rf -- "$APP_PATH"
    if [ "$HAD_TARGET" -eq 1 ] && [ -d "$BACKUP" ]; then
        mv -f -- "$BACKUP" "$APP_PATH"
    fi
    cleanup_marker_if_runnable
}

trap detach_image EXIT INT TERM

counter=0
while kill -0 "$PID" 2>/dev/null; do
    if [ "$counter" -ge 60 ]; then
        echo "ERROR: Timeout waiting for launcher to close"
        exit 1
    fi
    sleep 1
    counter=$((counter + 1))
done

if [ ! -f "$DMG_PATH" ]; then
    echo "ERROR: DMG file not found: $DMG_PATH"
    cleanup_marker_if_runnable
    exit 1
fi

MOUNT_POINT=$(hdiutil attach "$DMG_PATH" -nobrowse 2>/dev/null | awk '/\/Volumes\// {print substr($0, index($0, "/Volumes/")); exit}')
if [ -z "$MOUNT_POINT" ] || [ ! -d "$MOUNT_POINT/TensaLauncher.app" ]; then
    echo "ERROR: TensaLauncher.app was not found in the update image"
    cleanup_marker_if_runnable
    exit 1
fi

rm -rf -- "$STAGED"
ditto "$MOUNT_POINT/TensaLauncher.app" "$STAGED" || {
    echo "ERROR: Failed to stage application bundle"
    cleanup_marker_if_runnable
    exit 1
}
if [ ! -d "$STAGED" ]; then
    echo "ERROR: Staged application bundle is missing"
    cleanup_marker_if_runnable
    exit 1
fi

rm -rf -- "$BACKUP"
if [ -d "$APP_PATH" ]; then
    HAD_TARGET=1
    mv -f -- "$APP_PATH" "$BACKUP" || {
        echo "ERROR: Failed to back up current application"
        rm -rf -- "$STAGED"
        cleanup_marker_if_runnable
        exit 1
    }
fi

if ! mv -f -- "$STAGED" "$APP_PATH"; then
    echo "ERROR: Failed to activate update; restoring previous application"
    restore_previous
    exit 1
fi

rm -rf -- "$BACKUP"
rm -f -- "$DMG_PATH"
if [ -n "$MARKER" ]; then
    rm -f -- "$MARKER"
fi
echo "TensaLauncher update completed successfully"
