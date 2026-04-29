#!/bin/bash
# remove all fuzzing results and binary of the given crate
# but keep the generated testcases and seeds

CRATE_NAME=$1
if [ -z "$CRATE_NAME" ]; then
    echo "Crate name not provided." >&2
    echo "Usage: $0 <CRATE_NAME>"
    exit 1
fi

TOPLEVEL="$(git rev-parse --show-toplevel)"
TESTCASES_BASE="$TOPLEVEL/output/$CRATE_NAME/testcases"

count=0
for folder in "$TESTCASES_BASE"/*/; do
    rm -rf "${folder}target"
    rm -rf "${folder}outputs"
    count=$((count + 1))
    if [ $((count % 10)) -eq 0 ]; then
        echo "Processed $count test binaries..."
    fi
done
echo "Finished clearing fuzzing results for crate $CRATE_NAME. Total test binaries processed: $count"