#!/usr/bin/env python3
"""Audit the separately generated review baseline tracks without rerunning them."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple


TRACKS = {
    "SyRust": {
        "directory": "syrust-round1",
        "summary": ["summary.json", "partial-summary.json"],
    },
    "RuMono": {
        "directory": "rumono-round1",
        "summary": ["summary.json", "partial-summary.json"],
    },
    "FRIES": {
        "directory": "fries-round1",
        "summary": ["summary.json", "partial-summary.json"],
    },
    "deepSURF": {
        "directory": "deepsurf-analysis-fix-20261113",
        "summary": ["repair-summary.json", "partial-summary.json"],
    },
}

FAILURE_STATES = {
    "replay_failed",
    "fuzz_failed",
    "infrastructure_failed",
    "generation_failed",
    "planning_unknown",
    "validation_rejected",
    "compile_failed",
    "generation_timeout",
    "no_target",
    "unsettled_request",
}


def load_summary(
    track_root: Path, candidates: List[str]
) -> Tuple[Optional[Path], Optional[dict]]:
    for name in candidates:
        path = track_root / name
        if path.exists():
            return path, json.loads(path.read_text())
    return None, None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def audit_track(
    method: str,
    track_root: Path,
    candidate_summaries: list[str],
    versions: dict[str, str],
) -> dict:
    summary_path, summary = load_summary(track_root, candidate_summaries)
    if summary is None:
        return {
            "method": method,
            "status": "missing",
            "track_directory": str(track_root),
        }

    rows = {row["crate"]: row for row in summary.get("rows", [])}
    state_totals: Counter[str] = Counter()
    per_crate = []
    complete_rows = 0
    covered_union = 0
    defined_union = 0
    raw_crashes = 0
    raw_hangs = 0

    for crate, version in versions.items():
        row = rows.get(crate)
        if row is None:
            per_crate.append(
                {
                    "crate": crate,
                    "version": version,
                    "status": "missing",
                    "slot_status_counts": {},
                }
            )
            continue
        counts = {str(k): int(v) for k, v in row.get("slot_status_counts", {}).items()}
        state_totals.update(counts)
        fixed_slots = sum(counts.values())
        measured = int(row.get("measured_targets", counts.get("measured", 0)))
        covered = int(row.get("confirmed_covered_lower_bound", 0))
        denominator = int(row.get("denominator", 0))
        raw_crashes += int(row.get("raw_saved_crashes_not_bugs", 0))
        raw_hangs += int(row.get("raw_saved_hangs", 0))
        covered_union += covered
        defined_union += denominator
        if row.get("status") == "complete":
            complete_rows += 1
        per_crate.append(
            {
                "crate": crate,
                "version": version,
                "status": row.get("status"),
                "fixed_slots": fixed_slots,
                "measured": measured,
                "slot_status_counts": counts,
                "confirmed_covered_lower_bound": covered,
                "defined_api_denominator": denominator,
                "coverage_percent": row.get("coverage_percent"),
                "raw_saved_crashes_not_bugs": int(
                    row.get("raw_saved_crashes_not_bugs", 0)
                ),
                "raw_saved_hangs": int(row.get("raw_saved_hangs", 0)),
            }
        )

    fixed_slots = 20 * len(versions)
    measured = state_totals["measured"]
    failure_gaps = sum(
        count for state, count in state_totals.items() if state in FAILURE_STATES
    )
    unknown_states = sorted(set(state_totals) - FAILURE_STATES - {"measured"})
    coverage_values = [
        row["coverage_percent"]
        for row in per_crate
        if row.get("coverage_percent") is not None
    ]

    return {
        "method": method,
        "status": summary.get("status"),
        "track_directory": str(track_root),
        "summary_path": str(summary_path),
        "summary_sha256": sha256(summary_path),
        "fixed_slots": fixed_slots,
        "accounted_slots": sum(state_totals.values()),
        "measured_slots": measured,
        "failure_gap_slots": failure_gaps,
        "state_counts": dict(sorted(state_totals.items())),
        "unknown_states": unknown_states,
        "complete_crate_rows": complete_rows,
        "crate_rows": len(per_crate),
        "confirmed_covered_definitions_union": covered_union,
        "defined_api_denominator_union": defined_union,
        "pooled_coverage_lower_bound_percent": (
            100.0 * covered_union / defined_union if defined_union else None
        ),
        "complete_row_macro_coverage_percent": (
            sum(float(value) for value in coverage_values) / len(coverage_values)
            if coverage_values
            else None
        ),
        "raw_saved_crashes_not_bugs": raw_crashes,
        "raw_saved_hangs": raw_hangs,
        "crates": per_crate,
        "track": summary.get("track"),
        "repair_track": summary.get("repair_track"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    protocol = json.loads(args.protocol.read_text())
    versions = protocol["versions"]
    tracks = {
        method: audit_track(
            method,
            args.root / config["directory"],
            config["summary"],
            versions,
        )
        for method, config in TRACKS.items()
    }

    result = {
        "schema": "graft-review-baseline-audit-v1",
        "protocol_sha256": sha256(args.protocol),
        "fixed_slots_per_method": len(versions) * 20,
        "versions": versions,
        "source_identities": {
            "SyRust": {
                "release": "FINAL-85-pldi2021ae-syrust",
                "archive_md5": "e84b1e7cb133a80d8973d8ccbbaed92b",
            },
            "RuMono": {
                "repository": "https://github.com/Artisan-Lab/RULF/tree/RuMono",
                "commit": "40b3e9d63b4145e0f0cdc7aa768125d8c999c3e4",
            },
            "FRIES": {
                "repository": "https://github.com/MeowBoy326/FRIES",
                "commit": "849b3133524f347bfafc399777c9e94cb972ad99",
            },
            "deepSURF": {
                "track": "separately labelled repair of originally unfinished native generation"
            },
        },
        "interpretation": {
            "fixed_denominator": "20 slots per crate regardless of generation or execution outcome",
            "zero_coverage": "no_target and infrastructure failures are missing outcomes, not demonstrated zero coverage",
            "crashes": "raw saved crashes are execution artifacts and are not counted as confirmed library defects",
            "macro": "complete-row macro excludes incomplete rows; the pooled lower bound retains all defined APIs in every crate denominator",
        },
        "tracks": tracks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(args.output)
    print(json.dumps({m: t.get("status") for m, t in tracks.items()}))


if __name__ == "__main__":
    main()
