#!/bin/bash

TOPLEVEL="$(git rev-parse --show-toplevel)"

if [ ! -d "$TOPLEVEL/localdata" ]; then
    mkdir -p "$TOPLEVEL/localdata"
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

cd $RUST_CODE_PATH
pwd
cp bootstrap.example.toml bootstrap.toml
python3 x.py doc library/std --stage 1
DOC_FILE=$(find "build" -name "std.json" | head -n 1)
DOC_FILE="$RUST_CODE_PATH/$DOC_FILE"
cd $TOPLEVEL
if [ -z "$DOC_FILE" ]; then
    echo "Failed to generate std documentation." >&2
    exit 1
fi

DEST_PATH="$TOPLEVEL/localdata"
cp "$DOC_FILE" "$DEST_PATH/std.json"
echo "Standard library documentation generated at $DEST_PATH/std.json"

jq . "$DEST_PATH/std.json" > "$DEST_PATH/std_pretty.json"
sed -i 's/"\(#\[doc..*[^]]\)"$/"\1]"/g' "$DEST_PATH/std_pretty.json"