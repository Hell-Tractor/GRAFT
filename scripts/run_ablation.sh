#!/bin/bash
# GRAFT Ablation Experiment Runner
# Runs 4 variants (full, no-generic, no-graph, no-topo) × N crates × full pipeline
# Usage: bash run_ablation.sh [crate1 crate2 ...]
# Default: base64 byteorder fixedbitset time regex

set -uo pipefail

TOPLEVEL="$(git -C "$(dirname "$(realpath "$0")")" rev-parse --show-toplevel)"
source "$TOPLEVEL/env"

EXE="$TOPLEVEL/target/release/graft"
STD_DOC="$TOPLEVEL/output/std_doc.json"
OUTPUT_ROOT="$TOPLEVEL/output_ablation"
SEQ_COUNT=20
SEED_COUNT=3
FUZZ_TIMEOUT=3600   # 1 hour per fuzz session

CRATES=("${@:-base64 byteorder fixedbitset time regex}")
MODES=(no-generic no-graph no-topo)

mkdir -p "$OUTPUT_ROOT"
LOGFILE="$OUTPUT_ROOT/ablation.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOGFILE"; }
log_section() { log ""; log "================================================================"; log "  $*"; log "================================================================"; }

log_section "GRAFT Ablation Experiment Started"
log "Crates: ${CRATES[*]}"
log "Modes:  ${MODES[*]}"
log "Count=$SEQ_COUNT  Seeds=$SEED_COUNT  FuzzTimeout=${FUZZ_TIMEOUT}s"

# ─── Phase 1: API sequence generation ────────────────────────────────────────
log_section "Phase 1: API Sequence Generation"

declare -A EDGE_COUNTS

for MODE in "${MODES[@]}"; do
    for CRATE in "${CRATES[@]}"; do
        DIR="$OUTPUT_ROOT/$MODE/$CRATE"
        mkdir -p "$DIR"
        DOC="$TOPLEVEL/output/$CRATE/doc.json"

        ABLATION_FLAG=""
        [[ "$MODE" != "full" ]] && ABLATION_FLAG="--ablation $MODE"

        log "[$CRATE/$MODE] Generating $SEQ_COUNT sequences..."
        if $EXE api-sequence -i "$DOC" --std "$STD_DOC" \
                -o "$DIR/seqs.json" -c $SEQ_COUNT $ABLATION_FLAG \
                >"$DIR/seq_stdout.txt" 2>"$DIR/seq_stderr.log"; then
            ACTUAL=$(cat "$DIR/seq_stdout.txt" | tr -d '[:space:]')
            EDGES=$(grep -o '\[AblationMetric\] Total graph edges: [0-9]*' "$DIR/seq_stderr.log" | grep -o '[0-9]*$' || echo "?")
            EDGE_COUNTS["$CRATE/$MODE"]="$EDGES"
            log "[$CRATE/$MODE] OK: $ACTUAL seqs, edges=$EDGES"
        else
            log "[$CRATE/$MODE] ERROR generating sequences"
            EDGE_COUNTS["$CRATE/$MODE"]="ERR"
        fi
    done
done

# Print edge count summary
log ""
log "Edge count summary (NoGeneric reduces this):"
printf '%-16s %-10s %-12s %-12s %-10s\n' "Crate" "full" "no-generic" "no-graph" "no-topo" | tee -a "$LOGFILE"
printf '%-16s %-10s %-12s %-12s %-10s\n' "-----" "----" "----------" "--------" "-------" | tee -a "$LOGFILE"
for CRATE in "${CRATES[@]}"; do
    printf '%-16s %-10s %-12s %-12s %-10s\n' \
        "$CRATE" \
        "${EDGE_COUNTS[$CRATE/full]:-?}" \
        "${EDGE_COUNTS[$CRATE/no-generic]:-?}" \
        "${EDGE_COUNTS[$CRATE/no-graph]:-?}" \
        "${EDGE_COUNTS[$CRATE/no-topo]:-?}" | tee -a "$LOGFILE"
done

# ─── Phase 2: Testcase generation (LLM) ──────────────────────────────────────
log_section "Phase 2: Testcase Generation (LLM)"

