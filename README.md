# GRAFT

> Revision experiments: use the frozen API snapshots and offline audit workflow
> in [experiments/README.md](experiments/README.md). The historical live-lookup
> `coverage` command is not the scoring path for new controlled comparisons.
>
> Checked planning: see [experiments/checked-planning.md](experiments/checked-planning.md)
> for the new `graft-plan` importer, scoped unifier, explicit value plans,
> deterministic compiler witnesses, and plan-bound LLM rendering. The legacy
> `api-sequence` / `testcase` commands still reproduce unbound heuristic lists.
>
> The current paper batch uses the 13 frozen versions listed below, one
> generation, 20 slots per crate, and 300 seconds per admitted target. Its
> execution/recovery entry is
> [scripts/run_fixed_generation.py](scripts/run_fixed_generation.py); the
> historical Qwen/live-lookup workflow later in this README is retained only
> for legacy provenance.

**GRAFT** (Graph-guided Rust API Fuzzing Tool) is an LLM-assisted fuzzing framework for Rust libraries. It automatically generates AFL fuzzing harnesses by constructing a type-aware API dependency graph from rustdoc, sampling API call sequences via topological traversal, and using an LLM to synthesize compilable Rust fuzz targets.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Basic Workflow](#basic-workflow)
- [Reproducing Paper Experiments](#reproducing-paper-experiments)
  - [Coverage Evaluation](#coverage-evaluation-main-experiment)
  - [Ablation Study](#ablation-study)
- [Docker Deployment](#docker-deployment)

---

## Prerequisites

- **Rust nightly** toolchain (tested on `rustc 1.97.0-nightly (17584a181 2026-04-13)`)
- **cargo-afl** (`cargo install afl`)
- **LLVM tools** for coverage analysis (`rustup component add llvm-tools-preview`)
- An LLM API key (tested with Qwen3.5-Plus via DashScope; any OpenAI-compatible endpoint works)

```bash
rustup toolchain install nightly
cargo install afl
rustup component add llvm-tools-preview
```

---

## Installation

```bash
git clone <this-repo> GRAFT
cd GRAFT
cargo build --release
```

Set your LLM API key:

```bash
# One-time export:
export GRAFT_API_KEY=<your-api-key>

# Or persist to the env file and source it before each session:
echo 'export GRAFT_API_KEY=<your-api-key>' > env
source env
```

---

## Configuration

Edit `config.yaml` before running. Key fields:

```yaml
LLM:
  type: "DeepSeek"           # adapter name (works for any OpenAI-compatible API)
  model_name: "qwen3.5-plus"
  api_url_chat: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  fix_retries: 3             # legacy total attempts: initial + at most 2 repairs
  concurrent_tasks: 16       # parallel LLM requests
  max_tokens: 8192

testcase:
  each_api_max_usage: 3      # max appearances of one API per sequence
  api_least_coverage: 2      # minimum sequences each API must appear in

doc:
  parser: "RustDoc"          # RustDoc (recommended) or TrustFall
```

---

## Basic Workflow

The historical workflow below uses `target/release/graft`. For new checked
plans, use the separate `target/release/graft-plan` workflow linked above.

### Step 0 — Compile GRAFT

```bash
cargo build --release
export GRAFT=$(pwd)/target/release/graft
$GRAFT --help
```

You should see the following usage message:


```
Usage: graft <COMMAND>

Commands:
  doc           Generate API documentation for a crate
  api-graph     Generate API graph from API documentation
  measure       Process a quick measure of target crate. Calculating possible max API coverage
  api-sequence  Generate API sequences from API graph
  testcase      Convert API sequences to executable testcases
  seed-gen      Generate seed inputs for all testcases
  fuzz          Fuzz all testcases with AFL
  coverage      Analyze fuzzing results and calculate API coverage
  help          Print this message or the help of the given subcommand(s)

Options:
  -h, --help     Print help
  -V, --version  Print version
```

You can use `$GRAFT <subcommand> --help` to see usage for each subcommand.


### Step 1 — Generate std library documentation (once)

```bash
bash scripts/generate_std_doc.sh
$GRAFT doc -p localdata/std/std.json -o output/std_doc.json
```

### Step 2 — Generate API documentation for the target crate

```bash
# Fetch from crates.io:
$GRAFT doc -n <crate_name> -o output/<crate>/doc.json

# Or from a local rustdoc JSON file:
$GRAFT doc -p /path/to/rustdoc.json -o output/<crate>/doc.json
```

### Step 3 — Generate API sequences

```bash
$GRAFT api-sequence \
    -i output/<crate>/doc.json \
    --std output/std_doc.json \
    -o output/<crate>/seqs.json
```

The number of sequences is determined automatically from API coverage targets; override with `-c <N>`.

### Step 4 — Generate fuzz harnesses (LLM)

```bash
$GRAFT testcase <crate_name> \
    -i output/<crate>/seqs.json \
    -o output/<crate>/testcases/
```

Each harness is a self-contained Cargo project under `output/<crate>/testcases/<crate>_testN/`. Harnesses that fail to compile after `fix_retries` attempts are skipped.

### Step 5 — Generate seed inputs (LLM)

```bash
$GRAFT seed-gen <crate_name> output/<crate>/testcases/ \
    -c <num_testcases> -s <num_seeds_per_testcase>
```

### Step 6 — Fuzz

```bash
$GRAFT fuzz <crate_name> output/<crate>/testcases/ \
    -c <num_testcases> \
    -t 3600 \
    -j 32
```

GRAFT builds each harness with AFL instrumentation + AddressSanitizer and automatically resumes previous sessions when an AFL queue already exists.

### Step 7 — Calculate API coverage

First collect coverage data, then query GRAFT for the API-level result:

```bash
# Build coverage-instrumented binaries and merge profdata:
bash scripts/coverage.sh <crate_name> \

# Compute API coverage:
$GRAFT coverage <crate_name> \
    output/<crate>/coverage_detail.json \
    --crate-version <version>
```

---

## Historical Evaluation Workflow

> **Historical only.** The versions, model, retry semantics, and 3/6/12-hour
> commands in this section describe the pre-revision measurements. They are not
> the current paper batch. Current results use the frozen versions in
> `experiments/protocol-v1.json`, DeepSeek `deepseek-v4-pro` with one initial
> request and at most one repair, a fixed 20-slot budget, and the checked-plan
> entry points above.

### Coverage Evaluation (Main Experiment)

The historical evaluation used 13 crates. Crate versions:

| Crate | Version | Crate | Version |
|-------|---------|-------|---------|
| base64 | 0.22.1 | chrono | 0.4.44 |
| bumpalo | 3.20.2 | bytes | 1.11.1 |
| byteorder | 1.5.0 | csv | 1.4.0 |
| autocfg | 1.5.0 | idna | 1.1.0 |
| fixedbitset | 0.5.7 | regex | 1.12.3 |
| ryu | 1.0.23 | time | 0.3.47 |
| unicode-segmentation | 1.13.2 | | |

**LLM configuration used in the paper:** model `qwen3.5-plus` (DashScope), `temperature=0.0`, `max_tokens=8192`, `enable_thinking` disabled.

**To reproduce for a single crate** (example: `regex`):

```bash
export GRAFT_API_KEY=<your-api-key>


bash scripts/run.sh regex 1.12.3 0 8 10800
bash scripts/coverage.sh regex output
$GRAFT coverage regex \
    output/regex/coverage_detail.json --crate-version 1.12.3
```

### Ablation Study

The ablation study evaluates three degraded variants on 5 crates (`base64`, `byteorder`, `fixedbitset`, `time`, `regex`) with 20 sequences each and 1-hour fuzzing per harness.

| Variant | Degradation |
|---------|-------------|
| `no-generic` | Replaces generic-aware type matching with syntactic equality only |
| `no-graph` | Disables the API dependency graph; sequences are sampled randomly |
| `no-topo` | Keeps the dependency graph but selects target APIs randomly instead of by topological order |

```bash
source env

# Run all phases for all 5 crates × 3 variants:
bash scripts/run_ablation.sh

# Specify a subset of crates:
bash scripts/run_ablation.sh base64 regex time
```

Results are written to `output_ablation/` and printed as a summary table at the end. The log file is at `output_ablation/ablation.log`.

---

## Docker Deployment

See [Dockerfile](Dockerfile) in the repository root.

```bash
# Build the image:
docker build -t graft .

# Run interactively (mount output directory for persistence):
docker run -it \
    -e GRAFT_API_KEY=<your-api-key> \
    -v $(pwd)/output:/app/output \
    graft bash

# Inside the container the binary is available as GRAFT:
GRAFT doc -n regex -o output/regex/doc.json
GRAFT api-sequence -i output/regex/doc.json --std output/std_doc.json \
    -o output/regex/seqs.json
# ... (continue with remaining steps)
```
