#!/bin/bash
# Параметри: $1 - DMG файл, $2 - шлях до .app, $3 - PID

DMG_PATH="$1"
APP_PATH="$2"
PID="$3"

echo "TensaLauncher Updater (macOS)"
echo "=============================="
echo ""
echo "Waiting for launcher to close (PID: $PID)..."

# Чекаємо завершення
counter=0
while kill -0 "$PID" 2>/dev/null; do
    if [ $counter -gt 60 ]; then
        echo "ERROR: Timeout waiting for launcher to close"
        echo "Please close the launcher manually and run this script again"
        exit 1
    fi
    sleep 1
    counter=$((counter + 1))
done

echo "Launcher closed successfully"
echo ""

# Додаткова затримка
if [ ! -f "$DMG_PATH" ]; then
    echo "ERROR: DMG file not found: $DMG_PATH"
    exit 1
fi

echo "Mounting DMG..."

# Монтуємо DMG
MOUNT_POINT=$(hdiutil attach "$DMG_PATH" 2>/dev/null | grep Volumes | awk '{print $3}')

if [ -z "$MOUNT_POINT" ]; then
    echo "ERROR: Failed to mount DMG"
    exit 1
fi

echo "DMG mounted at: $MOUNT_POINT"
echo ""

# Видаляємо старий .app
echo "Removing old version..."
rm -rf "$APP_PATH"

if [ -d "$APP_PATH" ]; then
    echo "ERROR: Cannot delete old application"
    hdiutil detach "$MOUNT_POINT" 2>/dev/null
    exit 1
fi

# Копіюємо новий .app
echo "Installing new version..."

if [ -d "$MOUNT_POINT/TensaLauncher.app" ]; then
    cp -R "$MOUNT_POINT/TensaLauncher.app" "$(dirname "$APP_PATH")/"
else
    echo "ERROR: TensaLauncher.app not found in DMG"
    hdiutil detach "$MOUNT_POINT" 2>/dev/null
    exit 1
fi

# Демонтуємо DMG
echo "Cleaning up..."
hdiutil detach "$MOUNT_POINT" 2>/dev/null

# Видаляємо DMG
rm -f "$DMG_PATH"

echo ""
echo "Update completed successfully!"
echo ""

rm -- "$0"
