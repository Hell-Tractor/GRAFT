# GRAFT

**GRAFT** is a generics-aware fuzz-target generator for Rust libraries. It
imports compiler-produced rustdoc JSON, performs bounded type matching,
constructs explicit value-bound call plans, emits deterministic Rust programs,
and supports plan-preserving LLM parameter preparation.

The repository contains two paths:

- **Checked path:** `graft-plan`, `graft checked-testcase`, and
  `graft-harness`. This is the evaluated path and the recommended entry point.
- **Legacy heuristic path:** `api-graph`, `api-sequence`, `testcase`, and the
  original Qwen/live-lookup workflow. It remains available to reproduce
  historical experiments and ablations.

> The legacy path is not described here as the current implementation. In
> particular, the checked planner does not materialize and traverse a global
> heterogeneous API graph. It searches producer candidates on demand from a
> structured catalog.

## Current Pipeline

```text
format-57 rustdoc JSON
        |
        v
Catalog: APIs, generic identities, outputs, obligations, impls, type paths
        |
        v
Bounded target planning: substitutions, constructors, producer search,
borrow/move tracking, explicit failure and cycle handling
        |
        v
SequencePlan: value IDs, call order, adapters, type arguments, branches
        |
        +----> deterministic Rust emitter and compiler witness
        |
        +----> plan-preserving LLM rendering and bounded parameter repair
        |
        v
AST retention -> compilation -> runtime probes -> admission -> AFL execution
        |
        v
LLVM coverage replay and conservative API-entry mapping
```

## Repository Layout

| Path | Purpose |
|---|---|
| `src/checked/` | Rustdoc importer, bounded planner, unifier, emitter, retention checks, and probes |
| `src/bin/graft-plan.rs` | Checked catalog, planning, validation, emission, and probe CLI |
| `src/bin/graft-harness.rs` | Canonical byte/file wrapper and admission review |
| `src/main.rs`, `src/api_graph.rs` | Legacy `graft` CLI and historical graph traversal |
| `tests/checked.rs` | End-to-end checked-path regressions |
| `experiments/` | Frozen protocols, manifests, audits, and controlled-run evidence |
| `scripts/` | Cost control, admission, coverage, baseline, and publication tooling |
| `config.yaml` | Legacy/default local LLM configuration |

## Requirements

- Rustup and Cargo. `rust-toolchain.toml` pins
  `nightly-2026-04-13` with `rust-src` and `llvm-tools-preview`.
- Python 3.9 or newer for experiment and audit scripts.
- AFL++/`cargo-afl` and its runtime dependencies for fuzzing.
- LLVM tools from the matching Rust toolchain for coverage replay.
- An LLM API key only for commands that explicitly call a model.

The project currently depends on a companion fork of
`trustfall-rustdoc-adapter` version `56.2.0`. Cargo resolves it through a
relative sibling path:

<https://github.com/Hell-Tractor/trustfall-rustdoc-adapter>

## Build

The checked planner expects this workspace layout:

```text
workspace/
  GRAFT-2.0/
    GRAFTTool/
  trustfall-rustdoc-adapter/
```

Before the repository is renamed, clone it from its current URL:

```bash
mkdir -p GRAFT-workspace/GRAFT-2.0
git clone https://github.com/Hell-Tractor/PLFuzz.git \
  GRAFT-workspace/GRAFT-2.0/GRAFTTool
git clone https://github.com/Hell-Tractor/trustfall-rustdoc-adapter.git \
  GRAFT-workspace/trustfall-rustdoc-adapter

cd GRAFT-workspace/GRAFT-2.0/GRAFTTool
cargo build --locked \
  --bin graft \
  --bin graft-plan \
  --bin graft-harness
```

For a fully offline build, provide an appropriate Cargo cache and run with
`--offline`.

## Checked Planning Quick Start

### 1. Import a frozen rustdoc snapshot

```bash
target/debug/graft-plan import \
  /path/to/rustdoc.json \
  catalog.json \
  --package base64 \
  --features std
```

`import` accepts raw format-57 rustdoc JSON or an APIDoc containing
`parser/krate`. Feature declarations describe how the documentation was built;
rustdoc JSON alone does not prove the active feature set. The catalog stores
the crate version, package identity, default-feature state, and rustdoc hash.

### 2. Inspect support boundaries

```bash
target/debug/graft-plan inventory catalog.json support.json --edges
```

`--edges` reports candidate parameter pairs for diagnostics. These are not
compiler-validated dependencies and are not the graph used by the planner.

### 3. Plan one selected API target

Select an API ID from `catalog.json`, then run:

```bash
target/debug/graft-plan plan \
  catalog.json \
  <API_ID> \
  plan.json \
  --nodes 2000 \
  --depth 12 \
  --infer-call-results
```

Exit status `0` means a complete plan was written. Status `2` means the target
was rejected and structured reasons were saved. Default limits are:

- 2,000 search nodes;
- recursion depth 12;
- 128 monomorphization combinations per API;
- generic type pool `{u8, i32}`.

The CLI type pool is intentionally finite. The library API can provide a
different pool through `planner::Limits`.

### 4. Emit and compile the deterministic witness

```bash
target/debug/graft-plan emit plan.json checked-target
cd checked-target
cargo build --locked --offline
cd ..
```

The output contains `src/main.rs`, a pinned `Cargo.toml`, and `plan.json`.
Emission does not start fuzzing.

