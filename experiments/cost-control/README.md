# GRAFT 生成费用控制方案（2026-11-10）

目标：从新策略启用起，将每个目标的生成与修复费用控制在 ¥0.25 以下，同时统计总生成费用除以实际通过目标数，避免靠失败、空响应或免费基线稀释平均费用。

## 成本原因与修改

核对保存的响应、并按 provider response ID 去重后，历史 GRAFT 56 次响应按当前峰价估算平均约 ¥0.417/次；其中可核对的输出 token 有 96.88% 用于思考。其他受控 LLM 配置平均约 ¥0.42—0.47/响应。费用审计包含重试归档，不将重复保存的同一响应再次计费。输入缓存字段缺失时按未命中估算，历史估算不是账户账单。

因此主要降低思考输出和重复上下文，而不直接把输出上限压低后继续让思考耗尽预算。

| 项目 | 新策略 |
|---|---|
| 模型 | 仍统一使用 DeepSeek 官方 `deepseek-v4-pro` |
| 思考 | 显式 `thinking.type=disabled`；不发送 `reasoning_effort=high` |
| 输出 | 每次最多 3072 token；剩余预算不足时动态降低上限，低于最低完整响应预算则拒绝发起 |
| 修复 | 最多初次生成＋1 次修复，合计最多 2 次付费调用 |
| 单目标预算 | 按峰价保守估算，生成与修复合计最多预留 ¥0.24，留出 ¥0.01 余量 |
| 输入 | 最多 14000 UTF-8 字节；保留约束和源码，选择相关的完整文档/分析记录，不随意截断源码 |
| GRAFT 上下文 | 保留完整 immutable core，去掉与其重复的 pretty-printed plan；相关文档最多 2600 字节 |
| 修复上下文 | 只传初始任务、最新源码及最新错误，不累积所有旧版本；优先提取 rustc 错误码、错误内容和建议，省去巨大的 JSON 源码片段与警告 |
| deepSURF | 保留原生 system 约束、sample harness 和原生源码组装；只压缩补充分析和文档；预算不足时进一步减少这些补充记录 |
| deepSURF 整 crate | 最多 20 个付费 LLM 目标，总预算 ¥4.80；原生内部候选数不等于可无上限付费的目标数 |
| 无 LLM 基线 | RPG/RULF 模型费用为零，继续保留其原生生成耗时；不拿其零费用稀释 LLM 成功用例费用 |

GRAFT、LLM-only、随机 API、各消融及 deepSURF 使用同一模型、思考开关、单请求输出上限及费用规则。no-repair 仍共享 GRAFT 的初次响应，不重复付费或重复计算实际调用。

