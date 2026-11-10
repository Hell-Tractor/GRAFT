# Admission v2：核心预检与 LLM 参数修复

后续 v4 修正已经完成无费用验收，入口 `repair-v4.json`；实现、实际结果及仍未启动的付费验证见 [REPAIR-V4-20261110.md](REPAIR-V4-20261110.md)。保留旧校准未达标的结论，不把人工正例计入模型成功率。

2026-11-10 后续开发已加入公开类型路径、版本化参数提示、可重复对象实参探测和有界替代生产者重规划，入口为 `development-complete.json`。实现、无费用验证和已冻结的 24 目标校准命令见 [DEVELOPMENT-20261110.md](DEVELOPMENT-20261110.md)。下面保留上一版参数修复和历史预检协议记录；其中“未实现重规划”等限制描述的是当时版本。

## 当前实现：LLM 参数修复

`parameter-repair.json` 启用 `llm-parameter-repair` 和 `planned-call-v2`。对可修复的核心预检拒绝，不再直接终止，而是将失败输入、编译诊断、panic 和公开 API 文档交给 LLM。环境故障仍停止付费生成。

LLM 可以调整解码器、范围、借用、局部类型和安全的对象准备。Rust 验证器保留原有 fully-qualified 计划调用及顺序，允许局部变量一致改名，检查直接调用生产者的接收者依赖；禁止删调用、改目标、unsafe、额外项/导入、模型宏和假追踪。计划调用必须保持顶层直接初始化，避免把它们藏进死分支。

调用标记由工具在真实调用返回后插入，记录实际传入的直接标量参数。归一化后的独立包装和覆盖包装都需通过原三输入探测，最后还要通过初始语料运行、覆盖映射与 AFL 编译。只提前返回或将标量改成常量的样例在回归测试中不能通过。对不透明对象的完整输入依赖、全部输入上的前置条件尚未证明，当前结果只是有界运行证据。

fixedbitset 0.5.7 的 grow/len 状态提示按精确版本启用，来自本地冻结源码/公开文档，是显式提示辅助，不应描述为模型独立推断。没有实现通用的自动重规划或完整跨依赖类型解析修正；chrono 校准中的 Duration 注解由 LLM 修正。

同一原始目标最多两次付费调用、合计 ¥0.24。中断后发现同序号请求/响应文件时拒绝覆盖和重发；恢复语义修复必须有前次封存源码和诊断。

### 实际验证

- 初版四目标校准：bytes/0、chrono/0、time/0 通过，fixedbitset/4 失败；6 次调用，usage 峰价估算 ¥0.170694。
- 增加接收者状态提示后的独立 fixedbitset/14 校准：模型生成正确扩容思路，但被初版“变量名必须不变”检查拒绝；2 次调用，¥0.044145。没有重置 fixedbitset/4 的账本重试。
- 修正为允许变量一致改名、保留调用和直接生产者关系后，复用上述 5 个已有候选重新执行完整 admission：4 个通过，fixedbitset/4 仍失败。原 3 个成功候选无退化，追加模型调用与 fuzz 均为 0。
- 历史生成总费用含失败共 ¥0.214839，分摊给新契约下 4 个通过候选约 ¥0.05370975。全部 usage 已知，没有未知费用预留。来自不同提示/契约版本的诊断样本，不是无偏新生成成功率，不能合并进正式论文覆盖结果。
- 验证：89 项 Python 测试、39 项 Rust 集成测试通过。

证据目录：

```text
/home/cym/workspace/graft-parameter-repair-20261110
/home/cym/workspace/graft-parameter-repair-hints-20261110
/home/cym/workspace/graft-parameter-repair-contract-v2-replay-20261110
```

`calibrate_parameter_repair.py` 默认只准备独立清单，`--execute` 才会调用模型；会复制独立二进制和实现快照。`revalidate_parameter_repairs.py` 只复验已有响应，禁止模型请求。两者使用 admission-only 引擎，均不启动 AFL。

