#!/bin/bash

CRATE_NAME=$1
OUTPUT_BASE=${2:-}
if [ -z "$CRATE_NAME" ]; then
    echo "Usage: $0 <CRATE_NAME> [output_base_dir]"
    echo "  output_base_dir defaults to output/ (relative to repo root)"
    exit 1
fi

RUST_SYSROOT=$(rustc --print sysroot)
RUST_HOST=$(rustc -vV | grep host | awk '{print $2}')
LLVM_BIN_DIR="$RUST_SYSROOT/lib/rustlib/$RUST_HOST/bin"

if [ -d "$LLVM_BIN_DIR" ]; then
    export PATH="$LLVM_BIN_DIR:$PATH"
else
    echo "[ERROR] LLVM bin directory not found at $LLVM_BIN_DIR"
    echo "[HELP] Run: rustup component add llvm-tools-preview"
    exit 1
fi

TOPLEVEL="$(git rev-parse --show-toplevel)"
if [ -z "$OUTPUT_BASE" ]; then
    OUTPUT_BASE="$TOPLEVEL/output"
fi
TESTCASES_BASE="$OUTPUT_BASE/$CRATE_NAME/testcases"
COV_RAW_DIR="$OUTPUT_BASE/$CRATE_NAME/coverage_raw"
PROFDATA_FILE="$OUTPUT_BASE/$CRATE_NAME/total_coverage.profdata"
# Shared target dir: all testcases of the same crate share compiled deps (huge speedup)
SHARED_TARGET_DIR="$OUTPUT_BASE/$CRATE_NAME/target_coverage"

mkdir -p "$COV_RAW_DIR"
find "$COV_RAW_DIR" -name '*.profraw' -delete

export LLVM_PROFILE_FILE="$COV_RAW_DIR/test-%p-%m.profraw"
export RUSTFLAGS="-Cinstrument-coverage"

object_files=()

for folder in "$TESTCASES_BASE"/*/; do
    BINARY_NAME=$(basename "$folder")
    id=${BINARY_NAME##*_test}
    echo "==============================================="
    echo "[INFO] Processing Testcase ID: $id"
    echo "[INFO] Directory: $folder"

    echo "[INFO] Compiling coverage-instrumented binary (shared target)..."
    cargo build --manifest-path "${folder}Cargo.toml" --target-dir "$SHARED_TARGET_DIR" > /dev/null 2>&1

    BINARY_PATH="${SHARED_TARGET_DIR}/debug/${CRATE_NAME}_test${id}"

    if [ ! -f "$BINARY_PATH" ]; then
        echo "[ERROR] Compilation failed or binary not found: $BINARY_PATH"
        continue
    fi

    object_files+=("-object" "$BINARY_PATH")

    echo "[INFO] Executing testcases for coverage collection..."
    find "${folder}outputs/default/queue" -maxdepth 1 -type f ! -name "*.txt" 2>/dev/null | while read -r input_file; do
        "$BINARY_PATH" < "$input_file" > /dev/null 2>&1
    done
done

echo "==============================================="
echo "[INFO] Merging raw profile data into $PROFDATA_FILE..."
find "$COV_RAW_DIR" -name "*.profraw" > all_files.txt
llvm-profdata merge -sparse -f all_files.txt -o "$PROFDATA_FILE"
rm all_files.txt

echo -e "\n--- Generating detailed coverage report ---"
llvm-cov export \
    "${object_files[@]}" \
    -instr-profile="$PROFDATA_FILE" \
    --format=text \
    --skip-expansions \
    --skip-functions=false \
    --ignore-filename-regex="main\.rs$" \
    > "$OUTPUT_BASE/$CRATE_NAME/coverage_detail.json"

echo "==============================================="
echo "[SUCCESS] Coverage analysis completed."
echo "[INFO] Report: $OUTPUT_BASE/$CRATE_NAME/coverage_detail.json"
