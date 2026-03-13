import json
import sys
import re
from pathlib import Path

if len(sys.argv) != 3:
    print("Usage: python api_coverage.py <rustdoc_json> <coverage_detail.txt>")
    sys.exit(1)

json_file = Path(sys.argv[1])
coverage_file = Path(sys.argv[2])


# --------------------------------------------------
# 1 解析 rustdoc JSON
# --------------------------------------------------

print("[INFO] Parsing rustdoc JSON...")

with open(json_file) as f:
    doc = json.load(f)

index = doc["index"]

pub_functions = []

for item in index.values():

    inner = item.get("inner", {})

    if "function" not in inner:
        continue

    vis = item.get("visibility")

    is_pub = False
    if vis == "public":
        is_pub = True
    elif isinstance(vis, dict) and vis.get("public"):
        is_pub = True

    if not is_pub:
        continue

    span = item.get("span")
    if not span:
        continue

    filename = span["filename"]
    start = span["begin"][0]
    end = span["end"][0]

    pub_functions.append((filename, start, end))

print(f"[INFO] Found {len(pub_functions)} public functions")


# --------------------------------------------------
# 2 解析 llvm-cov show
# --------------------------------------------------

print("[INFO] Parsing coverage report...")

coverage = {}

current_file = None

line_pattern = re.compile(r"\s*(\d+)\|\s*([0-9]*)\|")

with open(coverage_file) as f:

    for line in f:

        stripped = line.strip()

        if stripped.endswith(".rs:"):
            current_file = stripped[:-1]
            coverage[current_file] = {}
            continue

        if current_file is None:
            continue

        m = line_pattern.match(line)

        if not m:
            continue

        lineno = int(m.group(1))

        count_str = m.group(2).strip()
        count = int(count_str) if count_str else 0

        coverage[current_file][lineno] = count

print(f"[INFO] Parsed coverage for {len(coverage)} files")


# --------------------------------------------------
# 3 路径匹配
# --------------------------------------------------

def find_coverage_file(json_path):

    json_path = str(Path(json_path))

    for cov_file in coverage:

        if cov_file.endswith(json_path):
            return cov_file

        if json_path.endswith(cov_file):
            return cov_file

        if Path(cov_file).name == Path(json_path).name:
            return cov_file

    return None


# --------------------------------------------------
# 4 计算 API coverage
# --------------------------------------------------

print("[INFO] Calculating API coverage...")

total_lines = 0
covered_lines = 0

total_functions = 0
covered_functions = 0

for file, start, end in pub_functions:

    cov_file = find_coverage_file(file)

    if not cov_file:
        continue

    total_functions += 1

    func_executed = False

    for line in range(start, end + 1):

        total_lines += 1

        count = coverage[cov_file].get(line, 0)

        if count > 0:
            covered_lines += 1
            func_executed = True

    if func_executed:
        covered_functions += 1


if total_lines == 0:
    print("[ERROR] No API lines counted")
    sys.exit(1)


line_ratio = covered_lines / total_lines * 100
func_ratio = covered_functions / total_functions * 100


print("\n============= API COVERAGE =============")

print("\n[API Line Coverage]")
print(f"Total API lines:   {total_lines}")
print(f"Covered API lines: {covered_lines}")
print(f"Coverage:          {line_ratio:.2f}%")

print("\n[API Function Coverage]")
print(f"Total API funcs:   {total_functions}")
print(f"Covered API funcs: {covered_functions}")
print(f"Coverage:          {func_ratio:.2f}%")

print("\n========================================")