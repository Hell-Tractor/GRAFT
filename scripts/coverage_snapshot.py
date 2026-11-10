#!/usr/bin/env python3
"""Offline, conservative API/function-count audit. Python 3.9+, no dependencies.

This freezes a documented candidate universe, not a claim of complete Rust public
API discovery. Never falls back to a live crate lookup or basename-only matching.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re


SCHEMA = "graft-api-snapshot-v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def filter_names(source):
    match = re.search(r"const FILTER_API_NAMES:.*?=\s*&\[(.*?)\];", Path(source).read_text(), re.S)
    if not match:
        raise ValueError("cannot find the historical filter list")
    return sorted(set(re.findall(r'"([^"\n]+)"', match[1])))


def source_file(filename, crate, version, source_root=None):
    """Require the exact crate-version path component (or an explicit root)."""
    path = Path(filename)
    if ".." in path.parts:
        return None
    if source_root:
        try:
            return path.relative_to(Path(source_root)).as_posix()
        except ValueError:
            pass
    parts = path.parts
    marker = "{}-{}".format(crate, version)
    if marker in parts:
        return Path(*parts[parts.index(marker) + 1:]).as_posix()
    return None


def freeze(doc_path, crate, names, source_root=None, expected_version=None):
    document = load(doc_path)
    krate = document.get("parser", {}).get("krate", document)
    index = krate["index"]
    root = index[str(krate["root"])]
    version = krate.get("crate_version")
    if not version or (expected_version and expected_version != version):
        raise ValueError("missing or inconsistent rustdoc crate version")
    if root.get("name", "").replace("_", "-") != crate.replace("_", "-"):
        raise ValueError("crate name does not match rustdoc root")
    local_id = root["crate_id"]
    selected_rows = Counter(str(api["id"]) for api in document.get("apis", []))
    selected = set(selected_rows)
    owners = defaultdict(list)
    for item in index.values():
        inner = item.get("inner", {})
        if "impl" in inner:
            impl = inner["impl"]
            owner = {"for": impl["for"], "trait": impl.get("trait"),
                     "generics": impl.get("generics", {}),
                     "is_synthetic": impl.get("is_synthetic", False)}
            for item_id in impl.get("items", []):
                owners[str(item_id)].append(owner)
        if "trait" in inner:
            for item_id in inner["trait"].get("items", []):
                owners[str(item_id)].append({"trait_declaration": item.get("name")})

    # Freeze all function records, including external/blanket candidates, and
    # record exclusion reasons. The selected set exactly preserves APIDoc IDs.
    apis = []
    for item_id, item in index.items():
        function = item.get("inner", {}).get("function")
        if function is None:
            continue
        span = item.get("span")
        filename = span.get("filename") if span else None
        relative = source_file(filename, crate, version, source_root) if filename else None
        own = owners.get(str(item_id), [])
        is_local = item.get("crate_id") == local_id
        is_public_candidate = (item.get("visibility") == "public" or
                               any(o.get("trait") is not None for o in own))
        reasons = []
        if not is_local:
            reasons.append("external_definition")
        if not is_public_candidate:
            reasons.append("not_public_or_trait_impl")
        if any("trait_declaration" in o for o in own):
            reasons.append("trait_declaration_requires_surface_review")
        if not function.get("has_body", False):
            reasons.append("no_body")
        # Visibility through modules/reexports and impl usability still require
        # independent surface validation, hence 'candidate', never 'all public'.
        candidate = is_local and is_public_candidate and not any(
            "trait_declaration" in o for o in own)
        semantic = {"crate": crate, "version": version, "name": item.get("name"),
                    "owners": sorted(own, key=lambda x: canonical(x)),
                    "function": function, "file": relative,
                    "begin": span.get("begin") if span else None,
                    "end": span.get("end") if span else None}
        apis.append({"api_id": digest(semantic), "rustdoc_id": str(item_id),
                     "name": item.get("name"), "owners": semantic["owners"],
                     "signature": function, "file": relative,
                     "original_file": filename,
                     "begin": semantic["begin"], "end": semantic["end"],
                     "source_sha256": file_hash(filename) if filename and Path(filename).is_file() else None,
                     "selected_by_historical_doc": str(item_id) in selected,
                     "historical_row_multiplicity": selected_rows[str(item_id)],
                     "local_callable_candidate": candidate,
                     "historical_name_filter": item.get("name") in names,
                     "deprecated": item.get("deprecation") is not None,
                     "exclusion_reasons": reasons,
                     "mapping_limitations": (["unresolved_source_path"] if not relative else []) +
                         (["no_source_span"] if not span else [])})
    ids = [a["api_id"] for a in apis]
    if len(ids) != len(set(ids)):
        # Distinct records sharing all semantic information require review, not
        # silent deduplication or inflated counts.
        collisions = Counter(ids)
        for api in apis:
            if collisions[api["api_id"]] > 1:
                api["mapping_limitations"].append("duplicate_semantic_identity")
                api["api_id"] += ":" + api["rustdoc_id"]
    available_ids = {a["rustdoc_id"] for a in apis}
    if selected - available_ids:
        raise ValueError("APIDoc references functions absent from its embedded rustdoc")
    snapshot = {"schema": SCHEMA, "crate": crate, "version": version,
                "rustdoc_format": krate.get("format_version"),
                "includes_private": krate.get("includes_private"),
                "status": "frozen_historical_audit_not_validated_public_surface",
                "surface_limitations": ["module/reexport visibility and impl usability require review",
                                        "generic instantiations are not separate API items",
                                        "external trait bodies remain explicit historical entries"],
                "source_root": str(source_root) if source_root else None,
                "input_doc_sha256": file_hash(doc_path),
                "historical_apidoc_rows": len(document.get("apis", [])),
                "historical_duplicate_rows": len(document.get("apis", [])) - len(selected),
                "filter_names": names, "apis": sorted(apis, key=lambda a: a["api_id"])}
    snapshot["universe_sha256"] = digest(snapshot)
    return snapshot


def verify(snapshot):
    if snapshot.get("schema") != SCHEMA:
        raise ValueError("unsupported snapshot schema")
    unhashed = {key: value for key, value in snapshot.items() if key != "universe_sha256"}
    if digest(unhashed) != snapshot.get("universe_sha256"):
        raise ValueError("API universe hash mismatch")


def measure(snapshot, coverage):
    verify(snapshot)
    if coverage.get("type") != "llvm.coverage.json.export":
        raise ValueError("expected LLVM coverage JSON export")
    data = coverage.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("coverage has no data records")
    entries = defaultdict(list)
    for api in snapshot["apis"]:
        if api["file"] and api["begin"] and api["end"]:
            entries[(api["file"], tuple(api["begin"]))].append(api)
    observations = defaultdict(list)
    unmatched = []
    version_conflicts = set()
    has_functions = False
    for record in data:
        if "functions" not in record:
            raise ValueError("function records missing; file segments cannot establish API entry counts")
        for file in record.get("files", []):
            filename = file.get("filename", "")
            for part in Path(filename).parts:
                if re.fullmatch(re.escape(snapshot["crate"]) + r"-\d[^/]*", part):
                    if part != snapshot["crate"] + "-" + snapshot["version"]:
                        version_conflicts.add(part)
        for function in record["functions"]:
            has_functions = True
            regions = function.get("regions", [])
            filenames = function.get("filenames", [])
            for filename in filenames:
                for part in Path(filename).parts:
                    if re.fullmatch(re.escape(snapshot["crate"]) + r"-\d[^/]*", part):
                        if part != snapshot["crate"] + "-" + snapshot["version"]:
                            version_conflicts.add(part)
            # LLVM's first region is the function body/entry region. Do not
            # substitute arbitrary executed lines or expansion regions.
            if not regions or len(regions[0]) < 8:
                raise ValueError("missing LLVM function entry region")
            region = regions[0]
            file_index = region[5]
            if not isinstance(file_index, int) or not 0 <= file_index < len(filenames):
                raise ValueError("invalid function filename index")
            if region[7] != 0:
                unmatched.append({"name": function.get("name"), "reason": "non_code_entry_region"})
                continue
            relative = source_file(filenames[file_index], snapshot["crate"],
                                   snapshot["version"], snapshot.get("source_root"))
            if relative is None:
                continue
            candidates = entries.get((relative, (region[0], region[1])), [])
            candidates = [a for a in candidates if tuple(region[2:4]) <= tuple(a["end"])]
            if len(candidates) != 1:
                unmatched.append({"name": function.get("name"), "file": relative,
                                  "begin": region[:2], "reason": "ambiguous_entry" if candidates else "no_exact_entry",
                                  "candidate_api_ids": [a["api_id"] for a in candidates]})
                continue
            count = function.get("count")
            if not isinstance(count, int) or count < 0:
                raise ValueError("invalid LLVM function count")
            observations[candidates[0]["api_id"]].append(count)
    if version_conflicts:
        raise ValueError("coverage contains a different target crate version: " + ", ".join(sorted(version_conflicts)))
    results = []
    for api in snapshot["apis"]:
        counts = observations.get(api["api_id"], [])
        status = "covered" if any(counts) else "observed_zero" if counts else "unresolved"
        if api["mapping_limitations"]:
            status = "unresolved"
        results.append({"api_id": api["api_id"], "name": api["name"], "file": api["file"],
                        "status": status, "function_records": len(counts),
                        "max_execution_count": max(counts) if counts else None,
                        "selected": api["selected_by_historical_doc"],
                        "historical_row_multiplicity": api["historical_row_multiplicity"],
                        "public_name_body_definition": api.get("public_name_body_definition", False),
                        "public_surface_review_item": api.get("public_surface_review_item", False),
                        "candidate": api["local_callable_candidate"],
                        "name_filtered": api["historical_name_filter"]})
    groups = {}
    subsets = [
        ("historical_selected", [r for r in results if r["selected"]]),
        ("local_callable_candidates_unfiltered", [r for r in results if r["candidate"]]),
        ("local_callable_candidates_name_filtered", [r for r in results if r["candidate"] and not r["name_filtered"]]),
    ]
    if any("public_name_body_definition" in api for api in snapshot["apis"]):
        subsets.extend([
            ("public_name_body_definitions", [r for r in results if r["public_name_body_definition"]]),
            ("public_surface_review_items", [r for r in results if r["public_surface_review_item"]]),
        ])
    for name, subset in subsets:
        counts = Counter(r["status"] for r in subset)
        total = len(subset)
        groups[name] = {"total": total, "covered": counts["covered"],
                        "observed_zero": counts["observed_zero"], "unresolved": counts["unresolved"],
                        "confirmed_coverage_percent": 100 * counts["covered"] / total if total else None}
    weighted = Counter()
    for result in results:
        weighted[result["status"]] += result["historical_row_multiplicity"]
    total_rows = sum(weighted.values())
    groups["historical_selected_rows_with_duplicates"] = {
        "total": total_rows, "covered": weighted["covered"],
        "observed_zero": weighted["observed_zero"], "unresolved": weighted["unresolved"],
        "confirmed_coverage_percent": 100 * weighted["covered"] / total_rows if total_rows else None}
    # Reproduce the old line/segment rule as a diagnostic control. It is not
    # accepted as function-entry evidence, and shared lines can overcount.
    active_lines = defaultdict(set)
    for record in data:
        for file in record.get("files", []):
            relative = source_file(file["filename"], snapshot["crate"], snapshot["version"],
                                   snapshot.get("source_root"))
            if relative:
                active_lines[relative].update(s[0] for s in file.get("segments", [])
                    if len(s) >= 6 and s[3] and s[2] > 0 and s[4] and not s[5])
    legacy_covered = sum(a["historical_row_multiplicity"] for a in snapshot["apis"]
        if a["begin"] and a["end"] and any(a["begin"][0] <= line <= a["end"][0]
            for line in active_lines.get(a["file"], [])))
    return {"schema": "graft-coverage-audit-v1", "crate": snapshot["crate"],
            "version": snapshot["version"], "universe_sha256": snapshot["universe_sha256"],
            "status": "conservative_existing_export_audit_not_new_fuzzing_result",
            "mapping_policy": "unique_exact_function_entry_position_v1",
            "has_function_records": has_functions, "groups": groups,
            "legacy_segment_diagnostic": {"covered_rows": legacy_covered, "total_rows": total_rows,
                "percent": 100 * legacy_covered / total_rows if total_rows else None,
                "validity": "historical line-region rule, not validated API-entry coverage"},
            "unmatched_functions": unmatched, "apis": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_args = commands.add_parser("freeze")
    freeze_args.add_argument("--doc", required=True, type=Path)
    freeze_args.add_argument("--crate", required=True)
    freeze_args.add_argument("--version")
    freeze_args.add_argument("--source-root", type=Path)
    freeze_args.add_argument("--filter-source", type=Path,
                             default=Path(__file__).resolve().parents[1] / "src/document.rs")
    freeze_args.add_argument("--output", required=True, type=Path)
    coverage_args = commands.add_parser("measure")
    coverage_args.add_argument("--universe", required=True, type=Path)
    coverage_args.add_argument("--coverage", required=True, type=Path)
    coverage_args.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = freeze(args.doc, args.crate, filter_names(args.filter_source),
                            args.source_root, args.version)
        else:
            result = measure(load(args.universe), load(args.coverage))
            result["coverage_sha256"] = file_hash(args.coverage)
        save(args.output, result)
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.exit(2, "audit error: {}\n".format(error))


if __name__ == "__main__":
    main()
