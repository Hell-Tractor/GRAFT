# 基线修复、共同版本与 24 小时预算

完整报告：[week4-baseline-and-budget-report.md](../../../GRAFTPaper/revisions/week4-baseline-and-budget-report.md)。

- 统一版本权威记录：`common-versions.json`；13 库、四编译器联合编译全部通过。
- `shared-project/` 固定同一个 Cargo.lock；`common-universes-v2/` 冻结 13 个新 API 计分集合（2,434 个公开有 body 定义；待核项另计）。
- `generator-preflight-v2/` 是限时生成与首两个原样 replay 编译的排障证据，不是总体编译率或覆盖率。RPG 20/22、RULF 21/24 包含编译后仍无效的 harness。
- `harness-validity-screen.json` 记录 RPG ryu 的实际无效输出指针，未执行。
- `rulf-container-patched-smoke/` 和 nested smoke 均通过原样生成、编译、回放；修补版和历史二进制分离。
- 原 `rulf-patched-smoke/` 与 nested 是宿主机构建 libc 不兼容的诊断，最终使用容器内构建版。
- `budget-first-report.md` 和 `experiment-time-budget.json`：按完整矩阵，30～60 分钟/目标远超 24 小时；阶段预留和模型耗时仍需校准，未承诺完成时间。
- 正式重跑尚未启动、模型请求 0；资源公平性、target 合约核验、统一时长和 seed 分层在正式 manifest 前固定。

大体积源码缓存/vendor、二进制与原始 rustdoc 存于 `/home/cym/workspace/graft-baseline-port-20261109/`。历史输出、原 RULF/RPG 源码与停止的历史容器保持不变。

第一轮保存在 `common-versions-round1.json`、`shared-project-round1/`、`common-universes/`、`generator-preflight/`；正式使用第二轮的 canonical common-versions/shared-project 和带 v2 的分母/生成结果。第二轮统一降为 chrono 0.4.19、regex 1.6.0、time 0.3.9，regex 的生成超时已解决，chrono/time 仍有预算内失败。
