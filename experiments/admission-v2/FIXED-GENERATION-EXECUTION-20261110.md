# 正式实验执行计划：admission v5，一次生成、六轮 fuzz

> **2026-11-12 事故后更新：下文启动、恢复命令已停用，仅保留历史。第一轮已部分运行并因服务器重启中断，禁止直接执行旧 --execute/--resume。改用 [第一轮故障恢复计划 v1](/home/cym/workspace/GRAFT-2.0/GRAFTPaper/revisions/round1-recovery-plan-v1-20261112.md)，先实现并验证资源保护，再恢复未完成任务；本次只做第一轮，不自动第二轮。**

状态：复用调度和统计发布已实现（工具 master `868e56d`、`1e81ca8`），106 项相关测试通过。admission 算法仍为 `0fde979`；v5 最终校准 23/24、控制 5/5 的原结论不变，没有重跑该付费队列。

新全量批次已经准备并通过实际 check-only：

`/home/cym/workspace/graft-admission-full-fixedgen-20261110`

**当前没有正式生成任务或正式 fuzz 启动。** check-only 的 checked_preparation_only 表示输入、实现和协议已冻结，不表示 2860 个正式目标已生成或已接收。

## 执行方式

以下命令可以从任意目录执行；需要本机 Docker 权限及已授权的 DeepSeek 网络访问。凭据继续由既有读取器读取指定文件，不放进命令行或日志。

先检查准备状态，零模型请求：

```bash
/usr/bin/python3 /home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_fixed_generation.py   --batch /home/cym/workspace/graft-admission-full-fixedgen-20261110   --check-only --through-round 6
```

先执行完整第一轮，完成统计和论文 PDF 后停止：

```bash
/usr/bin/python3 /home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_fixed_generation.py   --batch /home/cym/workspace/graft-admission-full-fixedgen-20261110   --execute --through-round 1
```

第一轮完成后，继续复用至第六轮；已经发布的轮次不重跑：

```bash
/usr/bin/python3 /home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_fixed_generation.py   --batch /home/cym/workspace/graft-admission-full-fixedgen-20261110   --execute --through-round 6 --resume
```

若从一开始就要自动连续跑完六轮，首条执行命令改为 --through-round 6 即可，仍逐轮经过论文发布屏障。**不要同时启动两个入口，不要运行旧 --auto-second 或旧六轮包装器。**

该目录已准备，不要再次 --prepare 或删除后重建。若执行被中断，在同一目录核对 progress、generation、started、result 与 cost ledger，再使用 --resume。已经开始但未完成的 fuzz 保留 interrupted，不重新分配一个名义完整的 300 秒；已有结果不覆盖，整轮不完整则不进入下一轮。

## 冻结与调度

- 11 配置、13 crates、每配置每 crate 20 槽位，固定分母 2860；所有原失败和 no_target 保留。每个可运行目标每轮 300 秒，一个 AFL worker。
- 第一轮生成、admission 和 fuzz 沿用现有执行器，新增模式不改变提示、合同或门槛。RPG/RULF 使用已准备的原生库存，原批次生成耗时明确标为 reused_prepared_inventory；deepSURF 只在第一轮运行一次原生生成。
- 第一轮完成后写入 generation-seal.json，封存 tasks、生成/admission 统计、费用账本、源码、AFL/coverage 二进制、初始语料及其证据。后五轮只调用共同 fuzz/覆盖测量组件，不调用模型、原生生成器或 admission。
- 同一 crate/slot 的 AFL seed 在方法间配对，轮间改变；所有六轮初始语料相同，各轮输出独立，不导入上一轮 AFL 队列。源码、二进制、语料、版本、共同 lock、工具和任务变化均阻止继续。
- 账本按请求 ID 去重，目标账本和 crate 镜像不重复收费。生成费用只统计一次；逐方法报告包含失败费用的每接收目标成本。未知费用阻止封存，成本不达标则发布已有结果并停止后续轮。

准备时冻结了容器身份、576 项 RPG/RULF 库存/报告引用、运行工具和 73 项脚本/配置。当前代码或输入漂移会拒绝启动；文档更新不影响冻结脚本。前置配置微调时的零调用准备快照已另存 graft-fixedgen-preparation-superseded-20261110，没有付费数据被清空。

## 逐轮统计、论文与控制

每轮保存 tasks.json、execution-order.json、result.json、summary.json；完成覆盖回放和论文构建后保存 publication-barrier.json。构建失败、测量缺失、hash 漂移或暂停均阻止下一轮。首轮不完整时也导出 unavailable 测量，不伪造零覆盖。

论文统计记录 independent_generation_rounds=1，已完整执行的 AFL seeds 从 1 到 6；generation_by_round 只含第一轮。API 覆盖按各轮完整测量求均值和样本标准差，不把六轮覆盖取并集，不把同批源码称为六次独立生成。

输出使用协议 publication_namespace 指定的独立目录，位于 GRAFTPaper/revisions 和 out 下；每轮有统计 CSV/JSON、results.tex、构建日志和 PDF。旧稿选中的数据保留，正式新批次产物独立保存。

查看 fixed-generation-progress.json 判断后续轮次/发布状态；首轮生成过程另见 progress.json、round-01/all-method-generation.json 及各目标 generation.json。control.json 在轮间读取：

```json
{"pause": true, "stop_after_round": 6, "auto_second": false}
```

pause=true 表示当前轮完成后暂停；恢复时改为 false，并使用同一 --execute --through-round 6 --resume。stop_after_round 可设 1–6。不要把 auto_second 打开。

## 验证与预算

106 项相关 Python 测试通过，新增调度测试覆盖零生成/模型调用、失败分母、成对种子、费用去重、成本超限、未知启动状态、暂停、首轮恢复、已完成/中断结果保留及论文失败屏障。还使用真实 admission 检查了任务序列化后的 replay 证据字段，fuzz 派发在该检查中被模拟。

最终版本另使用一个已有接收目标实际执行六次 **1 秒开发 fuzz**、覆盖回放和六次真实论文构建，六个 seed 不同，任务 hash 相同；完成后恢复没有重跑。新增模型调用为 0，约 28 秒完成。它们属于开发验证，不计入正式 300 秒结果，也不是新生成成功率。

正式配置使用 48 个 fuzz 并发、8 个编译槽，native deepSURF 分析固定使用 CPU 48–55；当前可见 64 个逻辑 CPU，已检查该分配可用。按最多 2860 个可运行目标估算，纯 fuzz 上限规模排程约 **5 小时/轮、30 小时/六轮**，还需生成、编译、回放与论文时间。用户已允许超过 24 小时；第一轮按实际 admitted 数和回放耗时更新剩余预计时间，不外推校准接收率。

每个付费方法每个原目标最多两次、¥0.24，按七个付费配置计算一次生成的最大预留 ¥436.80；这不是预测账单，不再乘以六。校准的 ¥2.40 总上限仅属于校准，不能当作全量总预算。使用既有冻结价格快照核算，历史校准费用另列保留。

机器验证摘要：GRAFTTool/experiments/admission-v2/fixed-generation-verification-20261110.json。真实开发证据：/home/cym/workspace/graft-fixed-generation-final-verification-20261110。正式批次 preparation-verification.json 保存准备状态与引用。
