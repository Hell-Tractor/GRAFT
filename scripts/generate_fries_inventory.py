#!/usr/bin/env python3
"""Generate FRIES targets for the frozen shared crate versions.

The FRIES checkout is pinned to the authors' commit.  This driver runs the
official modified rustdoc against the existing locked joint-compatibility
project, so the target library and dependency versions match the main
experiment.  It records one outcome per crate and never reruns a crate that
already has a recorded outcome.
"""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path


FRIES_COMMIT = "849b3133524f347bfafc399777c9e94cb972ad99"


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--port", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rustdoc", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--crates", help="comma-separated subset")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    protocol = json.loads(args.protocol.read_text())
    versions = protocol["versions"]
    selected = (
        {item.strip() for item in args.crates.split(",") if item.strip()}
        if args.crates
        else set(versions)
    )
    unknown = selected - set(versions)
    if unknown:
        raise ValueError("unknown crates: " + ",".join(sorted(unknown)))

    args.output.mkdir(parents=True, exist_ok=True)
    inventory_path = args.output / "inventory.json"
    outcomes_path = args.output / "generation-outcomes.json"
    inventory = (
        json.loads(inventory_path.read_text()) if inventory_path.exists() else []
    )
    recorded = {
        (row["crate"], row["version"]): row for row in inventory
    }
    outcomes = (
        json.loads(outcomes_path.read_text()) if outcomes_path.exists() else {}
    )

    manifest = args.port / "joint-compatibility-v2" / "Cargo.toml"
    cargo_config = args.port / "joint-compatibility-v2" / ".cargo" / "config.toml"
    cargo = args.toolchain / "bin" / "cargo"
    rustc = args.toolchain / "bin" / "rustc"
    cargo_home = args.port / "cargo-home"
    target_dir = args.output / "cargo-target"
    library_path = (
        f"{args.toolchain / 'lib'}:"
        f"{args.toolchain / 'lib' / 'rustlib' / 'x86_64-unknown-linux-gnu' / 'lib'}"
    )

    environment = dict(
        os.environ,
        RUSTC=str(rustc),
        RUSTDOC=str(args.rustdoc),
        CARGO_HOME=str(cargo_home),
        CARGO_TARGET_DIR=str(target_dir),
        CARGO_NET_OFFLINE="true",
        LD_LIBRARY_PATH=library_path,
    )

    for crate, version in versions.items():
        if crate not in selected:
            continue
        key = f"{crate}@{version}"
        previous = outcomes.get(key)
        if previous and previous.get("status") in {
            "complete",
            "no_target",
            "generation_timeout",
            "infrastructure_failed",
        }:
            continue
        if (crate, version) in recorded:
            continue

        crate_output = args.output / crate / "fuzz_file_dir" / "real_world_afl_work"
        source_dir = crate_output / "afl_files"
        source_dir.mkdir(parents=True, exist_ok=True)
        for old_source in source_dir.glob("*.rs"):
            old_source.unlink()

        log_path = args.output / f"{crate}-generation.log"
        command = [
            str(cargo),
            "--config",
            str(cargo_config),
            "doc",
            "--manifest-path",
            str(manifest),
            "--frozen",
            "--offline",
            "-j",
            "2",
            "--no-deps",
            "--package",
            key,
        ]
        started = time.monotonic()
        timed_out = False
        with log_path.open("w") as log:
            process = subprocess.Popen(
                command,
                cwd=args.output,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                returncode = 124
                timed_out = True

        candidates = sorted(source_dir.glob("*.rs"))
        if timed_out:
            status = "generation_timeout"
        elif returncode != 0:
            status = "infrastructure_failed"
        elif not candidates:
            status = "no_target"
        else:
            status = "complete"

        slots = []
        for slot in range(20):
            if slot < len(candidates):
                slots.append({"slot": slot, "source": str(candidates[slot])})
            else:
                slot_status = (
                    "generation_timeout"
                    if timed_out
                    else (
                        "infrastructure_failed"
                        if returncode != 0
                        else "no_target"
                    )
                )
                slots.append({"slot": slot, "status": slot_status})
        row = {
            "crate": crate,
            "version": version,
            "returncode": returncode,
            "timed_out": timed_out,
            "seconds": time.monotonic() - started,
            "native_target_count": len(candidates),
            "source_commit": FRIES_COMMIT,
            "generation_status": status,
            "targets": slots,
        }

        inventory = [
            old
            for old in inventory
            if (old["crate"], old["version"]) != (crate, version)
        ]
        inventory.append(row)
        order = list(versions)
        inventory.sort(key=lambda item: order.index(item["crate"]))
        write_json(inventory_path, inventory)
        outcomes[key] = {
            "status": status,
            "returncode": returncode,
            "seconds": row["seconds"],
            "native_target_count": len(candidates),
        }
        write_json(outcomes_path, outcomes)
        print(
            json.dumps(
                {
                    "crate": crate,
                    "version": version,
                    "status": status,
                    "targets": len(candidates),
                    "seconds": round(row["seconds"], 3),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
