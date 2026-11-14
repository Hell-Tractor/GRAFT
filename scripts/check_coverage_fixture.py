#!/usr/bin/env python3
"""Run actual rustdoc -> instrumented binary -> LLVM export -> snapshot checks."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from coverage_snapshot import freeze, load, measure, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", default="nightly-2026-02-07")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixture = root / "experiments/week1/fixtures/coverage-fixture-0.1.0/src/lib.rs"
    llvm = Path(subprocess.check_output(["rustc", "+" + args.toolchain, "--print", "sysroot"], text=True).strip())
    host = next(line.split(": ", 1)[1] for line in subprocess.check_output(
        ["rustc", "+" + args.toolchain, "-vV"], text=True).splitlines() if line.startswith("host: "))
    llvm = llvm / "lib/rustlib" / host / "bin"
    commands = []
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="graft-coverage-fixture-") as directory:
        work = Path(directory)

        def run(command, env=None):
            result = subprocess.run([str(x) for x in command], capture_output=True, text=True,
                                    timeout=60, env=env)
            commands.append({"command": [str(x) for x in command], "exit_code": result.returncode,
                             "stdout": result.stdout, "stderr": result.stderr})
            result.check_returncode()
            return result.stdout

        run(["rustdoc", "+" + args.toolchain, fixture, "--crate-name", "coverage_fixture",
             "--crate-version", "0.1.0", "--edition", "2024", "--output-format", "json",
             "-Z", "unstable-options", "-o", work / "doc"])
        run(["rustc", "+" + args.toolchain, fixture, "--crate-name", "coverage_fixture",
             "--crate-type", "rlib", "--edition", "2024", "-C", "instrument-coverage",
             "-C", "link-dead-code", "-o", work / "libcoverage_fixture.rlib"])
        run(["rustc", "+" + args.toolchain, root / "experiments/week1/fixtures/runner.rs",
             "--edition", "2024", "--extern", "coverage_fixture=" + str(work / "libcoverage_fixture.rlib"),
             "-C", "instrument-coverage", "-C", "link-dead-code", "-o", work / "runner"])
        env = dict(os.environ, LLVM_PROFILE_FILE=str(work / "run-%p-%m.profraw"))
        run([work / "runner"], env)
        run([llvm / "llvm-profdata", "merge", "-sparse", *sorted(work.glob("*.profraw")),
             "-o", work / "coverage.profdata"])
        exported = run([llvm / "llvm-cov", "export", work / "runner",
                        "-instr-profile=" + str(work / "coverage.profdata")])
        (args.output / "coverage.json").write_text(exported)
        snapshot = freeze(work / "doc/coverage_fixture.json", "coverage-fixture", [],
                          fixture.parents[1], "0.1.0")
        result = measure(snapshot, load(args.output / "coverage.json"))
        states = {r["name"]: r["status"] for r in result["apis"] if r["candidate"]}
        for name in ["entered", "generic", "new", "read", "reexported", "same_line_left"]:
            assert states[name] == "covered", (name, states)
        assert states["uncalled"] != "covered", states
        assert states["same_line_right"] != "covered", states
        assert sum(r["name"] == "generic" and r["candidate"] for r in result["apis"]) == 1
        save(args.output / "api_universe.json", snapshot)
        save(args.output / "result.json", result)
        save(args.output / "validation.json", {"passed": True, "toolchain": args.toolchain,
             "assertions": ["executed generic instances merge into one API", "reexport resolves to definition",
                            "same-line uncalled API stays unconfirmed", "known entered APIs are covered"],
             "macro_status": states.get("macro_api"), "commands": commands})
    print("coverage fixture passed; results at " + str(args.output))


if __name__ == "__main__":
    main()
