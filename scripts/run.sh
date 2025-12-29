#!/usr/bin/env bash

# Fail on first error, on undefined variables, and on failures in pipelines.
set -euo pipefail

CRATE_NAME=$1
API_KEY=$2
export PLF_API_KEY=$API_KEY
TESTCASE_COUNT=3
if [ -z $CRATE_NAME ]; then
    echo "Usage: $0 <crate_name>"
    exit 1
fi

TOPLEVEL="$(git rev-parse --show-toplevel)"
OUTPUT_DIR="$TOPLEVEL/output/$CRATE_NAME/"
if [ -d $OUTPUT_DIR ]; then
    echo "Output directory $OUTPUT_DIR already exists."
    echo "Enter 'y' to delete it and continue, or any other key to exit."
    read -r response
    if [ "$response" != "y" ]; then
        echo "Exiting."
        exit 1
    else
        rm -rf $OUTPUT_DIR
    fi
fi
mkdir -p $OUTPUT_DIR

# build project
echo "Building project..."
cargo build --release
EXE="$TOPLEVEL/target/release/PLFuzz"
if [ ! -e $EXE ]; then
    echo "Build failed, executable not found at $EXE"
    exit 1
fi
if [ ! -x $EXE ]; then
    echo "Build failed, $EXE is not executable"
    exit 1
fi
echo "Build succeeded."

# Generate Docs
echo "Generating documentation..."
$EXE doc -n $CRATE_NAME -o "${OUTPUT_DIR}doc.json"
if [ ! -e "${OUTPUT_DIR}doc.json" ]; then
    echo "Documentation generation failed."
    exit 1
fi
echo "Documentation generated at ${OUTPUT_DIR}doc.json."

# Generate API sequences
echo "Generating API sequences..."
$EXE api-sequence -i "${OUTPUT_DIR}doc.json" -o "${OUTPUT_DIR}seqs.json" -c $TESTCASE_COUNT
if [ ! -e "${OUTPUT_DIR}seqs.json" ]; then
    echo "API sequence generation failed."
    exit 1
fi
echo "API sequences generated at ${OUTPUT_DIR}seqs.json."

# Generate test cases
echo "Generating test cases..."
$EXE testcase $CRATE_NAME -i "${OUTPUT_DIR}seqs.json" -o "${OUTPUT_DIR}/testcases/"
echo "Test cases generated at ${OUTPUT_DIR}/testcases/."

# Generate seed for testcases
echo "Generating seed for test cases..."
$EXE seed-gen $CRATE_NAME "${OUTPUT_DIR}/testcases/" -c $TESTCASE_COUNT
echo "Seed generation completed."

# Start Fuzzing
echo "Starting fuzzing..."
$EXE fuzz $CRATE_NAME "${OUTPUT_DIR}/testcases/" -c $TESTCASE_COUNT
echo "All tasks completed successfully."