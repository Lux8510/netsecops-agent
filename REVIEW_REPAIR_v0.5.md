# v0.5：成功重采与 Area 证据分离（仅离线）

本次依据 `review-feedback-v0.4.zip` 的定向复审，只修复一个 P2。附件测试原样保存在 `review-input-v0.4/test_review_recheck_v04.py`；来源 SHA256、旧实现上两项失败的独立复现记录见 `review-results/v0.5-red-20261014/result.md`。原 v0.1–v0.4 清单、审查包、轨迹、验收结果均不覆盖。

## 修复

`labs/ospf-baseline/evidence_checks.py` 的 `critical_attempt_kind()` 原来把 OSPF Area 行当成查询成功的必要条件。因此，FRR 固定查询返回退出码 0、正确接口头、`ifindex` 状态行和 `OSPF not enabled on this interface` 时，先前的命令执行失败仍被错误地视为当前失败。现在该种完整且身份匹配的状态输出可作为**命令成功重采**，取代同节点、同接口、同工具的旧执行失败；旧失败证据保留在轨迹中。残缺行、错误接口、非白名单命令、地址矛盾或命令错误不能取代失败。

“查询成功”不等于“Area 已证明”。不含 Area 的 down/未启用输出不会进入 `normal_proofs()` 或 `cited_area_facts()` 的 Area 事实，故不能确认区域正常或区域不一致。管理关闭仍须由故障接口的 FRR 管理状态直接证实，不能仅凭这条 OSPF 输出推断根因。独立评判器沿用原有根因和引用要求，没有放宽验收条件。

## 测试和范围

附件原两项测试先在 v0.4 上运行，结果为 **2 FAIL**，修复后 **2 PASS**。项目 `tests/test_review_recheck_v04.py` 纳入相同两项并新增四项边界：不含 Area 不能证明正常或区域差异；残缺输出、错误接口、Area 地址错误不能清除旧执行失败。旧有 66 项测试仍保留；新总数为 72 项。

干净副本入口：`python3 run_clean_offline_tests_v05.py`。它只复制项目白名单源码、测试、fixtures、拓扑与源配置到临时目录，不复制 `.env`、历史实验、Agent 轨迹、Containerlab 生成目录或 `.git`。Linux SIGTERM 测试只向其自身创建的 mock 子进程发信号。本次不读取密钥、不调用 DeepSeek 或 Docker、不注入真实故障，也不重新检查现场网络。

Windows 完整测试与干净副本均为 **72 项通过、1 项 Linux 专用测试按平台跳过**；Ubuntu/WSL2 Python 3.12.3 干净副本实际运行 **72 项通过、0 跳过**，见 `review-results/v0.5-offline-20261014T105947Z/{result.json,unittest-output.txt}`。该临时副本收集 62 个白名单文件。

v0.5 清单为 `labs/ospf-baseline/reliability-v0.5.json`，审查包为项目根目录的 `review-bundle-v0.5.zip`。v0.4 的离线验收与 v0.3 的两个真实 FAIL 仍按各自旧版本记录解读；v0.5 的离线通过不构成真实模型/现场验收。
