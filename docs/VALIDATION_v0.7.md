# v0.7 本机事件流验证（2026-10-28）

本记录只验证可选回调、FastAPI/SSE 和网页事件显示；不改写 [v0.6 清单](v0.6-release-manifest.json)或 v0.5 真实模型验收。本轮没有调用 DeepSeek、读取本机密钥、执行 Docker/Containerlab 命令、注入故障或推送远端。

| 检查 | 实际结果 | 边界 |
|---|---|---|
| Agent 回调 | 模型实际选择的 `tool_calls`、工具结果和最终 trace 的事件顺序经单元测试核对；回调异常不能更改诊断，记入 `event_delivery_errors` | 这不说明真实模型本轮选了哪些工具 |
| 本机 SSE | FastAPI 测试客户端收到开始、模型响应、9 个真实 `AgentTools` 返回和完成事件；会话令牌、非法 Origin、未启用 live、非法模式及并发拒绝均测试 | 演示模型是脚本化 mock；固定观测来自测试 fixture |
| WSL 干净副本 | Ubuntu/WSL2 Python 3.12.3，隔离 venv；77 tests，0 skipped，PASS；未复制 `.env`、历史运行或 Containerlab 生成目录 | 不等于现场网络或真实 DeepSeek 验收 |
| 前端 | `npm run check`：类型检查、7 项网页测试与生产构建通过 | GitHub Actions 尚未执行 |
| 浏览器 | 本地 Vite + 本机 mock SSE 服务：页面先显示“诊断开始”，随后逐步收到工具 E001–E009，最后显示保护层结论；点击 E003 显示 r2 OSPF 接口原始命令、Area 和退出码；可下载本次轨迹 JSON | 没有测试 `--enable-live` 按钮或真实模型调用 |

实际运行截图：[progress-v07.png](images/progress-v07.png)，PNG 930×878，SHA-256 `26e24cceabdb3a5e25d5dffa403540178df5b211e612472874590d678439e8e4`。截图展示的是**mock 事件演示**，不是当前网络状态或新的真实模型诊断。

服务默认绑定 `127.0.0.1:8000`，只接受 `demo`/`live` 固定模式，没有任意 shell、文件路径或任务输入。只有启动参数 `--enable-live`、WSL2 环境和项目密钥同时满足时才提供 live；此路径仍需要独立真实验收。默认服务最多保留 8 次运行于内存，重启后消失；如需持久保存，应下载轨迹。v0.5 动态成功重采路径仍为 `NOT_EXERCISED`，总体 `INCOMPLETE`。