for MODE in "${MODES[@]}"; do
    for CRATE in "${CRATES[@]}"; do
        DIR="$OUTPUT_ROOT/$MODE/$CRATE"
        TC_DIR="$DIR/testcases"
        log "[$CRATE/$MODE] Generating testcases from LLM..."
        if $EXE testcase "$CRATE" -i "$DIR/seqs.json" -o "$TC_DIR" \
                2>"$DIR/testcase.log"; then
            COMPILED=$(find "$TC_DIR" -maxdepth 2 -name "Cargo.toml" 2>/dev/null | wc -l)
            SUCCEED=$(grep -c "generated successfully" "$DIR/testcase.log" 2>/dev/null || echo "?")
            log "[$CRATE/$MODE] Done: $COMPILED dirs, ~$SUCCEED compiled ok"
        else
            log "[$CRATE/$MODE] ERROR in testcase generation (check $DIR/testcase.log)"
        fi
    done
done

# ─── Phase 3: Seed generation (LLM) ──────────────────────────────────────────
log_section "Phase 3: Seed Generation (LLM)"

for MODE in "${MODES[@]}"; do
    for CRATE in "${CRATES[@]}"; do
        DIR="$OUTPUT_ROOT/$MODE/$CRATE"
        TC_DIR="$DIR/testcases"
        log "[$CRATE/$MODE] Generating seeds..."
        $EXE seed-gen "$CRATE" "$TC_DIR" -c $SEQ_COUNT -s $SEED_COUNT \
            2>"$DIR/seedgen.log" || true

        # Fallback: ensure every testcase has at least one seed
        for i in $(seq 0 $((SEQ_COUNT - 1))); do
            INPUT_DIR="$TC_DIR/${CRATE}_test${i}/inputs"
            if [ -d "$TC_DIR/${CRATE}_test${i}" ] && \
               { [ ! -d "$INPUT_DIR" ] || [ -z "$(ls -A "$INPUT_DIR" 2>/dev/null)" ]; }; then
                mkdir -p "$INPUT_DIR"
                printf '\x00\x01\x02\x03' > "$INPUT_DIR/seed_fallback"
            fi
        done
        log "[$CRATE/$MODE] Seed gen complete."
    done
done

# ─── Phase 4: Fuzzing (per crate: all 4 modes in parallel) ───────────────────
log_section "Phase 4: Fuzzing (${FUZZ_TIMEOUT}s per mode)"

for CRATE in "${CRATES[@]}"; do
    log "[$CRATE] Launching all 4 modes in parallel..."
    declare -A FUZZ_PIDS
    for MODE in "${MODES[@]}"; do
        TC_DIR="$OUTPUT_ROOT/$MODE/$CRATE/testcases"
        $EXE fuzz "$CRATE" "$TC_DIR" -c $SEQ_COUNT -t $FUZZ_TIMEOUT \
            2>"$OUTPUT_ROOT/$MODE/$CRATE/fuzz.log" &
        FUZZ_PIDS["$MODE"]="$!"
        log "[$CRATE/$MODE] Fuzz started (PID=${FUZZ_PIDS[$MODE]})"
    done
    for MODE in "${MODES[@]}"; do
        wait "${FUZZ_PIDS[$MODE]}" || true
        log "[$CRATE/$MODE] Fuzz session finished."
    done
    log "[$CRATE] All 4 modes fuzz done."
    unset FUZZ_PIDS
done

# ─── Phase 5: Coverage calculation ───────────────────────────────────────────
log_section "Phase 5: Coverage Calculation"

RUST_SYSROOT=$(rustc --print sysroot)
RUST_HOST=$(rustc -vV | grep host | awk '{print $2}')
LLVM_BIN="$RUST_SYSROOT/lib/rustlib/$RUST_HOST/bin"
export PATH="$LLVM_BIN:$PATH"

