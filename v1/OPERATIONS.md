# 本机基线工作台

在 WSL 项目根目录执行 `python3 -m v1.baseline workbench`：Containerlab 部署或协调实验，最多等待 90 秒，采集通过后启动 http://127.0.0.1:5173/ 。保持该前台会话运行。网页底部“六节点真实基线”读取最近一次采集结果。

只采集：`python3 -m v1.baseline check`。启动并等待基线：`python3 -m v1.baseline start`。查看容器：`python3 -m v1.baseline status`。停止并移除本实验：`python3 -m v1.baseline stop`。

结果保存在 `v1/results/<run_id>.json`，最新快照为 `latest.json`，包含命令、退出码、时间、原始 stdout/stderr、检查结论和证据引用。结果目录和生成 lab 目录已加入 Git 忽略规则。

网页只在 Vite 开发服务器提供本机快照接口；静态生产构建不包含网络证据。点击读取不会执行命令。每次重新检查在终端执行 check，再在网页刷新；显示的时间用于识别旧快照。

当前基线针对审查包的固定六节点地址和主路径。尚未泛化到任意拓扑，也没有故障诊断或模型调用。部分实验由 Containerlab 协调，不自动删除未知资源；同名容器必须具有本项目 containerlab 标签。若协调不能修复，先检查失败证据，再明确 stop/start。

验证：v1 离线 26 项、v0.7 离线 77 项通过；前端 7 项、类型检查及构建通过；真实六节点基线全部检查通过。
