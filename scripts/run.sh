#!/bin/bash

pause(){
    if [ ! -z "$DEBUG" ]; then
        local prompt="${1:-Press any key to continue...}"
        read -n 1 -s -r -p "$prompt"
        echo ""
    fi
}

# Fail on first error, on undefined variables, and on failures in pipelines.
set -euo pipefail

CRATE_NAME=$1
# TESTCASE_COUNT=$2
SEED_COUNT=$2
FUZZ_TIMEOUT=$3

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
pause

# Generate Docs
echo "Generating documentation..."
$EXE doc -n $CRATE_NAME -o "${OUTPUT_DIR}doc.json"
if [ ! -e "${OUTPUT_DIR}doc.json" ]; then
    echo "Documentation generation failed."
    exit 1
fi
echo "Documentation generated at ${OUTPUT_DIR}doc.json."
pause

# Generate API sequences
echo "Generating API sequences..."
TESTCASE_COUNT=$($EXE api-sequence -i "${OUTPUT_DIR}doc.json" -o "${OUTPUT_DIR}seqs.json")
if [ ! -e "${OUTPUT_DIR}seqs.json" ]; then
    echo "API sequence generation failed."
    exit 1
fi
echo "API sequences(count = ${TESTCASE_COUNT}) generated at ${OUTPUT_DIR}seqs.json."
pause

# Generate test cases
echo "Generating test cases..."
$EXE testcase $CRATE_NAME -i "${OUTPUT_DIR}seqs.json" -o "${OUTPUT_DIR}/testcases/"
echo "Test cases generated at ${OUTPUT_DIR}/testcases/."
pause

# Generate seed for testcases
echo "Generating seed for test cases..."
$EXE seed-gen $CRATE_NAME "${OUTPUT_DIR}/testcases/" -c $TESTCASE_COUNT -s $SEED_COUNT
echo "Seed generation completed."
pause

# Start Fuzzing
echo "Starting fuzzing..."
$EXE fuzz $CRATE_NAME "${OUTPUT_DIR}/testcases/" -c $TESTCASE_COUNT -t $FUZZ_TIMEOUT
echo "All tasks completed successfully."