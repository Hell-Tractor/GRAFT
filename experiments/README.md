# 实验数据口径与第一周审计

> 后续重跑采用用户新约束：总墙钟预算最多 24 小时，版本不兼容时所有工具共同降级。
> 最新共同版本/锁文件见 [week4/common-versions.json](week4/common-versions.json)，
> 第二轮分母见 `week4/common-universes-v2/`，完整时间评估见
> [week4/budget-first-report.md](week4/budget-first-report.md)。
> [protocol-v2-draft.json](protocol-v2-draft.json) 尚未冻结正式时长；勿直接启动旧的每目标数小时重跑脚本。

新测量采用离线 API 快照，覆盖测量不重新联网查询 crate。历史目录只读；不能把当前目录中的所有目标、最新 AFL stats 或最新 coverage JSON 自动视为论文原始那一次运行。

## 已交付

- `protocol-v1.json`：两条版本轨道、指标含义、预算与重复规则、运行 manifest 必需字段。正式重复数、实际参数和模型预算仍需在 pilot 后、正式运行前固定。
- `week1/audit/api_universes/`：13 个 GRAFT 历史 rustdoc 快照及哈希，保存原 APIDoc 所选项、重复行、类型/位置、过滤标记和来源文件哈希。
- `week1/audit/artifact_inventory.json`：GRAFT、RULF、RPG、deepSURF 及消融目录的文件哈希、Cargo.lock 版本、目标身份和可读 AFL stats。
- `week1/audit/recounts/`：同版本文件的离线诊断复算，分开列已确认执行、已观察零计数、无法映射，以及旧行区间算法对照。
- `week1/audit/rerun_matrix.csv`：每个工具/crate 的待重跑原因。历史比较均缺少完整 run manifest 与独立重复证据。
- `week1/coverage_validation/`：真实 Rust/LLVM 微型验证产物；`scripts/test_coverage_snapshot.py` 补充歧义、分母、版本冲突等回归检查。
- `week1/public_surface/`：用现有 rustdoc adapter 独立核对公开路径和重导出，覆盖 13 个 crate；`api_universes/` 是加入公开定义标记的新快照，`recounts/` 另报未按名称过滤的公开函数定义诊断计数。

快照中的 `local_callable_candidate` **不是已证明完整的公共 API 集**：该字段未编码模块/重导出可见性、条件 impl 可用性和宏来源，须结合独立交叉检查；公开路径已核对，impl 实例化和宏映射仍有未决项。`historical_selected` 统计唯一函数定义；`historical_selected_rows_with_duplicates` 另保留历史重复行的权重。两者都不能静默冒充新的最终分母。所有 unresolved 项留在各自分母中。

公开路径交叉检查的 `public_name_body_definitions` 范围为库拥有、可经公开函数路径或公开 receiver/trait 关联、且有函数体的唯一定义。它不是所有可调用 trait 声明/opaque receiver 的完备集合，不能声称解决 impl 适用性；未知 owner/blanket/opaque 项和无函数体声明单列。该范围内无法映射的函数仍留在分母中。

## 重现本次审计

在 GRAFTTool 根目录运行（Python 3.9+，无第三方依赖）：

```bash
python3 -m unittest discover -s scripts -p 'test_coverage_snapshot.py' -v
python3 scripts/check_coverage_fixture.py --toolchain nightly-2026-02-07 --output /tmp/graft-coverage-validation
python3 scripts/audit_experiments.py --workspace /home/cym/workspace --output /tmp/graft-week1-audit
```

独立检查公开路径（在本工作区可用的依赖布局下）：

```bash
cargo build --offline --locked --bin audit_public_surface
python3 scripts/check_public_surface.py --binary target/debug/audit_public_surface --audit /tmp/graft-week1-audit --output /tmp/graft-public-surface --normalize-doctest-attributes
```

bytes、chrono、time 的原文档各含缺少结尾 `]` 的 `doc(test(attr(deny/allow(...))))` 属性，adapter 会拒绝解析。上面的显式参数只在单独分析副本中修复严格匹配的 doctest 属性；原始文档和原快照不变。副本哈希、逐条编辑、原始失败 stderr 均保存；它们不是重新生成的原始实验文档。

第二条命令要求该固定工具链和**相同工具链附带的** llvm-tools-preview。审计脚本只读取工作区的历史文件，不启动 LLM、cargo 构建或 fuzzing；其环境记录中的 `rustc -vV` 指当前主机，不代表历史环境。输出目录必须使用新的路径；审计输出可覆盖，但不得指向历史实验目录。

单独冻结和复算一个 crate：

```bash
python3 scripts/coverage_snapshot.py freeze --crate ryu --version 1.0.23 --doc /home/cym/workspace/PLFuzz/output/ryu/doc.json --output /tmp/ryu-api-universe.json
python3 scripts/coverage_snapshot.py measure --universe /tmp/ryu-api-universe.json --coverage /home/cym/workspace/PLFuzz/output/ryu/coverage_detail.json --output /tmp/ryu-coverage-audit.json
```

快照哈希被修改、目标 crate 版本不一致或缺少函数记录时，命令失败；不退回 basename 或正计数行猜测。exact-entry 策略仍可能漏掉宏/不一致位置映射，故输出显式 unresolved 和诊断下界，不声称给出全面准确的新覆盖结果。

原 `graft coverage` 是会重新提取文档的历史测量路径，尚未切换到快照口径，不用于新受控结果；新实验使用上面的离线入口。不要混合两个计分器的输出。

## 构建位置

迁移后的本工作区布局为 `workspace/GRAFT-2.0/GRAFTTool` 与 `workspace/trustfall-rustdoc-adapter`。Cargo.toml 的相对依赖路径已修正为 `../../trustfall-rustdoc-adapter`。若移动仓库，需按该布局提供 56.2.0 的适配器并保存其源码身份；完整独立 artifact 打包在后续复现阶段完成。

## 证据限制

stats 中的 run_time 是当前保留会话的 worker 墙钟时间，不能当作总 CPU 时间，也不能恢复过去所有 resume。目标目录存在不等于编译、运行成功。当前 CSV 目录缺少覆盖导出，RPG 的一份覆盖文件为空，deepSURF 部分统计目录无读取权限；这些情况均保留为未知/不可读，不计成零覆盖。
