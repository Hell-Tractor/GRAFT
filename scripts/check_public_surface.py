#!/usr/bin/env python3
"""Cross-check frozen item identities with the existing rustdoc visibility adapter."""
import argparse
import copy
from collections import Counter
from pathlib import Path
import re
import subprocess

from coverage_snapshot import digest, file_hash, load, measure, save, verify


def doctest_analysis_view(document):
    """Repair only a tightly specified, visibility-neutral doctest attribute.

    The returned copy is a separately hashed analysis view, never a replacement
    for the frozen rustdoc. Other malformed attributes remain errors.
    """
    view = copy.deepcopy(document)
    krate = view.get("parser", {}).get("krate", view)
    edits = []
    allowed = re.compile(r"^#\[doc\(test\(attr\((?:deny|allow)\([A-Za-z0-9_, ]+\)\)\)\)$")
    for item_id, item in krate["index"].items():
        for attribute in item.get("attrs", []):
            if isinstance(attribute, dict) and isinstance(attribute.get("other"), str):
                raw = attribute["other"]
                if allowed.fullmatch(raw):
                    attribute["other"] = raw + "]"
                    edits.append({"item_id": item_id, "before": raw, "after": raw + "]",
                                  "scope": "doc(test(attr(deny/allow(...)))) only; no API signature or visibility edit"})
    return view, edits


