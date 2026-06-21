#!/bin/bash
# Параметри: $1 - новий файл, $2 - старий файл, $3 - PID

SOURCE="$1"
TARGET="$2"
PID="$3"

echo "TensaLauncher Updater"
echo "====================="
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
sleep 2

# Перевірка файлів
if [ ! -f "$SOURCE" ]; then
    echo "ERROR: Update file not found: $SOURCE"
    exit 1
fi

if [ ! -f "$TARGET" ]; then
    echo "ERROR: Target file not found: $TARGET"
    exit 1
fi

echo "Replacing old version..."

# Видалення старого
retry=0
while [ -f "$TARGET" ] && [ $retry -lt 10 ]; do
    rm -f "$TARGET" 2>/dev/null
    if [ -f "$TARGET" ]; then
        sleep 1
        retry=$((retry + 1))
    fi
done

if [ -f "$TARGET" ]; then
    echo "ERROR: Cannot delete old file"
    exit 1
fi

# Переміщення нового
mv -f "$SOURCE" "$TARGET"
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to replace file"
    exit 1
fi

# Права на виконання
chmod +x "$TARGET"

echo ""
echo "Update completed successfully!"
echo ""

# Видалення скрипта
rm -- "$0"
