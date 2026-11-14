# 第 2～3 周：可验证的类型与值计划

新入口 `graft-plan` 和 `graft checked-testcase` 已实现。旧 `api-sequence` / `testcase` 保留历史启发式及消融行为；不能把它们的输出当作新计划或套用新验证结论。

## 固定输入、生成计划和确定性代码

```bash
cargo build --locked --bin graft-plan --bin graft
target/debug/graft-plan import frozen-doc.json catalog.json --package base64
target/debug/graft-plan inventory catalog.json support.json --edges
target/debug/graft-plan plan catalog.json 395 plan.json --nodes 2000 --depth 12
target/debug/graft-plan emit plan.json checked-target
cd checked-target
cargo generate-lockfile --offline
cargo build --locked --offline
cargo run --locked --offline -- 1 0
```

示例 ID `395` 仅适用于本工作区 `base64 0.22.1` 冻结文档，不可用于其他快照。`import` 支持原始 format-57 rustdoc 或带 `parser.krate` 的历史 APIDoc。必须从包构建配置提供正确 `--package`、重复的 `--features FEATURE` 和必要时的 `--no-default-features`；rustdoc JSON 不能证明构建时实际使用了哪些 features。默认声明是 crate 同名包和 default features。新运行必须核验该声明及 rustdoc 生成命令。

`plan` 成功输出 `planned`，拒绝时仍保存结构化原因并返回状态码 2。搜索默认类型池为 `u8`、`i32`，最多 2,000 个搜索节点、深度 12、每个 API 128 个单态化组合；库接口 `Limits` 可显式设置其他具体类型池。失败是这个有限搜索的结果，不是不可构造性的完整证明。

`emit` 输出独立验证程序、精确版本/feature 清单和计划。它是确定性编译/回放入口，不会自动启动 AFL 或长时间 fuzzing。后续接入 AFL 时须另行验证 wrapper 确实将 fuzz 字节交给 `graft_body`，并保存其编译、运行与工具链记录。

本机离线缓存采用可写临时解包目录与外置构建目录：

```bash
CARGO_HOME=/tmp/graft-week1-cargo-home CARGO_TARGET_DIR=/tmp/graft-week1-target cargo test --all-targets --offline --locked
```

这是本机缓存布局的工作命令，不是 artifact 的通用前提；普通可写 Cargo 缓存无需此设置。

## 实现的约束

- 变量身份含定义、调用实例和参数名；同一调用使用一个共享具体替换，不同调用可独立实例化。统一递归检查名义构造器、全部类型参数、tuple 和定长数组，并执行 occurs check；失败不提交部分替换。
- `Compatible` 返回替换、适配和义务；`Incompatible` 与 `Unknown` 分开。primitive 解码是独立根规则，不是任意源到 primitive 的匹配证明。
- trait 用快照、crate、item 身份区分。正向 impl 证明递归检查条件；缺失/外部 impl 仍为 Unknown，不做封闭世界的否定结论。
- `SequencePlan` 保存值 ID、调用 ID、实际参数来源、类型替换、借用/移动、构造和分支。参数构造期间保留已选参数，避免后续 producer 移走已借用值。共享借用可复用，可变借用冲突和非 Copy 重复移动要构造新值或失败。
- 原始 `Option` / `Result` 返回类型保留，`Some` / `Ok` 提取作为显式步骤；失败只结束当前验证输入。Vec、Box、tuple、短定长数组、Option 和 slice 背板有显式构造，不能靠剥掉 wrapper 判断等价。
- 所有依赖先于调用写入计划。无根环、无 producer、预算耗尽、未知约束均有原因；不会略过缺失参数。

## LLM 生成及修复

```bash
target/debug/graft checked-testcase plan.json new-target --max-repairs 3
target/debug/graft-plan validate plan.json new-target/src/main.rs
target/debug/graft-plan probe plan.json /absolute/path/to/checked_target runtime.json
```

`checked-testcase` 使用现有 LLM 配置，运行该命令会调用服务；本轮验收只运行离线 mock，没有付费模型请求。新语义是 **1 次初始请求 + 最多 3 次修复**，与旧 `fix_retries` 的总尝试语义分离。输出目录必须全新，以保留完整记录；依赖先离线精确解析，后续检查 manifest/lock 不变并用 `--locked --offline` 编译。

每次请求都给出完整计划和不可修改的确定性核心。Rust AST 比较允许注释/格式变化，并允许一个 main wrapper，但拒绝核心中的删除、常量替代、类型参数/绑定/顺序/分支变化，以及可改变名字解析的其他顶层定义。它是保守的保留检查；尚不接受任意语义等价重写。`validate` 只检查 AST。`checked-testcase` 编译后还对实际 standalone wrapper 执行空、0、1 三组输入探针，逐输入超时 3 秒；`probe` 可独立复现并返回状态码 3 表示未通过。运行标记由不可修改核心在调用完成后发出，可拒绝测试输入上跳过核心或向核心传入常量的 wrapper；opaque 参数的输入影响仍为 Unknown。编译、保留和运行结果分别保存。

修复显式包括原始约束、当前源码、诊断和剩余修复次数。每个目标的 `transcript/` 保存独立 prompt、响应、源码、耗时、编译和保留结果及后端失败。服务未提供 tokens、费用或不可变 revision 时记录 null；不估填。旧接口的对话历史继续保留，但新流程不依赖隐式历史来恢复修复上下文。

## 回归与支持统计

```bash
cargo test --all-targets --locked
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/audit_checked_support.py --planner target/debug/graft-plan --source /home/cym/workspace/PLFuzz/output --output support-13
python3 scripts/check_baseline_containers.py --output new-baseline-smoke
```

Rust 回归包括评审的 `combine<T>(Vec<T>, Buffer<T>)`：异型参数被 rustc 拒绝，规划器重规划成一致的 `i32` 实例；还覆盖独立泛型作用域、嵌套容器、条件 impl、同名不同 trait、泛型方法/Self、借用/移动、Option/Result 失败、环、预算和编译通过却删除目标的反例。运行证据只证明测试输入上完成调用及直接标量参数变化，不是完整污点证明。自由 main wrapper 的执行到达不由 AST 检查保证；三输入运行探针只提供有界见证。

`support-13/` 统计固定 13 个历史文档的**唯一选中定义**；它不是第一周新公开定义分母，也不是原表的重复 API 行。候选参数对分为冲突、Unknown、待 trait 义务及未编译验证的统一候选。没有导入阻碍不等于可构造/可编译/可执行，不作为覆盖率。

## 边界与后续工作

关联类型/投影、HRTB、符号 const、复杂 lifetime、带 lifetime 的名义类型、借用返回、trait 方法派发、类型别名展开、raw pointer 和没有显式合约的 unsafe 调用均拒绝为 Unknown。公开路径遍历也是受限子集；rustc 仍是最终检查。尚未集成旧 usage/depth 策略到新计划搜索，未自动执行 Unknown 的启发式 LLM fallback，也未开展正式多次 LLM/AFL 对比。

RPG 与 RULF 的容器/镜像均找到。`baselines/compile-smoke/` 的原样 RPG 生成、编译和回放通过；RULF 生成成功，但原样输出丢失 `::`，编译失败。已准备生成器源码修补提案 `baselines/rulf-path-separator.patch`；原目录和历史容器保持不变，补丁尚未重建验证，因此 RULF 仍不能当作已通过端到端验收的基线。所有容器测试均禁用网络，只读工具/源文件，写入新的微型实验目录。