calc_coverage() {
    local MODE="$1" CRATE="$2"
    local TC_BASE="$OUTPUT_ROOT/$MODE/$CRATE/testcases"
    local COV_RAW="$OUTPUT_ROOT/$MODE/$CRATE/coverage_raw"
    local PROFDATA="$OUTPUT_ROOT/$MODE/$CRATE/total_coverage.profdata"
    local COV_JSON="$OUTPUT_ROOT/$MODE/$CRATE/coverage_detail.json"
    local COV_LOG="$OUTPUT_ROOT/$MODE/$CRATE/coverage.log"

    mkdir -p "$COV_RAW"
    find "$COV_RAW" -name '*.profraw' -delete

    export LLVM_PROFILE_FILE="$COV_RAW/test-%p-%m.profraw"
    export RUSTFLAGS="-Cinstrument-coverage"

    local OBJFILES=()
    for folder in "$TC_BASE"/*/; do
        [ -d "$folder" ] || continue
        local NAME
        NAME=$(basename "$folder")
        local COV_DIR="${folder}target/coverage"
        cargo build --manifest-path "${folder}Cargo.toml" --target-dir "$COV_DIR" \
            >>"$COV_LOG" 2>&1 || continue
        local BIN="${COV_DIR}/debug/${NAME}"
        [ -f "$BIN" ] || continue

        local QUEUE="${folder}outputs/default/queue"
        [ -d "$QUEUE" ] || continue
        OBJFILES+=("-object" "$BIN")
        while IFS= read -r -d '' f; do
            "$BIN" < "$f" >/dev/null 2>&1 || true
        done < <(find "$QUEUE" -maxdepth 1 -type f -print0)
    done

    unset RUSTFLAGS LLVM_PROFILE_FILE

    if [ ${#OBJFILES[@]} -eq 0 ]; then
        log "[$CRATE/$MODE] No coverage binaries found, skipping."
        return
    fi

    find "$COV_RAW" -name "*.profraw" -print > /tmp/graft_profraw_$$.txt
    llvm-profdata merge -sparse -f /tmp/graft_profraw_$$.txt -o "$PROFDATA" 2>>"$COV_LOG"
    rm -f /tmp/graft_profraw_$$.txt

    llvm-cov export "${OBJFILES[@]}" -instr-profile="$PROFDATA" \
        --format=text --skip-expansions --skip-functions=false \
        --ignore-filename-regex="main\\.rs$" > "$COV_JSON" 2>>"$COV_LOG"

    local RESULT
    RESULT=$($EXE coverage "$CRATE" "$COV_JSON" 2>>"$COV_LOG")
    echo "$RESULT"
}

declare -A API_COV

for MODE in "${MODES[@]}"; do
    for CRATE in "${CRATES[@]}"; do
        log "[$CRATE/$MODE] Calculating API coverage..."
        RESULT=$(calc_coverage "$MODE" "$CRATE" 2>&1)
        API_COV["$CRATE/$MODE"]="$RESULT"
        log "[$CRATE/$MODE] Coverage result: $RESULT"
    done
done

# ─── Final Summary ────────────────────────────────────────────────────────────
log_section "Ablation Experiment Summary"

log "API Coverage Results:"
printf '%-16s %-16s %-16s %-16s %-16s\n' "Crate" "full" "no-generic" "no-graph" "no-topo" | tee -a "$LOGFILE"
printf '%-16s %-16s %-16s %-16s %-16s\n' "-----" "----" "----------" "--------" "-------" | tee -a "$LOGFILE"
for CRATE in "${CRATES[@]}"; do
    printf '%-16s %-16s %-16s %-16s %-16s\n' \
        "$CRATE" \
        "${API_COV[$CRATE/full]:-N/A}" \
        "${API_COV[$CRATE/no-generic]:-N/A}" \
        "${API_COV[$CRATE/no-graph]:-N/A}" \
        "${API_COV[$CRATE/no-topo]:-N/A}" | tee -a "$LOGFILE"
done

log ""
log "Edge Counts (graph construction metric for no-generic):"
printf '%-16s %-10s %-12s\n' "Crate" "full" "no-generic" | tee -a "$LOGFILE"
for CRATE in "${CRATES[@]}"; do
    printf '%-16s %-10s %-12s\n' \
        "$CRATE" \
        "${EDGE_COUNTS[$CRATE/full]:-?}" \
        "${EDGE_COUNTS[$CRATE/no-generic]:-?}" | tee -a "$LOGFILE"
done

log ""
log "Ablation experiment complete! Full log: $LOGFILE"
