# Admission v2：第一阶段实现

本阶段完成失败证据归档、修复反馈、付费前核心预检，以及新协议/论文结果隔离。尚未实现容量/索引/日期语义规则、依赖类型路径修正、本地重规划和模型包装片段接口；不要将此阶段称为完整的通过率优化。

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
