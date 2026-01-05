#!/bin/bash

CRATE_NAME=$1
if [ -z "$CRATE_NAME" ]; then
    echo "Crate name not provided." >&2
    echo "Usage: $0 <CRATE_NAME>"
    exit 1
fi

total_count=0
TOPLEVEL="$(git rev-parse --show-toplevel)"
TESTCASES_BASE="$TOPLEVEL/output/$CRATE_NAME/testcases"

if [ ! -d "$TESTCASES_BASE" ]; then
    echo "Path $TESTCASES_BASE not exists." >&2
    exit 1
fi

for folder in "$TESTCASES_BASE"/*/; do
    # ${folder%/} 是为了去掉末尾多余的斜杠
    TARGET_DIR="${folder}outputs/default/crashes"

    if [ -d "$TARGET_DIR" ]; then
        current_count=$(find "$TARGET_DIR" -maxdepth 1 -type f ! -name "*.txt" | wc -l)
        total_count=$((total_count + current_count))
    fi
done

echo "crashes found: $total_count"