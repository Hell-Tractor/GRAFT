#!/usr/bin/env python3
"""Inventory historical artifacts without building, resuming, or altering them."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import subprocess

from coverage_snapshot import digest, file_hash, filter_names, freeze, load, measure, save


CRATES = ["ryu", "bumpalo", "base64", "regex", "idna", "fixedbitset", "chrono",
          "time", "csv", "autocfg", "bytes", "unicode-segmentation", "byteorder"]


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return {"command": args, "exit_code": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": args, "error": str(error)}


def artifact(path):
    path = Path(path)
    return {"path": str(path), "sha256": file_hash(path), "bytes": path.stat().st_size}


def lock_packages(path, crate):
    # Cargo's generated lockfile package headers and quoted name/version/source/
    # checksum fields; deliberately not a general TOML parser (Python 3.9 stdlib).
    packages = []
    for block in path.read_text().split("[[package]]")[1:]:
        fields = dict(re.findall(r'^(name|version|source|checksum) = "([^"\n]*)"', block, re.M))
        if fields.get("name") == crate:
            packages.append(fields)
    return packages


def stats_record(path):
    fields = {}
    for line in path.read_text(errors="replace").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            if key.strip() in {"start_time", "last_update", "run_time", "fuzz_time",
                               "execs_done", "execs_per_sec", "command_line",
                               "afl_version", "target_mode", "saved_crashes"}:
                fields[key.strip()] = value.strip()
    return dict(artifact(path), fields=fields)


def inventory(path, crate, target_base=None):
    row = {"artifact_directory": str(path), "exists": path.exists(),
           "files": {}, "targets": [], "worker_stats": []}
    if not path.exists():
        return row
    for name in ["doc.json", "seqs.json", "coverage_detail.json", "total_coverage.profdata",
                 "testcase.log", "fuzz.log", "coverage.log", "seq_stderr.log"]:
        if (path / name).is_file():
            row["files"][name] = artifact(path / name)
    doc = path / "doc.json"
    if doc.is_file():
        data = load(doc)
        krate = data.get("parser", {}).get("krate", data)
        row["rustdoc_version"] = krate.get("crate_version")
        row["rustdoc_format"] = krate.get("format_version")
        row["apidoc_rows"] = len(data.get("apis", []))
        row["apidoc_unique_ids"] = len({a["id"] for a in data.get("apis", [])})
    coverage = path / "coverage_detail.json"
    if coverage.is_file():
        try:
            data = load(coverage)
        except (ValueError, OSError) as error:
            row["coverage_error"] = str(error)
            data = {}
        paths = [f["filename"] for group in data.get("data", []) for f in group.get("files", [])]
        versions = set()
        for filename in paths:
            for part in Path(filename).parts:
                match = re.fullmatch(re.escape(crate) + r"-(\d[^/]*)", part)
                if match:
                    versions.add(match[1])
        row["coverage_source_versions"] = sorted(versions)
    target_base = target_base or path / "testcases"
    if target_base.is_dir():
        for target in sorted(target_base.iterdir()):
            if not target.is_dir():
                continue
            manifest = target / "Cargo.toml"
            if not manifest.is_file():
                continue
            entry = {"name": target.name, "manifest": artifact(manifest)}
            lock = target / "Cargo.lock"
            if lock.is_file():
                entry["lock"] = artifact(lock)
                entry["target_packages"] = lock_packages(lock, crate)
            source = target / "src/main.rs"
            if source.is_file():
                entry["source"] = artifact(source)
            row["targets"].append(entry)
            stats_paths = set()
            for directory in ["outputs", "output", "out"]:
                p = target / directory
                if p.is_dir():
                    try:
                        stats_paths.update(p.glob("*/fuzzer_stats"))
                        if (p / "fuzzer_stats").is_file():
                            stats_paths.add(p / "fuzzer_stats")
                    except PermissionError:
                        row.setdefault("unreadable_stats_directories", []).append(str(p))
            for p in sorted(stats_paths):
                try:
                    row["worker_stats"].append(stats_record(p))
                except PermissionError:
                    row.setdefault("unreadable_stats_directories", []).append(str(p))
    row["lock_target_versions"] = dict(Counter(
        package["version"] for target in row["targets"] for package in target.get("target_packages", [])))
    durations = [int(s["fields"]["run_time"]) for s in row["worker_stats"]
                 if s["fields"].get("run_time", "").isdigit()]
    row["surviving_stats_run_time_seconds_sum"] = sum(durations) if durations else None
    row["duration_histogram_seconds"] = dict(Counter(durations))
    row["budget_limitations"] = "surviving AFL stats only; resume may accumulate time; not CPU time or full run history"
    return row


def paper_claims(paper):
    claims = []
    versions = dict(re.findall(r"\\texttt\{([^}]+)\}\s+(\d+\.\d+\.\d+)",
                               (paper / "chapters/evaluation.tex").read_text()))
    for line_number, line in enumerate((paper / "OUR_RESULTS.md").read_text().splitlines()[1:], 2):
        cells = line.split()
        if not cells:
            continue
        claims.append({"tool": "GRAFT", "crate": cells[0], "version": versions.get(cells[0]),
                       "targets": int(cells[2]), "compiled": int(cells[3]),
                       "covered": int(cells[5]), "total": int(cells[6]),
                       "seconds_per_target": int(cells[1]), "source": "OUR_RESULTS.md",
                       "line": line_number})
    for filename in ["BASELINE_RESULTS.md", "DEEPSURF_RESULTS.md"]:
        tool = "deepSURF" if filename.startswith("DEEP") else "RPG"
        for line_number, line in enumerate((paper / filename).read_text().splitlines(), 1):
            if line.startswith("## RULF"):
                tool = "RULF"
            cells = [x.strip().replace("**", "") for x in line.strip().strip("|").split("|")]
            if len(cells) < 5 or cells[0] not in CRATES or not re.fullmatch(r"\d+\.\d+\.\d+", cells[1]):
                continue
            covered_index = 4 if tool == "deepSURF" else 2
            if len(cells) <= covered_index + 1 or not cells[covered_index].isdigit() or not cells[covered_index + 1].isdigit():
                continue
            claims.append({"tool": tool, "crate": cells[0], "version": cells[1],
                           "covered": int(cells[covered_index]), "total": int(cells[covered_index + 1]),
                           "source": filename, "line": line_number})
    return claims


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("/home/cym/workspace"))
    parser.add_argument("--paper", type=Path, default=Path(__file__).resolve().parents[2] / "GRAFTPaper")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for historical in ["PLFuzz", "output_deepsurf", "deepsurf_crates", "RULF", "deepSURF", "Rust-Lib-Testing-main"]:
        forbidden = (args.workspace / historical).resolve()
        if args.output.resolve() == forbidden or forbidden in args.output.resolve().parents:
            parser.error("audit output must not be inside a historical source/artifact tree")
    args.output.mkdir(parents=True, exist_ok=True)
    tool = Path(__file__).resolve().parents[1]
    names = filter_names(tool / "src/document.rs")
    save(args.output / "filter_policy.json", {"names": names, "count": len(names),
         "source": artifact(tool / "src/document.rs"),
         "type_filter": "removes Error-suffixed enum definitions from type pool, not API rows"})
    claims = paper_claims(args.paper)
    save(args.output / "historical_claims.json", claims)
    records, recounts, issues = [], [], []
    summaries = []
    snapshots = {}
    for crate in CRATES:
        doc_path = args.workspace / "PLFuzz/output" / crate / "doc.json"
        if doc_path.is_file():
            snapshot = freeze(doc_path, crate, names)
            snapshots[crate] = snapshot
            save(args.output / "api_universes" / (crate + ".json"), snapshot)
    roots = [("GRAFT", "PLFuzz/output"), ("RULF", "PLFuzz/output_rulf"),
             ("RPG-merged", "PLFuzz/output_baseline_v2"),
             ("RPG-bfs", "PLFuzz/output_baseline_v2_bfs"),
             ("RPG-wubfs", "PLFuzz/output_baseline_v2_wubfs"),
             ("deepSURF", "output_deepsurf")]
    roots.extend(("ablation-" + mode, "PLFuzz/output_ablation/" + mode)
                 for mode in ["full", "no-generic", "no-graph", "no-topo"])
    for label, root in roots:
        for crate in CRATES if not label.startswith("ablation-") else ["base64", "byteorder", "fixedbitset", "time", "regex"]:
            path = args.workspace / root / crate
            if not path.exists() and crate == "unicode-segmentation":
                path = args.workspace / root / "unicode_segmentation"
            targets = None
            if label == "deepSURF":
                matches = sorted((args.workspace / "deepsurf_crates").glob(crate + "-*/deepSURF/fuzz/fuzzing_corpus"))
                if len(matches) == 1:
                    targets = matches[0]
            row = inventory(path, crate, targets)
            row.update(tool=label, crate=crate)
            records.append(row)
            reasons = ["missing_run_manifest", "no_independent_repetition_evidence"]
            if row.get("coverage_error"):
                reasons.append("invalid_coverage_json")
            if row.get("unreadable_stats_directories"):
                reasons.append("worker_stats_permission_denied")
            if not row["exists"]:
                reasons.append("artifact_directory_missing")
            if not row.get("worker_stats"):
                reasons.append("worker_budget_not_observed")
            if crate not in snapshots:
                reasons.append("api_snapshot_missing")
            else:
                snapshot = snapshots[crate]
                if snapshot["historical_duplicate_rows"]:
                    reasons.append("duplicate_api_rows_require_counting_policy")
                versions = row.get("coverage_source_versions", [])
                if versions and versions != [snapshot["version"]]:
                    reasons.append("coverage_version_differs_from_graft_snapshot")
                cov_path = path / "coverage_detail.json"
                if cov_path.is_file() and versions == [snapshot["version"]]:
                    try:
                        result = measure(snapshot, load(cov_path))
                        result["coverage_sha256"] = row["files"]["coverage_detail.json"]["sha256"]
                        result["tool"] = label
                        output = "recounts/{}-{}.json".format(label, crate)
                        save(args.output / output, result)
                        recounts.append({"tool": label, "crate": crate, "report": output,
                                         "groups": result["groups"]})
                        selected = result["groups"]["historical_selected"]
                        if selected["unresolved"]:
                            reasons.append("unresolved_function_mapping")
                    except (ValueError, KeyError, TypeError) as error:
                        reasons.append("recount_failed:" + str(error))
            claim_tool = "RPG" if label.startswith("RPG-") else label
            matching_claims = [c for c in claims if c["crate"] == crate and c["tool"] == claim_tool]
            if matching_claims:
                claim = matching_claims[0]
                observed_versions = row.get("coverage_source_versions", [])
                if claim.get("version") and observed_versions and observed_versions != [claim["version"]]:
                    reasons.append("historical_claim_version_differs_from_present_artifacts")
                if claim.get("targets") and claim["targets"] != len(row.get("targets", [])):
                    reasons.append("present_target_count_differs_from_paper")
                if crate in snapshots and claim["total"] != sum(a["selected_by_historical_doc"] for a in snapshots[crate]["apis"]):
                    reasons.append("paper_denominator_differs_from_graft_snapshot")
            summaries.append({"tool": label, "crate": crate,
                              "rustdoc_version": row.get("rustdoc_version"),
                              "coverage_versions": ";".join(row.get("coverage_source_versions", [])),
                              "lock_versions": json.dumps(row.get("lock_target_versions", {}), sort_keys=True),
                              "targets_present": len(row.get("targets", [])),
                              "worker_stats_present": len(row.get("worker_stats", [])),
                              "surviving_stats_run_time_seconds_sum": row.get("surviving_stats_run_time_seconds_sum"),
                              "decision": "recount_diagnostic_only_rerun_required",
                              "reasons": ";".join(reasons)})
    save(args.output / "artifact_inventory.json", records)
    save(args.output / "recount_summary.json", recounts)
    with (args.output / "rerun_matrix.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    graft = [c for c in claims if c["tool"] == "GRAFT"]
    save(args.output / "historical_totals.json", {
        "generated": sum(c["targets"] for c in graft), "compiled": sum(c["compiled"] for c in graft),
        "covered": sum(c["covered"] for c in graft), "total": sum(c["total"] for c in graft),
        "macro_api_percent": sum(c["covered"] / c["total"] for c in graft) * 100 / len(graft),
        "micro_api_percent": sum(c["covered"] for c in graft) * 100 / sum(c["total"] for c in graft),
        "configured_successful_target_hours": sum(c["compiled"] * c["seconds_per_target"] / 3600 for c in graft),
        "note": "recomputed historical claims; not revalidated execution measurements"})
    save(args.output / "current_environment.json", {"scope": "current host, not historical run environment",
         "commands": [command(["rustc", "-vV"]), command(["rustup", "toolchain", "list"]),
                      command(["git", "-C", str(tool), "rev-parse", "HEAD"]),
                      command(["git", "-C", str(args.paper), "rev-parse", "HEAD"])],
         "audit_sources": [artifact(Path(__file__)), artifact(tool / "scripts/coverage_snapshot.py")],
         "paper_sources": [artifact(args.paper / p) for p in
                           ["OUR_RESULTS.md", "BASELINE_RESULTS.md", "DEEPSURF_RESULTS.md", "ABLATION_RESULT.md"]]})
    source_files = [tool / "Cargo.toml", tool / "Cargo.lock", *sorted((tool / "src").rglob("*.rs"))]
    source_identity = {str(p.relative_to(tool)): file_hash(p) for p in source_files}
    save(args.output / "tool_source_identity.json", {"files": source_identity,
         "source_sha256": digest(source_identity), "scope": "Cargo.toml, Cargo.lock and src/**/*.rs"})
    versions = []
    for crate, snapshot in snapshots.items():
        candidates = [r for r in records if r["crate"] == crate]
        historical_version = snapshot["version"]
        for label in ["RULF", "RPG-merged"]:
            row = next(r for r in candidates if r["tool"] == label)
            observed = row.get("coverage_source_versions", [])
            if len(observed) == 1:
                historical_version = observed[0]
                break
        versions.append({"crate": crate, "newer_track_version": snapshot["version"],
                         "newer_track_historical_universe_sha256": snapshot["universe_sha256"],
                         "historical_compatibility_candidate": historical_version,
                         "status": "exact_versions_frozen; common_tool_feasibility_and_public_surface_pending"})
    save(args.output / "version_tracks.json", versions)
    lines = ["# 第一周：历史实验数据审计", "",
        "这是现存产物的诊断复算，不是重新运行 fuzzing 的结果。所有历史主比较仍需在共同版本、共同分母和资源预算下重跑。",
        "", "## GRAFT 主结果与现存覆盖文件", "",
        "旧规则列使用冻结的原 API 行数和行区间算法；新函数入口规则的歧义项显式保留。相同计数仅说明当前导出能重算该数字，不证明它来自论文声明的同一次实验。", "",
        "| Crate | 文档版本 | 论文 covered/total | 当前导出按旧规则 | 入口规则 unresolved 行 | 当前目标目录 / 论文目标数 |",
        "|---|---|---|---|---|---|"]
    for claim in graft:
        crate = claim["crate"]
        row = next(r for r in records if r["tool"] == "GRAFT" and r["crate"] == crate)
        result = next((r for r in recounts if r["tool"] == "GRAFT" and r["crate"] == crate), None)
        old, unresolved = "缺失/不可复算", "未知"
        if result:
            output = load(args.output / result["report"])
            legacy = output["legacy_segment_diagnostic"]
            old = "{}/{} ({:.2f}%)".format(legacy["covered_rows"], legacy["total_rows"], legacy["percent"])
            unresolved = str(output["groups"]["historical_selected_rows_with_duplicates"]["unresolved"])
        lines.append("| {} | {} | {}/{} | {} | {} | {} / {} |".format(
            crate, row.get("rustdoc_version"), claim["covered"], claim["total"], old,
            unresolved, len(row["targets"]), claim["targets"]))
    lines.extend(["", "## 已确定的证据限制", "",
        "- regex、fixedbitset、time、byteorder 的当前旧规则复算值对应消融 full 列，不复现主表；仅凭文件无法断定发生覆盖或替换的具体过程。",
        "- chrono、time、bytes 的历史 APIDoc 含重复函数 ID；快照同时提供唯一函数定义与重复行口径，待明确 impl/重导出身份后再确定正式分母。",
        "- csv 的现存 doc.json 有 152 行，论文写 148；当前主目录未找到 coverage_detail.json。",
        "- 部分 RPG 锁文件和覆盖文件版本与实验笔记记载不同，必须逐份区分；RULF/deepSURF 的若干版本也不同于 GRAFT。",
        "- 当前 AFL stats 包含长于论文预算的会话；它们不等于历史完整预算或 CPU 小时。",
        "- full 消融专用目录缺失；RPG-bfs 的 unicode-segmentation 覆盖导出为空；部分 deepSURF stats 无读取权限。",
        "", "## 可复核产物", "",
        "- [待重跑原因矩阵](rerun_matrix.csv)", "- [版本轨道与候选共同版本](version_tracks.json)",
        "- [42 条历史数字及出处](historical_claims.json)", "- [产物清单与哈希](artifact_inventory.json)",
        "- [13 份冻结 API 清单](api_universes/)", "- [同版本诊断复算索引](recount_summary.json)",
        "- [过滤名单与类型池过滤含义](filter_policy.json)",
        "", "正式分母尚待可见性、重导出、条件 impl 与宏映射核验；当前快照作为固定审计对象使用，不声称已完整识别 Rust 公共 API。",
        ""])
    (args.output / "report.md").write_text("\n".join(lines))
    print(json.dumps({"artifact_groups": len(records), "snapshots": len(snapshots),
                      "diagnostic_recounts": len(recounts), "output": str(args.output)}))


if __name__ == "__main__":
    main()
