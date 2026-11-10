# Admission 后续开发与验证（2026-11-10）

## 已实现的流程

入口配置为 `development-complete.json`。旧 `stage-ab.json`、`parameter-repair.json` 保留原协议；当前暂停批次不恢复。类型修正、提示规则和探测发生变化，必须创建独立新批次并冻结二进制。

1. rustdoc 导入同时保存 nominal identity、定义路径、公开可访问路径及外部依赖 metadata 来源。使用公开 re-export，避免把 chrono 的内部 time::Duration 与项目直接依赖 time 混为一类。局部中间调用结果允许编译器推断，输入解码仍有明确类型。没有实现任意类型别名展开或完整 Cargo 多版本身份解析。
2. 按 crate 精确版本给 LLM 提供容量、集合长度、索引、日期和时间提示；总提示上下文不超过 2200 UTF-8 字节。LLM 实际添加参数校验与准备代码。规则是有来源的建议，不是通用前置条件证明，也没有实现累计存活分配量的静态证明。
3. 受约束 AST 保留真实目标调用、顺序和直接生产者关系，允许参数准备、一致变量改名以及 Ok/Some 提取。工具在真实调用前记录 entered、实际标量/受支持对象摘要，返回后记录 returned。对象只观察确定值与有界前缀，未知对象仍明确记为 unknown。
4. 保留原严格三输入门槛，再执行 8 组固定输入各两次，总计 16 次运行、10 秒墙钟预算。相同输入必须有可重复观测，至少两个输入的实际参数不同。参数全部固定或用进程变化冒充输入变化不能通过。真正无参数 API 标记为“不适用”，不冒充输入敏感。额外诊断输入不加入正式 fuzz 种子。
5. 第一次 LLM 响应验证失败后，编译/依赖类型、目标不可达或输入影响问题可尝试替换生产者。原计划加最多两个替代计划；追加搜索共享 2000 节点和 60 秒预算（初始核心预检另有 60 秒上限）。替代计划需先通过核心预检；参数 panic/资源错误优先继续 LLM 参数修复。目标、版本、原槽位与付费账本均保持，最多两次付费调用、合计 ¥0.24。恢复使用封存 lineage；异常/中断不能重置候选或节点预算。
6. 统计区分重规划任务、采用新计划的模型尝试和重规划后接收。no-repair 仍只使用首次响应和初始计划；deterministic-emitter 使用无模型的干净核心，通过相同计划校验。

`development-no-replanning.json` 和 `development-no-guidance.json` 是独立消融配置。前者关闭重规划；后者使用显式空规则文件，避免意外退回旧 fixedbitset 提示。两者保留同样的严格门槛和对象探测。不同配置需要独立批次，不混写结果，也不自动触发额外付费对照。

额外对象/计划证据仅适用于有 SequencePlan 的配置。公共初始语料、编译器、资源限制和最终覆盖流程保持一致；不能宣称无计划的原生基线也获得同样的对象依赖证明。基线原始生成器不被静默重写。

## 已执行的无模型费用验证

| 检查 | 结果 |
|---|---|
| Rust 集成测试 | 46 通过 |
| Python 测试 | 96 通过 |
| 已付费候选完整 admission 回放 | 4/5 通过；原已接收的 3 个无退化 |
| 137 个冻结历史目标的兼容核心预检 | 62 通过、75 拒绝；原已接收 54/54 核心均通过 |
| chrono 固定对象参数反例 | 旧严格门槛通过，新对象检查拒绝 |
| chrono 原目标 878 新目录/类型路径核心编译 | 通过；外部 time 定义仍保持原 identity，经 chrono::Duration 公开路径表达 |
| 新模型调用 / fuzz 作业 | 0 / 0 |

4/5 是来自不同提示/契约版本的选定响应回放，不是新生成成功率。54/54 是核心预检兼容结果，不代表重新执行了其完整 admission 或 fuzz。仍失败的 fixedbitset/4 没有获得新的付费重试。

机器可读摘要及证据 hash：`development-verification-20261110.json`。完整运行目录：`/home/cym/workspace/graft-admission-development-20261110`。回放目录保留当时的协议和独立工具；后续开发没有覆盖原付费校准目录或论文正式数据。

## 已准备、尚未执行的 24 目标校准

清单：`/home/cym/workspace/graft-admission-development-20261110/pilot-24-ready/calibration-manifest.json`。

bytes、chrono、time、fixedbitset、csv、bumpalo 各 4 个原槽位，合计 5 个原成功控制、19 个原失败控制；包括较长调用链。选择在调用前冻结，不能事后替换。旧 `pilot-24-prepared` 是开发期间的准备快照，执行应使用最终 `pilot-24-ready`。

清单冻结目标、旧计划、旧生成记录、新配置、关键实现、规则、费用策略和工具 hash。执行时任何差异都在模型调用前拒绝；成功目标需显式作为控制样例纳入。最高 48 次调用，费用预留上限 ¥5.76，未知费用保留在原账本。本入口 admission-only，不启动 AFL。

```bash
cd /home/cym/workspace/GRAFT-2.0/GRAFTTool
python3 scripts/calibrate_parameter_repair.py \
  --source-batch /home/cym/workspace/graft-cost-controlled-20261110 \
  --output /home/cym/workspace/graft-admission-pilot-24-execution-20261110 \
  --planner /tmp/graft-admission-v4-target/debug/graft-plan \
  --harness /tmp/graft-admission-v4-target/debug/graft-harness \
  --admission-profile experiments/admission-v2/development-complete.json \
  --case-manifest /home/cym/workspace/graft-admission-development-20261110/pilot-24-ready/calibration-manifest.json \
  --include-accepted-controls --execute
```

执行目录必须尚不存在。保留既有密钥读取方式和 DeepSeek 官方、关闭 thinking 的费用配置。这里的运行命令已准备，但本次未执行；新的诊断协议实验与旧付费响应分别统计，不能作为旧账本重置的续跑。

校准暂按 30–60 分钟预留，非服务延迟承诺。结果需先检查目标到达、输入影响、包含失败的每通过目标费用和时间分布，再决定扩大。

## 后续全量时间评估

当前进程实际可见/可用均为 64 个逻辑 CPU，不能直接按宿主机声称的 128 线程调度。示例保留 8 个线程用于编译等，fuzz 用 56 个 worker。按 13 crates × 每配置最多 20 槽位 × 11 配置，并将 deepSURF 主从计为两个 worker，其余配置各一个：每目标 300 秒，一轮纯 fuzz 可行排程约 4 小时 40 分，六轮约 28 小时。生成、编译、admission、每轮统计与论文更新还需另加。

若另一个运行环境确实开放 120 个可用 worker，同一场景约每轮 2 小时 10 分、六轮 13 小时。该差异只是资源场景，不是资源公平性实验结论；实际接收数和统一 worker 分配确定后需重新估算。六次 fuzz 重复复用已接收源码，不重复六次模型生成。用户已允许全配置超过 24 小时，仍需启动前报告估时。

`next-run-time-scenarios.json` 位于完整运行目录。当前没有启动校准、全量 fuzz 或恢复暂停批次。尚未获得新生成总体通过率、24 目标真实费用或新版覆盖结果，不能在正文中填入预期数值。
