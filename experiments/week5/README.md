# 5 分钟 × 6 轮：调用说明

参数已固定：13 个共同版本库、11 个配置、每配置每库 20 个目标槽位，每目标 **300 秒**，**6 轮**。使用最多 56 个逻辑 worker，所有配置统一单 worker；保留每轮的独立生成记录和 AFL 种子。用户已同意超过原先的 24 小时，并明确改用 DeepSeek 官方服务及授权所需生成/修复内容出站。

**当前交付是调度、回放、统计和论文更新框架，完整 11 配置的目标自动准备尚未全部实现。** 现有正式任务清单仍为 pending。因此 `run` 目前会退出 3、报告未就绪，不会启动实验；`prepare` 只做基线编译预检，`calibrate` 只做一个真实模型校准，两者都不会自动填满正式任务清单。这一限制不能用空轮次、旧版本目标或手工修改状态代替。

## 直接调用

任何目录均可调用，不需要导出密钥：

```bash
/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_5min_6rounds.sh status
/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_5min_6rounds.sh check
/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_5min_6rounds.sh budget
```

`status` 显示完整批次是否就绪；`check` 未就绪时退出 3；`budget` 仅重算时间，不启动模型或 fuzzing。

需要重新做准备检查、单目标模型校准时，由你调用：

```bash
/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_5min_6rounds.sh prepare --jobs 8
/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_5min_6rounds.sh calibrate
```

`prepare` 在新目录重新编译已保存基线 replay、导入 13 份共同版本文档，不执行这些 replay。`calibrate` 会访问 DeepSeek、有模型调用费用；只处理保存的 base64 计划，最多初始请求加 3 次修复，总超时 300 秒，每次创建独立记录。凭据从 `~/.config/go-exit/deepseek.env` 的 `DEEPSEEK_API_KEY` 字面赋值读取，仅在生成子进程中映射为 `GRAFT_API_KEY`；脚本不会执行该文件，也不会复制/输出密钥。模型固定为已通过官方 `/models` 查询确认存在的 `deepseek-v4-pro`，配置放在批次独立目录，原有 Qwen 配置不变。

适配器和所有正式清单准备完成、`check` 通过后，运行或恢复：

```bash
/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_5min_6rounds.sh run
```

建议在自己的长时间终端会话中调用。`run` 本身不生成新目标，也不读取模型凭据；它执行已冻结的六轮目标。当前没有后台实验服务在等待自动启动。

通用参数放在子命令后：`--batch /绝对路径`、`--protocol /绝对路径`、`--paper /绝对路径`、`--port /绝对路径`。默认值分别为：

| 项目 | 路径 |
|---|---|
| 批次目录 | `/home/cym/workspace/graft-experiments-5min-6rounds-20261109` |
| 协议 | `/home/cym/workspace/GRAFT-2.0/GRAFTTool/experiments/week5/protocol.json` |
| 论文 | `/home/cym/workspace/GRAFT-2.0/GRAFTPaper` |
| 共同源代码/编译缓存 | `/home/cym/workspace/graft-baseline-port-20261109` |

## 每轮输出及恢复

顺序固定为：本轮目标运行 → 完整 corpus 回放 → 核验 LLVM API 入口 → 本轮和累计统计 → 更新论文 include → 成功生成本轮 PDF → 下一轮。所有目标使用新 AFL 输出目录；AFL 种子由根种子、轮号、库和槽位确定，不同方法采用配对种子。调度顺序确定性打散，混排方法和库。无修复配置须共享同轮 Full 的首次响应，不能重新请求另一份“首次生成”。

每轮的 JSON 与日志保存在 `批次目录/round-01/` 到 `round-06/`。论文更新后生成：

- `GRAFTPaper/revisions/short-budget/round-01/summary.json`：本轮统计。
- `GRAFTPaper/revisions/short-budget/round-01/statistics.json`：截至本轮的均值、样本标准差及失败计数。
- `GRAFTPaper/revisions/short-budget/round-01/coverage.csv`：逐库、逐方法、逐轮数据。
- `GRAFTPaper/chapters/generated/short-budget.tex`：最新数据 include。
- `GRAFTPaper/out/short-budget/round-01/main.pdf`：本轮论文 PDF。
- `GRAFTPaper/revisions/short-budget/latest.json`：最新成功发布的轮次和文件路径。

方法名中的 `GRAFT` 对应 Full；其他 10 个配置见协议 `methods`。同一库同一轮对目标的 API 集合取并集，跨轮计算均值和样本标准差，不跨轮取覆盖并集冒充均值。第 1 轮没有样本标准差，以 `--` 显示。宏平均等权平均所有 13 个库；覆盖映射不确定的定义仍在分母中。保存的 crash/hang 只是原始案例数，不称为漏洞数。