### 5. Optionally render with an LLM

```bash
export GRAFT_API_KEY=<your-key>
target/debug/graft checked-testcase \
  plan.json \
  generated-target \
  --max-repairs 1
```

`--max-repairs 1` means one initial request plus at most one optional repair.
The default CLI value is 3, but controlled paper batches freeze their own
allowance. A model response must preserve all planned calls, callee paths,
type arguments, producer dependencies, call order, required branches, and
input-derived bindings. Formatting, comments, safe local preparation, and one
main wrapper may differ.

The model is not used to replace a rejected plan with another target. Use
bounded alternate-producer planning instead:

```bash
target/debug/graft-plan alternatives \
  plan.json \
  catalog.json \
  alternatives.json \
  --max-candidates 2 \
  --nodes 2000
```

## Admission and Runtime Checks

`graft-harness` converts generated source into the controlled byte/file-entry
wrapper and records static review evidence:

```bash
target/debug/graft-harness \
  generated.rs \
  normalized.rs \
  validity.json \
  --plan plan.json
```

For LLM parameter preparation, pass `--repair-plan`, and optionally
`--standalone-output` and `--observe-objects`.

`graft-plan validate` compares the surviving AST with the immutable plan core:

```bash
target/debug/graft-plan validate plan.json generated.rs
```

Runtime arrival is checked separately with empty, zero-byte, and one-byte
probes:

```bash
target/debug/graft-plan probe \
  plan.json \
  /absolute/path/to/executable \
  runtime.json
```

Add `--extended` for repeatability checks. Compilation, AST retention, runtime
arrival, and executed coverage are separate outcomes.

## Formal Experiment Entry

The current controlled protocol is implemented by
[`scripts/run_fixed_generation.py`](scripts/run_fixed_generation.py):

- one generation pass;
- fixed target slots per crate;
- one generation reused across six AFL seeds;
- 300 seconds per admitted target;
- sealed generation and cost ledgers;
- a publication barrier between rounds.

Always validate an existing prepared batch first:

```bash
python3 scripts/run_fixed_generation.py \
  --batch /absolute/path/to/frozen-batch \
  --check-only
```

Execution must use an already prepared and frozen batch:

```bash
python3 scripts/run_fixed_generation.py \
  --batch /absolute/path/to/frozen-batch \
  --execute \
  --through-round 1
```

Do not use `--resume` after an interrupted fuzz job as a fresh 300-second run.
Do not reconstruct or reset paid ledgers. The scripts reject changed protocol,
target, corpus, binary, replay-adapter, and publication hashes.

See:

- [`experiments/checked-planning.md`](experiments/checked-planning.md)
- [`experiments/README.md`](experiments/README.md)
- [`experiments/manifest.yaml`](experiments/manifest.yaml)
- [`experiments/protocol-v1.json`](experiments/protocol-v1.json)

## Validation

Current regression commands:

```bash
cargo test --all-targets --locked --offline
python3 -m unittest discover -s scripts -p 'test_*.py'
```

Verified locally:

- 52 Rust integration tests passed;
- 150 Python tests passed.

These tests cover scoped generic substitutions, occurs checks, conditional
impls, constructor and producer backtracking, move/borrow conflicts, explicit
`Option`/`Result` branches, bounded alternatives, AST retention, parameter
repair, runtime probes, admission rejection, and coverage/cost bookkeeping.

## Supported Subset and Boundaries

The checked path explicitly returns `Unknown` or a structured failure for
unsupported cases. Current boundaries include:

- projections and associated-type bindings;
- higher-ranked trait bounds;
- symbolic constants and complex const expressions;
- unsupported lifetime relations and borrowed outputs;
- trait-method dispatch and specialization;
- arbitrary type-alias expansion;
- raw-pointer operations and unsafe calls without an explicit contract;
- missing external or blanket impl evidence.

The planner's finite search is not a proof that an API is untestable. A
successful compiler witness does not prove all runtime preconditions. Three
bounded probes are not complete taint analysis, and compiler acceptance does
not establish sanitizer or whole-language soundness.

## Legacy CLI

The original `graft` path remains available for historical reproduction:

```text
doc
api-graph
measure
api-sequence
testcase
seed-gen
fuzz
coverage
```

Its historical Qwen configuration and topological graph traversal are retained
for provenance and ablations. They are not the checked planner, and their
outputs must not be mixed with `SequencePlan` or admission results.

The `config.yaml` file continues to hold the legacy local LLM defaults.
Controlled experiments load frozen model, cost, admission, and repair settings
from their batch manifests instead of assuming the defaults in `config.yaml`.

## Artifact and Reproducibility Notes

- `experiments/week6-first-round/` is a single-target 300-second execution
  probe, not the complete paper matrix.
- `experiments/admission-v2/` documents preflight and parameter-repair
  protocols, including failed and incomplete evidence.
- `scripts/audit_*.py`, `coverage_snapshot.py`, and
  `replay_round_coverage.py` implement offline denominator, coverage, cost, and
  baseline audits.
- Large experiment data remain organized by protocol week and include hashes,
  raw outcomes, and unresolved cases where available.

The paper protocol uses 13 frozen crates, 20 fixed slots per crate,
DeepSeek `deepseek-v4-pro`, one generation, and six paired AFL executions per
admitted target. Check the frozen batch manifest rather than inferring exact
model or cost settings from the legacy `config.yaml`.