新全量批次可用以下配置接入，不能直接恢复到旧批次：

```text
--admission-profile experiments/admission-v2/parameter-repair.json
--planner-bin <单独构建且冻结的 graft-plan>
--harness-bin <单独构建且冻结的 graft-harness>
```

本次没有恢复旧批次或启动新全量批次。

## 历史第一阶段：仅诊断和预检

以下描述的是 `stage-ab.json` 的历史行为。该模式只做诊断和提前拒绝；它与上面的 LLM 参数修复模式分别冻结，不能混用。

## 已实现

- 新配置 `stage-ab.json` 显式启用核心预检，保留原严格三输入门槛。旧批次默认不启用，加载新配置会因冻结协议不符而拒绝。
- 计划核心由确定性发射器生成，按共同编译环境编译，运行原有三输入探测。失败在任何付费请求之前结束；固定目标槽与 no-repair 配对槽保留。环境错误与计划拒绝分开记录。
- 60 秒预检预算包括等待编译槽的时间。只有产物 hash 全部一致才能复用缓存，环境失败不缓存为计划失败。
- 每次验证前归档旧工作日志，之后把本次源码 hash、编译/探测日志、JSON 证据及原因链封存在 `validation-attempts/<index>/`；不依靠文件时间戳区分尝试。
- 修复提示携带具体 AST 拒绝、编译错误、失败字节输入、panic/分配错误；文本采用现有费用压缩器可以保留的 error/note 格式。
- 报表区分已调度任务、模型尝试、核心预检拒绝、固定槽位通过率；修复上限从实际配置记录，不再固定写成三次。
- 新协议记录实现和二进制 hash。论文输出放入 `admission-v2/<batch-id>/`，编译期间锁定共同 include，完成后恢复原稿所选的数据。

## 无费用验证

以下命令只读旧批次，输出目录必须位于旧批次之外。回放输出目录必须尚不存在，避免覆盖证据。

```bash
cd /home/cym/workspace/GRAFT-2.0/GRAFTTool
python3 scripts/admission_diagnostics.py \
  --batch /home/cym/workspace/graft-cost-controlled-20261110 \
  --output /home/cym/workspace/graft-admission-v2-20261110/legacy-audit.json
python3 scripts/replay_admission_preflight.py \
  --audit /home/cym/workspace/graft-admission-v2-20261110/legacy-audit.json \
  --output /home/cym/workspace/graft-admission-v2-20261110/new-replay \
  --planner /tmp/graft-week1-target/debug/graft-plan --workers 4
python3 -m unittest discover -s scripts
```

本次实际验证使用 `preflight-replay-verified` 目录：137 个原目标，62 个核心通过预检、75 个核心被拒绝，无基础设施失败。原来已接收的 54 个目标全部通过预检；这仅说明没有在预检阶段误拒它们，不是完整 admission/fuzz 再运行成功。

75 个原失败目标历史上共进行了 150 次模型调用，usage 峰价估算合计 ¥4.0872156。预检可以在对应任务付费前发现核心问题；这是回放可避免费用估计，不是实际退款或本批次已经实现的节省。

另外 8 个原失败目标核心预检通过，仍需进一步分析生成/包装/AST 差异。历史日志已被旧程序覆盖的地方，审计明确标记 `latest_files_only`，不能还原成完整的两次尝试历史。

## 后续新批次的接入

当前暂停批次保留，不自动恢复、不把这次回放算入正式覆盖数据。后续完成语义与类型修正后，准备独立新批次，再向 `launch_controlled_round.py` 传入：

```text
--admission-profile experiments/admission-v2/stage-ab.json
```

如需使用新 Rust 实现，使用独立构建目录并传入 `--planner-bin` 和 `--harness-bin`；不要覆盖旧批次仍会调用的二进制。新的模型配置继续沿用费用受控策略，每个原始目标的生成及修复合计最多 ¥0.24。

设计文件：`/home/cym/workspace/GRAFT-2.0/GRAFTPaper/revisions/admission-improvement-plan-20261110.md`。
