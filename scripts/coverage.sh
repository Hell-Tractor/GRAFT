#!/bin/bash

CRATE_NAME=$1
if [ -z "$CRATE_NAME" ]; then
    echo "Usage: $0 <CRATE_NAME>"
    exit 1
fi

# --- Locate LLVM tools from rustup ---
# This identifies the bin directory for the current rustc toolchain
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

HARNESS_FILE="main.rs"
TOPLEVEL="$(git rev-parse --show-toplevel)"
TESTCASES_BASE="$TOPLEVEL/output/$CRATE_NAME/testcases"
COV_RAW_DIR="$TOPLEVEL/output/$CRATE_NAME/coverage_raw"
PROFDATA_FILE="$TOPLEVEL/output/$CRATE_NAME/total_coverage.profdata"

# 1. Initialization
mkdir -p "$COV_RAW_DIR"
rm -f "$COV_RAW_DIR"/*.profraw

export LLVM_PROFILE_FILE="$COV_RAW_DIR/test-%p-%m.profraw"
export RUSTFLAGS="-C instrument-coverage"

id=0
object_files=()

# 2. Iterative Compilation and Data Collection
for folder in "$TESTCASES_BASE"/*/; do
    echo "==============================================="
    echo "[INFO] Processing Testcase ID: $id"
    echo "[INFO] Directory: $folder"

    COVERAGE_TARGET_DIR="${folder}target/coverage"

    echo "[INFO] Compiling coverage-instrumented binary..."
    cargo build --manifest-path "${folder}Cargo.toml" --target-dir "$COVERAGE_TARGET_DIR" > /dev/null 2>&1

    BINARY_PATH="${COVERAGE_TARGET_DIR}/debug/${CRATE_NAME}_test${id}"

    if [ ! -f "$BINARY_PATH" ]; then
        echo "[ERROR] Compilation failed or binary not found: $BINARY_PATH"
        id=$((id + 1))
        continue
    fi

    object_files+=("-object" "$BINARY_PATH")

    echo "[INFO] Executing testcases for coverage collection..."

    find "${folder}outputs/default/queue" "${folder}outputs/default/crashes" -maxdepth 1 -type f ! -name "*.txt" 2>/dev/null | while read -r input_file; do
        $BINARY_PATH < "$input_file" > /dev/null 2>&1
    done

    id=$((id + 1))
done

# 3. Data Aggregation
echo "==============================================="
echo "[INFO] Merging raw profile data into $PROFDATA_FILE..."
llvm-profdata merge -sparse "$COV_RAW_DIR"/*.profraw -o "$PROFDATA_FILE"

# 4. Report Generation
echo -e "\n--- Coverage Report Summary ---"
llvm-cov report \
    "${object_files[@]}" \
    -instr-profile="$PROFDATA_FILE" \
    -ignore-filename-regex="rustc|testcases|$HARNESS_FILE"
    # "$CRATE_NAME"

echo -e "\n--- Generating detailed coverage report ---"
llvm-cov show \
    "${object_files[@]}" \
    -instr-profile="$PROFDATA_FILE" \
    -show-instantiations \
    -show-line-counts-or-regions \
    -ignore-filename-regex="rustc|testcases|$HARNESS_FILE" \
    --format=text > "$TOPLEVEL/output/$CRATE_NAME/coverage_detail.txt"

echo "==============================================="
echo "[SUCCESS] Detailed coverage analysis completed."
echo "[INFO] Report location: $TOPLEVEL/output/$CRATE_NAME/coverage_detail.txt"