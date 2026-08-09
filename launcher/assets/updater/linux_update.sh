#!/bin/sh

SOURCE=$1
TARGET=$2
PID=$3
MARKER=$4
STAGED="${TARGET}.new"
BACKUP="${TARGET}.bak"
HAD_TARGET=0

cleanup_marker_if_runnable() {
    if [ -x "$TARGET" ] && [ -n "$MARKER" ]; then
        rm -f -- "$MARKER"
    fi
}

restore_previous() {
    rm -f -- "$TARGET"
    if [ "$HAD_TARGET" -eq 1 ] && [ -f "$BACKUP" ]; then
        mv -f -- "$BACKUP" "$TARGET" && chmod +x "$TARGET"
    fi
    cleanup_marker_if_runnable
}

counter=0
while kill -0 "$PID" 2>/dev/null; do
    if [ "$counter" -ge 60 ]; then
        echo "ERROR: Timeout waiting for launcher to close"
        exit 1
    fi
    sleep 1
    counter=$((counter + 1))
done

if [ ! -f "$SOURCE" ]; then
    echo "ERROR: Update file not found: $SOURCE"
    cleanup_marker_if_runnable
    exit 1
fi

rm -f -- "$STAGED"
cp -f -- "$SOURCE" "$STAGED" || {
    echo "ERROR: Failed to stage update"
    cleanup_marker_if_runnable
    exit 1
}
if [ ! -s "$STAGED" ]; then
    echo "ERROR: Staged update is empty"
    rm -f -- "$STAGED"
    cleanup_marker_if_runnable
    exit 1
fi
chmod +x "$STAGED" || {
    echo "ERROR: Failed to make staged update executable"
    rm -f -- "$STAGED"
    cleanup_marker_if_runnable
    exit 1
}

rm -f -- "$BACKUP"
if [ -f "$TARGET" ]; then
    HAD_TARGET=1
    mv -f -- "$TARGET" "$BACKUP" || {
        echo "ERROR: Failed to back up current launcher"
        rm -f -- "$STAGED"
        cleanup_marker_if_runnable
        exit 1
    }
fi

if ! mv -f -- "$STAGED" "$TARGET"; then
    echo "ERROR: Failed to activate update; restoring previous launcher"
    restore_previous
    exit 1
fi
chmod +x "$TARGET" || {
    echo "ERROR: Updated launcher is not executable; restoring previous launcher"
    restore_previous
    exit 1
}

rm -f -- "$BACKUP" "$SOURCE"
if [ -n "$MARKER" ]; then
    rm -f -- "$MARKER"
fi
echo "TensaLauncher update completed successfully"
