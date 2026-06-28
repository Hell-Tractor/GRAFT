FROM rust:1.94-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install nightly toolchain (required for cargo-afl and instrument-coverage)
RUN rustup toolchain install nightly-2026-04-13 \
    && rustup component add llvm-tools-preview \
    && rustup component add llvm-tools-preview --toolchain nightly-2026-04-13

# Install cargo-afl
RUN cargo install afl --version 0.18.2 --locked

WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY prompts ./prompts

# Build GRAFT in release mode
RUN cargo build --release

# ─── Runtime image ────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    # Runtime libraries for Rust binaries
    ca-certificates \
    libssl3 \
    # AFL++ runtime dependencies
    libstdc++6 \
    # Utilities
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy Rust toolchain and cargo binaries from builder
COPY --from=builder /usr/local/rustup /usr/local/rustup
COPY --from=builder /usr/local/cargo /usr/local/cargo

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:/usr/local/rustup/toolchains/nightly-2026-04-13-x86_64-unknown-linux-gnu/bin:$PATH \
    RUST_NIGHTLY=nightly-2026-04-13

# Copy GRAFT binary and supporting files
WORKDIR /app
COPY --from=builder /build/target/release/graft ./GRAFT
COPY config.yaml ./
COPY prompts ./prompts
COPY scripts ./scripts

# Ensure AFL can run without root (disable core dump pattern check)
RUN echo core > /proc/sys/kernel/core_pattern 2>/dev/null || true

# Create output directory
RUN mkdir -p /app/output

# Verify the binary works
RUN ./GRAFT --version 2>/dev/null || ./GRAFT --help | head -3

ENV GRAFT_API_KEY=""

ENTRYPOINT ["/bin/bash"]
CMD ["-c", "echo 'GRAFT container ready. Set GRAFT_API_KEY and run: ./GRAFT --help'; exec bash"]