def run(command, require_success=True):
    result = subprocess.run([str(part) for part in command], capture_output=True, text=True, timeout=60)
    if require_success:
        result.check_returncode()
    return {"command": [str(part) for part in command], "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("/home/cym/workspace"))
    parser.add_argument("--toolchain", default="nightly")
    parser.add_argument("--normalize-doctest-attributes", action="store_true",
                        help="use a separate, logged analysis copy for the known malformed doctest-only attributes")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    fixture_dir = args.output / "fixture"
    fixture_dir.mkdir(exist_ok=True)
    commands = [run(["rustdoc", "+" + args.toolchain, root / "experiments/week1/fixtures/public_surface.rs",
                    "--crate-name", "public_surface", "--crate-version", "0.1.0", "--edition", "2024",
                    "--document-private-items", "--output-format", "json", "-Z", "unstable-options",
                    "-o", fixture_dir])]
    commands.append(run([args.binary, fixture_dir / "public_surface.json", fixture_dir / "crosscheck.json"]))
    fixture = load(fixture_dir / "crosscheck.json")
    commands.append(run([args.binary, fixture_dir / "public_surface.json", fixture_dir / "repeat.json"]))
    assert fixture["functions"] == load(fixture_dir / "repeat.json")["functions"], "visibility evidence is not deterministic"
    own = [f for f in fixture["functions"] if f["status"] != "external_definition"]
    states = {f["name"]: f["status"] for f in own if f["name"] != "default_method"}
    assert states["exposed_by_alias"] == "publicly_named_function", states
    assert states["not_exported"] == "not_publicly_named_function", states
    assert states["public_method"] == "method_on_publicly_named_receiver", states
    assert states["private_method"] == "private_inherent_method", states
    defaults = {f["status"] for f in own if f["name"] == "default_method"}
    assert "public_trait_declaration" in defaults, defaults
    assert "trait_method_on_publicly_named_receiver" in defaults, defaults
    summaries = []
    for universe_path in sorted((args.audit / "api_universes").glob("*.json")):
        universe = load(universe_path)
        verify(universe)
        doc_path = args.workspace / "PLFuzz/output" / universe["crate"] / "doc.json"
        assert file_hash(doc_path) == universe["input_doc_sha256"], str(doc_path)
        output = args.output / (universe["crate"] + ".json")
        commands.append(run([args.binary, doc_path, output], require_success=False))
        raw_diagnostic = commands[-1]
        normalization = None
        if raw_diagnostic["exit_code"] != 0 and args.normalize_doctest_attributes:
            view, edits = doctest_analysis_view(load(doc_path))
            if edits:
                view_path = args.output / "normalized_inputs" / (universe["crate"] + ".json")
                save(view_path, view)
                save(args.output / (universe["crate"] + "-raw-diagnostic.json"), raw_diagnostic)
                commands.append(run([args.binary, view_path, output], require_success=False))
                normalization = {"edits": edits, "analysis_view_path": str(view_path),
                                 "analysis_view_sha256": file_hash(view_path),
                                 "original_sha256": universe["input_doc_sha256"],
                                 "raw_diagnostic": raw_diagnostic}
        if commands[-1]["exit_code"] != 0:
            failure = {"crate": universe["crate"], "version": universe["version"],
                       "analysis_status": "failed_original_metadata_not_normalized",
                       "input_doc_sha256": universe["input_doc_sha256"],
                       "universe_sha256": universe["universe_sha256"],
                       "diagnostic": commands[-1], "report": output.name}
            save(output, failure)
            summaries.append(failure)
            continue
        report = load(output)
        assert report["version"] == universe["version"]
        functions = {f["rustdoc_id"]: f for f in report["functions"]}
        selected = Counter()
        all_local = Counter()
        for entry in universe["apis"]:
            function = functions[entry["rustdoc_id"]]
            selected[function["status"]] += entry["historical_row_multiplicity"]
            if function["status"] != "external_definition":
                all_local[function["status"]] += 1
        report["input_doc_sha256"] = universe["input_doc_sha256"]
        report["universe_sha256"] = universe["universe_sha256"]
        report["binary_sha256"] = file_hash(args.binary)
        if normalization:
            report["doctest_analysis_view_normalization"] = normalization
        save(output, report)
        public_statuses = {"publicly_named_function", "method_on_publicly_named_receiver",
                           "trait_method_on_publicly_named_receiver", "public_trait_declaration"}
        review_statuses = {"owner_visibility_or_applicability_unresolved",
                           "trait_impl_requires_applicability_or_opaque_receiver_review"}
        public_universe = copy.deepcopy(universe)
        public_universe["original_universe_sha256"] = universe["universe_sha256"]
        public_universe["public_surface_policy"] = {
            "name": "publicly_named_crate_owned_function_definitions_with_body_v1",
            "source": "rustdoc adapter publicly importable paths and associated receiver/trait owners",
            "impl_obligations": "included syntactically; no satisfiability or constructibility guarantee",
            "unknown_items": "retained separately for sensitivity, not silently classified public or private",
            "declarations_without_body": "separately reported; not an instrumentable function definition",
            "crosscheck_semantic_sha256": digest({
                "functions": report["functions"], "input_doc_sha256": report["input_doc_sha256"],
                "binary_sha256": report["binary_sha256"],
                "normalization_edits": normalization["edits"] if normalization else []})}
        for entry in public_universe["apis"]:
            function = functions[entry["rustdoc_id"]]
            entry["public_surface_status"] = function["status"]
            entry["public_name_body_definition"] = function["status"] in public_statuses and function["has_body"]
            entry["public_surface_review_item"] = function["status"] in review_statuses
        public_universe["universe_sha256"] = digest({k: v for k, v in public_universe.items() if k != "universe_sha256"})
        save(args.output / "api_universes" / (universe["crate"] + ".json"), public_universe)
        measurement = None
        coverage_path = doc_path.parent / "coverage_detail.json"
        if coverage_path.is_file():
            measurement = measure(public_universe, load(coverage_path))
            measurement["coverage_sha256"] = file_hash(coverage_path)
            save(args.output / "recounts" / (universe["crate"] + ".json"), measurement)
        summaries.append({"crate": universe["crate"], "version": universe["version"],
                          "analysis_status": ("completed_with_logged_doctest_analysis_view" if normalization else
                                              "completed_visibility_only_impl_obligations_unverified"),
                          "historical_selected_rows_by_visibility": dict(selected),
                          "all_local_function_items_by_visibility": dict(all_local),
                          "public_name_body_definitions": sum(e["public_name_body_definition"] for e in public_universe["apis"]),
                          "public_declarations_without_body": sum(f["status"] in public_statuses and not f["has_body"] for f in report["functions"]),
                          "surface_review_items": sum(e["public_surface_review_item"] for e in public_universe["apis"]),
                          "public_universe_sha256": public_universe["universe_sha256"],
                          "measurement_groups": measurement["groups"] if measurement else None,
                          "report": output.name})
        assert file_hash(doc_path) == universe["input_doc_sha256"], "original input changed during cross-check"
    save(args.output / "summary.json", summaries)
    tracks = load(args.audit / "version_tracks.json")
    by_crate = {row["crate"]: row for row in summaries}
    for track in tracks:
        track["newer_track_public_definition_universe_sha256"] = by_crate[track["crate"]].get("public_universe_sha256")
        track["public_definition_scope"] = "publicly_named_crate_owned_function_definitions_with_body_v1"
    save(args.output / "version_tracks.json", tracks)
    lines = ["# 公开路径与分母范围交叉检查", "",
             "本表统计冻结文档中的库自有、公开命名/关联且有函数体的唯一定义，未按方法名称过滤。它不表示所有 impl 均可实例化，也不是全部 Rust callable 的完备集合。诊断覆盖是实际入口已确认的下界，未映射项仍在分母中。", "",
             "| Crate | 公开定义（有 body） | 公开声明（无 body） | owner/适用性待核项 | 已确认入口 / 定义数 | 映射未决 | 属性分析副本 |",
             "|---|---|---|---|---|---|---|"]
    for row in summaries:
        if not row["analysis_status"].startswith("completed"):
            lines.append("| {} | 未知 | 未知 | 未知 | 分析失败 | 未知 | 原始失败记录保留 |".format(row["crate"]))
            continue
        groups = row["measurement_groups"]
        group = groups["public_name_body_definitions"] if groups else None
        counts = "{}/{}".format(group["covered"], group["total"]) if group else "缺少导出"
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            row["crate"], row["public_name_body_definitions"], row["public_declarations_without_body"],
            row["surface_review_items"], counts, group["unresolved"] if group else "未知",
            "显式修复 doctest 属性" if "analysis_view" in row["analysis_status"] else "未使用"))
    lines.extend(["", "可复核数据：[汇总](summary.json)、[验证与命令](validation.json)、api_universes/、recounts/。",
                  "三个 doctest 分析副本保留原文档哈希、逐条编辑和原始失败诊断；原始实验文档未被修改。",
                  "未知 owner/opaque/blanket 项单列，宏及 inline 缺少唯一函数入口记录时仍为 unresolved；不能用这些诊断比例替换论文正式结果。", ""])
    (args.output / "report.md").write_text("\n".join(lines))
    successes = sum(row["analysis_status"].startswith("completed") for row in summaries)
    save(args.output / "validation.json", {"fixture_passed": True, "fixture_assertions": 7,
         "rustdoc_toolchain": commands[0], "binary_sha256": file_hash(args.binary),
         "script_sha256": file_hash(__file__), "mapping_script_sha256": file_hash(root / "scripts/coverage_snapshot.py"),
         "attempted_crates": len(summaries), "successful_crates": successes, "commands": commands,
         "limitations": ["trait impl applicability is not solved",
                         "naming visibility is not constructibility or runtime reachability",
                         "opaque receiver and blanket impl cases remain explicit review items"]})
    print("public-surface fixture passed; {} of {} frozen crate analyses completed".format(successes, len(summaries)))


if __name__ == "__main__":
    main()
