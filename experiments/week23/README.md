# 第 2～3 周验收记录

执行报告：[week23-report.md](../../../GRAFTPaper/revisions/week23-report.md)。使用说明：[checked-planning.md](../checked-planning.md)。

- `all-tests.log`：最终 31 项 Rust 回归；`python-tests.log`：23 项已有审计回归；`cargo-check.log`：全 target 检查。
- `witnesses/`：26 个 compiler 程序、正/负预期与诊断、运行探针、mock 请求和响应。这里包括能编译但保留/运行无效的反例，不能把 compiler 通过数作为有效目标数。
- `base64-plan.json`、`base64-target/`、`base64-probe.json`：固定 base64 0.22.1 的真实库 standalone smoke。
- `support-13/`：固定 13 个历史文档的唯一选中定义支持统计，非公开全集覆盖率；1,723 个定义中 770 个无导入阻碍，不代表后续可构造或编译。
- `baselines/compile-smoke/`：最终隔离 Docker 生成与原样回放检查。RPG 通过；RULF 生成通过、原样编译失败。`rulf-path-separator.patch` 是待重建的源码提案。
- `baselines/isolated-smoke/`、`verified-smoke/`、`final-smoke/`：先前权限/noexec 定位记录，最终结论采用 `compile-smoke/`。
- `toolchains.json` 与 `validation.json`：环境与验收身份；无真实模型或正式 AFL 新数据。

历史 doc/coverage/corpus 和停止的历史容器均保持不变。运行时标量日志只作为有限输入见证，opaque 参数输入影响保留 Unknown。
