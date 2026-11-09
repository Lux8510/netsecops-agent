# v1 第一阶段离线验证记录（2026-10-29，Asia/Shanghai）

## 基线保护

- GitHub 私有仓库 `main` 核对为 `77ede943b191880a34ba5ea5925a39ef0fa56e8f`。
- 本地项目不是 Git 检出目录，无法给出 `git status`。以 GitHub 该提交的树对象逐项比较，本地 131 个远端文件内容哈希全部一致；新增内容限于 `v1/`。本地被忽略的旧运行历史、密钥文件与缓存未比较，也未修改。
- v0.5 证据、冻结清单和 v0.7 源码未改写。本次没有提交或推送。

## 实际运行

| 检查 | 环境 | 结果 |
| --- | --- | --- |
| `python -B -m v1.topology` | Windows Python 3.12.10 | PASS：6 节点、6 链路；结构候选路 r1–r2–r4 cost 20、r1–r3–r4 cost 100 |
| `yaml.safe_load(v1/lab/topology.clab.yml)` | Windows，已有 PyYAML | PASS：YAML 可解析，6 节点、6 链路；不等于 Containerlab 部署验证 |
| `python -B v1/run_offline_tests.py` | Windows Python 3.12.10，临时 v1 独立副本 | PASS：23 项，0 跳过 |
| `python3 v1/run_offline_tests.py` | Ubuntu 24.04/WSL2 Python 3.12.3，临时 v1 独立副本 | PASS：23 项，0 跳过 |
| `.venv/Scripts/python.exe -B run_clean_offline_tests_v07.py` | Windows，现有虚拟环境 | PASS：77 项，1 跳过；跳过的是 POSIX SIGTERM 测试 |
| `python3 -m unittest discover -s labs/ospf-baseline/tests -p test_reliability.py -v` | Ubuntu/WSL2 | PASS：11 项，0 跳过，含 POSIX SIGTERM mock 子进程测试 |
| `python3 run_clean_offline_tests_v07.py` | Ubuntu/WSL2 系统 Python | 未执行测试：缺少 `fastapi`，入口退出 2；本轮未安装依赖 |
| `python3 run_clean_offline_tests_v06.py` | Ubuntu/WSL2 系统 Python | FAIL：75 项中 `test_progress_api` 导入失败，原因同为缺少 `fastapi`；原始输出保存在忽略目录 `review-results/v0.6-demo-offline-20261029T064728Z/`；本轮未更改旧测试 |

以上仅证明模型校验、静态资产一致性与旧版适用离线回归。尚未运行 `clab` 解析/部署、FRR 运行配置读取、OSPF FULL、路由转发或 HTTP 端到端检查；四组 `synthetic/mock` 样本也不是现场验收。

## 下一阶段门槛

先依 [`README.md`](README.md) 做现有资源、地址、镜像和权限检查；出现冲突就停止并调整 v1 计划。资源无冲突后才能安排六节点部署与业务基线验证。进入下一阶段的**准备材料已具备**，运行环境与真实网络结果仍待验证。
