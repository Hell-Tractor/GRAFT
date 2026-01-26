#!/bin/bash

TOPLEVEL="$(git rev-parse --show-toplevel)"
OUTPUT_PATH="$TOPLEVEL/localdata/std"

if [ ! -d $OUTPUT_PATH ]; then
    mkdir -p $OUTPUT_PATH
fi

RUST_CODE_PATH="$TOPLEVEL/localdata/rust"
if [ -d "$RUST_CODE_PATH" ]; then
    echo "Rust source code already exists at $RUST_CODE_PATH"
else
    echo "Cloning Rust source code into $RUST_CODE_PATH..."
    git clone --depth 1 https://github.com/rust-lang/rust.git "$RUST_CODE_PATH"
fi

export RUSTC_BOOTSTRAP=1
export RUSTDOCFLAGS="-Z unstable-options --output-format json"

echo "Generating standard library documentation..."
STD_LIBS=("std" "core" "alloc")
for lib in "${STD_LIBS[@]}"; do
    echo "Generating ${lib}.json..."
    cd $RUST_CODE_PATH
    cp bootstrap.example.toml bootstrap.toml
    python3 x.py doc library/$lib --stage 1
    DOC_FILE=$(find "build" -name "${lib}.json" | head -n 1)
    DOC_FILE="$RUST_CODE_PATH/$DOC_FILE"
    cd $TOPLEVEL
    if [ -z "$DOC_FILE" ]; then
        echo "Failed to generate ${lib} documentation." >&2
        exit 1
    fi
    DEST_PATH=$OUTPUT_PATH
    cp "$DOC_FILE" "$DEST_PATH/${lib}.json"
    echo "${lib} documentation generated at $DEST_PATH/${lib}.json"

    jq . "$DEST_PATH/${lib}.json" > "$DEST_PATH/${lib}_pretty.json"
    sed -i 's/"\(#\[doc..*[^]]\)"$/"\1]"/g' "$DEST_PATH/${lib}_pretty.json"

    mv "$DEST_PATH/${lib}_pretty.json" "$DEST_PATH/${lib}.json"
done