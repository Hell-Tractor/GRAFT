# 第一轮执行验证与论文修改记录（2026-11-10）

**完整 11 配置首轮尚未启动/完成。** 生成适配缺口仍阻塞 2,860 个正式槽位；本次运行的是已通过既有验证的一个 base64 目标，作为 300 秒执行验证。没有把它记作 Full、外部基线或消融配置的完整首轮，也没有启动第二轮。

| 项目 | 实测结果 |
|---|---:|
| 库版本 | base64 0.22.1 |
| 目标 | base64::encoded_len |
| AFL worker | 1 |
| AFL 实际时长 | 300 秒 |
| 执行次数 | 956,031 |
| 原始保存 crash / hang | 0 / 0 |
| 唯一回放输入 | 3 |
| 已确认公开有 body 定义入口 | 1/76（1.32%） |
| 映射未解决项 | 23（保留在分母中） |

该覆盖仅反映单一目标的入口验证，不是整库效果比较。AFL 构建使用既有 nightly 1.97 dev 二进制，回放为 nightly 1.95 instrumentation 构建，尚未满足正式统一 cfg/编译器与 sanitizer 配置；不能用于方法排名或漏洞效果结论。0 crash/hang 也不是不存在缺陷的证据。

## 结果证据

原始日志、最终 AFL statistics、队列与 LLVM 导出都保留在 `/home/cym/workspace/graft-experiments-5min-6rounds-20261109/first-round-execution-probe-20261110`。本目录保存 `result.json`、`summary.csv`、`coverage-audit.json` 和 `formal-round-status.json`；结果包含源码、二进制、锁文件和分母 SHA256，以及执行/回放命令、随机种子和时间戳。

可复现入口：`scripts/run_first_round_probe.py --output 新的绝对输出目录`。该命令只运行执行验证，不能替代被阻塞的全矩阵。

## 等待期间完成的论文修改

1. 新增 `chapters/measurement_protocol.tex`：固定公开定义分母、精确入口证据、同轮目标取并集/跨轮取均值与样本标准差、宏/微平均区别，区分真实生成失败与基础设施缺测。
2. 明确 LLM 的当前职责：不可变 core 不允许新增计划调用或修复被规划器拒绝的类型约束；LLM 价值仍需确定性 emitter 对照，不能由校准成功推导。
3. 修正历史因果解释：图边减少不等于验证后的泛型依赖减少，编译失败不自动归因于 trait；补上 byteorder 主表 36/58、52 个目标与消融 14/58、20 槽位的差异，两处均称一小时，因此不以缩短 fuzz 时长解释。
4. 重写模型随机性、API 映射与验证门槛的有效性威胁，明确模型别名、六个短重复、三输入探针和 AST 保留的限制。
5. 历史 RQ 标题及文字改为历史记录；修正 related work 对新版 checked 路径的描述，并使用可核验的章节引用。
6. 本次 300 秒验证数据写入独立的 `chapters/generated/first-round-execution-probe.tex`，不填充新效果表或覆盖历史表。

论文 PDF：`/home/cym/workspace/GRAFT-2.0/GRAFTPaper/out/week6-first-round/main.pdf`。完整首轮的后续工作仍是补齐适配和真实构建证据，不会把 pending 槽位改成虚假零覆盖。
