#!/bin/bash

CRATE_NAME=$1
if [ -z "$CRATE_NAME" ]; then
    echo "Crate name not provided." >&2
    echo "Usage: $0 <CRATE_NAME>"
    exit 1
fi

HARNESS_FILE="main.rs"
TIME_OUT="10s"
TOPLEVEL="$(git rev-parse --show-toplevel)"
TESTCASES_BASE="$TOPLEVEL/output/$CRATE_NAME/testcases"

if [ ! -d "$TESTCASES_BASE" ]; then
    echo "Path $TESTCASES_BASE not exists." >&2
    exit 1
fi

export RUST_BACKTRACE=1

categorize_bug() {
    local log="$1"

    # 按照优先级和匹配精准度排序喵~
    if echo "$log" | grep -q "memory allocation of.*bytes failed"; then
        # 专门抓捕这种巨大的内存分配失败喵！
        echo "out_of_memory"
    elif echo "$log" | grep -q "index out of bounds"; then
        echo "out_of_bounds"
    elif echo "$log" | grep -q "attempt to divide by zero"; then
        echo "division_by_zero"
    elif echo "$log" | grep -q "Option::unwrap()"; then
        echo "unwrap_none"
    elif echo "$log" | grep -q "Result::unwrap()"; then
        echo "unwrap_err"
    elif echo "$log" | grep -q "stack overflow"; then
        echo "stack_overflow"
    elif echo "$log" | grep -q "assertion failed"; then
        echo "assertion_failed"
    elif echo "$log" | grep -q "SIGSEGV"; then
        echo "segfault_unsafe"
    else
        # 如果是其他的 panic 喵
        echo "unknown_panic"
    fi
}

total_count=0
staged_count=0
for folder in "$TESTCASES_BASE"/*/; do
    echo "Processing test binary in folder: $folder"

    BINARY_NAME=$(basename "$folder")
    BINARY_PATH="${folder}target/debug/${BINARY_NAME}"
    STAGING_DIR="${folder}filtered_crashes"
    [ -d "$STAGING_DIR" ] && rm -rf "$STAGING_DIR"
    mkdir -p "$STAGING_DIR"

    if [ ! -f "$BINARY_PATH" ]; then
        echo "Warning: Binary $BINARY_PATH not found, skipping..."
        continue
    fi

    total_count_this_testcase=0
    staged_count_this_testcase=0
    while read -r crash_file; do
        crash_file="/${crash_file#/}"
        error_log=$(timeout $TIME_OUT $BINARY_PATH < "$crash_file" 2>&1)
        exit_code=$?

        if ! echo "$error_log" | grep -q "$HARNESS_FILE"; then
            category=""
            if [ $exit_code -eq 124 ]; then
                category="hang_timeout"
            else
                category=$(categorize_bug "$error_log")
            fi

            mkdir -p "$STAGING_DIR/$category"
            cp "$crash_file" "$STAGING_DIR/$category/"

            staged_count_this_testcase=$((staged_count_this_testcase + 1))
        fi
        total_count_this_testcase=$((total_count_this_testcase + 1))
    done < <(find "${folder}outputs/default/crashes" -maxdepth 1 -type f ! -name "*.txt")
    echo "Testcase $BINARY_NAME: total crashes = $total_count_this_testcase, staged crashes = $staged_count_this_testcase"
    total_count=$((total_count + total_count_this_testcase))
    staged_count=$((staged_count + staged_count_this_testcase))
done

echo "Total crashes processed: $total_count"
echo "Crate bugs saved: $staged_count"