预算使用 2026-11-10 [DeepSeek 官方人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)的峰价：每百万 token，缓存命中输入 ¥0.30、未命中输入 ¥9、输出 ¥27。实际在空闲时段的计价更低，本方案不依赖等待空闲时段才能达标。[官方支持显式关闭思考](https://api-docs.deepseek.com/guides/thinking_mode/)。价格变化或换模型时必须重新核对费率。

## 预算如何执行

- 请求前依据文本 UTF-8 字节上界、消息开销余量和最大输出，预留最坏费用；收到 usage 后按输入缓存命中/未命中及输出结算。使用字节上界是保守预算估计，最终仍需核对 usage 和账户账单。
- 每个目标、每个 crate 都保存独立持久化账本，并加文件锁处理并发；总额、调用次数与目标数均在发起请求前检查。
- 超时、缺失 usage 等无法确认是否扣费的调用保留全额预留，不能当作免费失败继续重试。
- 目标目录被移入充值重试归档后，仍会从 crate 账本恢复该目标已用预算，不能通过重启或归档重置费用。
- 本地预算拒绝不会发起 API 请求，不会标成“算法生成零覆盖”。它有独立预算拒绝原因，并保持基础设施/实验范围缺口可见。
- 空响应、截断响应和服务异常不自动重复付费。native deepSURF 也通过同一个预算客户端，不会绕过开关继续使用 SDK 的默认高强度思考。
- 记录按 usage 估算的费用及未知费用预留；不得把“预留”说成已对账的实际扣款。若报告 usage 超出预留，停止后续调用并核对计费。

## 实际校准结果

独立校准覆盖 6 个目标，合计 7 次 API 调用，全部费用按官方峰价估算，包含失败目标和一次修复：

| 目标 | 生成＋修复费用 | 编译及有限输入运行验证 |
|---|---:|---|
| GRAFT / base64 / 0 | ¥0.021978 | 通过 |
| GRAFT / base64 / 1 | ¥0.021618 | 通过 |
| GRAFT / bumpalo / 0 | ¥0.021492 | 通过 |
| LLM-only / ryu | ¥0.014211 | 通过 |
| deepSURF / byteorder | ¥0.061533 | 通过 |
| deepSURF / bytes | ¥0.141327 | 修复一次后仍未通过编译，费用保留 |

合计 ¥0.282159，平均 **¥0.0470265/尝试用例**；将失败费用全部分摊给 5 个通过用例，为 **¥0.0564318/通过用例**。每个目标本身也均低于 ¥0.25。

这是有目的选取的诊断样本，不是无偏的全量成功率估计；不能由此保证所有 crate 的质量不下降。首次两个 native 请求在本地因上下文限制被拒绝，没有付费；修正为优先减少补充记录后，仅重新校准这两个目标。bytes 修复复用同一账本，不重发初次生成。没有运行 AFL，也没有把这些数据填入正式覆盖结果。

校准汇总：[calibration-summary.json](/home/cym/workspace/GRAFT-2.0/GRAFTTool/experiments/cost-control/calibration-summary.json)。原始请求、usage、源码、账本和编译检查在 `/home/cym/workspace/graft-cost-calibration-20261110` 与 `/home/cym/workspace/graft-cost-calibration-native-20261110`。

## 接续执行

**旧批次已有实际结果，不能改其冻结协议并混入关闭思考的新数据。** 旧记录保留在 `/home/cym/workspace/graft-full-round1-20261110`。新方案是单独的费用受控变体，后续比较或写论文时需报告预算、思考模式、上下文选择及成功率变化。

已准备新目录 `/home/cym/workspace/graft-cost-controlled-20261110`，借用经过验证的运行依赖，但没有复制旧响应、目标或覆盖结果，也没有启动新全量实验。其 `control.json` 保留原运行安排；没有重新打开后续轮次的付费生成。若后续轮次复用第一轮目标，只做新的 fuzz seed 重复，无需再次调用模型；不能把它描述为多轮独立生成。

后续执行者使用：

```bash
cd /home/cym/workspace/GRAFT-2.0/GRAFTTool
flock -n /home/cym/workspace/graft-cost-controlled-20261110/.run.lock \
  python3 -u scripts/launch_controlled_round.py \
  --root /home/cym/workspace/graft-cost-controlled-20261110 \
  --cost-policy /home/cym/workspace/GRAFT-2.0/GRAFTTool/experiments/cost-control/policy.json \
  > /home/cym/workspace/graft-cost-controlled-20261110/launcher.log 2>&1
```

新调度入口默认使用降费策略。直接对旧目录 `--resume` 会因冻结协议不一致而拒绝，且会在启动付费 native 子进程前拒绝。`--legacy-model-budget` 仅用于显式复现旧高费用配置，不应在本次降费执行中使用。旧 `experiment_batch.py` / `model_preflight.py` 是历史入口，不应用来代替本策略。

运行中审计（不发起模型请求）：

```bash
python3 /home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/audit_model_costs.py \
  --root /home/cym/workspace/graft-cost-controlled-20261110 \
  --output /home/cym/workspace/graft-cost-controlled-20261110/cost-report.json
```

除每调用费用外，必须报告各配置、crate 的总费用、已付费/预留调用数、通过目标数、总费用除以通过目标数、预算拒绝数及 usage 未确认数。通过目标为零时该单位费用不可用，不能填零。若实际全量质量使费用/通过目标超过 ¥0.25，应停止扩大生成、调整提示或报告局限，不能隐藏失败费用。

主要文件：`scripts/model_cost_control.py`（预算及上下文）、`scripts/costed_model_request.py`（共享请求客户端）、`scripts/audit_model_costs.py`（对账）、`scripts/test_model_cost_control.py`（计价、重试和拒绝检查）。native 改动保存在 `deepsurf-budget.patch` 与 `deepsurf-budget-extensions.patch`，独立生成器已离线构建通过；没有修改原始 deepSURF 项目。