真实生成超时、编译失败、无目标和已证明无效的 harness 保留在固定槽位中；缺测、基础设施故障和未审查的 harness 不会被记为零覆盖或自动丢弃。未准备好的任务阻止启动。AFL 必须实际执行满预算，提供对应 worker 的统计并成功回放，才有 `measured` 结果。

再次调用同一个 `run` 可恢复；已完成且任务哈希一致的结果会复用。仅有 started 记录的被中断任务会标为 interrupted，不会悄悄重新赠送 300 秒。论文编译失败会恢复上一个 include，并阻止进入下一轮；修复后再次调用会重试发布，无需重跑已保存的 fuzzing。不要在同一批次中更换协议、目标源码、锁文件或分母，需另建批次。

## 仍需补齐的执行适配

| 部分 | 当前情况 |
|---|---|
| Full / 随机目标 | checked 单目标规划、共享锁编译、AST 保留、实际 wrapper 探针已可用；整批选择策略迁移未完成 |
| 确定性 emitter | 一个新快照 base64 目标已跑通 AFL → replay → LLVM；整批封装和 cfg 证明未完成 |
| LLM-only、随机 API、语法匹配、无跨参数统一 | 新协议对应的批量生成入口/消融实现未完成；不能把历史启发式模式当作这些新配置 |
| 无修复 | 校准可设 repair=0；与 Full 同一首次响应的批次共享适配待实现 |
| RPG / RULF | 已有共同版本生成兼容预检；正式六轮生成、unsafe 合约分类、AFL 封装、cfg/输入到达证明待补齐 |
| deepSURF | 共同版本库可编译；整批生成、共同模型与统一 AFL worker 导出适配待补齐 |

正式任务清单格式为 `round-NN/tasks.json`，每轮必须恰好覆盖 11 × 13 × 20 个槽位，并引用协议文件 SHA256。`ready` 任务必须保存源文件/二进制/构建证据/有效性证据/初始 corpus 清单的绝对路径与 SHA256，并有真实通过的合约、锁/features、instrumentation cfg 和 wrapper 探针记录。运行命令是参数数组；支持 `{run_dir}`、`{seconds}`、`{seed}`、`{coverage_export}` 替换，不解释 shell。`replay_command` 必须导出 LLVM JSON，执行器自行按冻结分母重算入口覆盖，不能填入自报覆盖率。具体字段由 `scripts/run_experiment_rounds.py:check_tasks` 校验。

这一版本没有“自动补齐上述所有适配器”的命令。后续补齐之前，`run` 被拦截是预期行为。

2026-11-10 按用户要求单独完成了一个已验证 base64 目标的 300 秒执行/回放检查，记录见 `experiments/week6-first-round/report.md`。它没有填充上述正式清单，也不是完整首轮。复现该检查可调用 `python3 scripts/run_first_round_probe.py --output 新的绝对输出目录`；该命令不访问模型服务。

## 时间和已完成验证

完整计划为 17,160 次目标运行；56 个统一单 worker 槽位下，纯 fuzzing 算术下限 25.54 小时，逐轮屏障后的排程为 **26 小时**。生成、编译、回放、PDF 更新均另计。原来 6 小时前后处理 + 2 小时余量仅是占位敏感性，不能当作实测总工期保证。自然 deepSURF 双 worker 拓扑的纯测试排程是 28 小时，但不能替代本批共同单 worker 的资源匹配证明。

离线预检选取 296 个已保存 replay，现代编译器通过 279 个（RPG 134/138，RULF 145/158）；115 个有 unsafe token，等待分类，关键词筛查不是正确性证明。13 份新快照 catalog 导入成功。一个确定性 base64 目标在 **5 秒执行器诊断**中完成 12,792 次 AFL 执行，回放 3 个唯一输入，确认命中计划的 encoded_len；这不是本批 300 秒正式目标，也没有计入六轮。一个真实 DeepSeek 校准约 101.74 秒、两次请求，最终通过共享锁编译、AST 保留及三输入 wrapper 探针；单个样本不能估计整批成功率或保证模型吞吐量。

## 工具副本与重建

本批持久工具副本在 `批次目录/tools/`，避免依赖 `/tmp` 中的临时可执行文件；`tool-snapshot.json` 保存二进制和源码哈希。如源码后续改变，需重新构建后复制，不能把旧副本当成新版本：

```bash
cd /home/cym/workspace/GRAFT-2.0/GRAFTTool
CARGO_HOME=/tmp/graft-week1-cargo-home CARGO_TARGET_DIR=/tmp/graft-week1-target cargo build --offline --locked --bin graft --bin graft-plan
cp /tmp/graft-week1-target/debug/graft /tmp/graft-week1-target/debug/graft-plan /home/cym/workspace/graft-experiments-5min-6rounds-20261109/tools/
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

临时 Cargo 缓存消失后需先恢复工具构建依赖；不要为了离线通过而更新共同实验库的锁文件。持久工具副本当前可直接使用。
