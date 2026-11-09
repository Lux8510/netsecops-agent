# v0.6 演示版验证记录（2026-10-27）

这是新展示层与可发布样本的验证，不覆盖 v0.1–v0.5 的冻结清单、真实模型轨迹或失败记录。本轮**没有**调用 DeepSeek、运行 Docker/Containerlab、注入故障、检查当下网络健康状态、部署公网或推送 GitHub。

| 类别 | 实际执行 | 结果 | 不能推断什么 |
|---|---|---|---|
| Python 干净临时副本 | Ubuntu/WSL2，`python3 history/run_clean_offline_tests_v06.py`；仅复制源码、源配置和 tests/fixtures，不带 `.env`/历史运行/容器目录 | 72 tests，0 skipped，PASS；Python 3.12.3 | 不等于当下现场网络健康或真实模型准确率 |
| Windows 辅助检查 | Windows Python 3.12.10 执行同一入口 | 72 tests，1 skipped（仅 Linux/WSL 的 mock SIGTERM 测试），PASS | 不代替 Linux 信号测试 |
| 前端 | `cd web && npm run check` | TypeScript typecheck PASS；Vitest 7 tests PASS；Vite production build PASS；`web/dist/` 含五份案例 | 不等于网页调用过模型或实验设备 |
| 发布样本 | `python3 tools/check_publication.py` | 仅检查白名单案例/fixtures、占位配置、个人路径/凭据模式及当前文档链接 | 静态扫描不能保证任意未来文件无敏感信息 |
| 浏览器人工检查 | 实际启动本地 Vite 页并打开浏览器 | 5 例可切换；播放、暂停和单步有效；回放前地址/Area/邻居为未知；正常案例的 `E003` 引用跳到 r2 原始 OSPF 输出；采集失败案例从模型原始区域不一致改为最终采集失败并补引 `E003`；缺 Area 例最终证据不足；设备点击可筛选相关调用；控制台无错误 | 浏览器检查是展示验收，不是新模型诊断验收 |

实际页面截图：[workbench-v06.png](images/workbench-v06.png)，截自本地运行的“关键命令采集失败”案例播放完毕的首屏，PNG 930×878，SHA-256 `297dcb64c97ed4e18e7da3132b1f1c49d1f17261a98dcf9bfb35d76fa6d198a`。截图不是设计稿、合成诊断或网络实时观测。

可提交的机器可读测试摘要：[v0.6-test-results.json](v0.6-test-results.json)。原始本机测试输出留在忽略的 `review-results/`，避免发布机器路径。

历史真实模型边界仍为：v0.5 单次定向验收前三例 PASS；动态成功重采 `NOT_EXERCISED`；总体 `INCOMPLETE`。五份导出案例中的 Stage 5 故障回放属于更早版本，不与 v0.5 结果合并计算准确率。完整本地历史材料因可能包含个人路径或冗余运行数据，没有整体加入源码仓库。

GitHub Actions 已新增无密钥、无 Docker 的 Python/前端工作流，但此轮未推送，**远端 CI 尚未执行**。实际结果需以后续 GitHub Actions 页面为准。
