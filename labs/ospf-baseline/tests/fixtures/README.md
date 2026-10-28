# 离线回归 fixtures

这些文件是从 v0.1 已保存观测与轨迹中提取的**固定样本**，只用于无 API、无 Docker 的回归测试；原始历史文件未改动。`observations/normal.json`、`area-fault.json`、`recovered.json` 对应已有正常、区域不一致和恢复快照；`missing-one-area.json`、`failed-command.json` 对应已有缺证据/采集失败输入；`stage5-case-01..03.json` 与 `traces/stage5-case-01..03.json` 对应原三种 r2 验收案例；`traces/normal.json` 是正常模型轨迹。`configs/` 从正常观测中的运行配置生成，供旧的启动配置解析单测使用。

测试只读取此目录、拓扑、源配置和项目设置，不再依赖 `experiments/`、`agent-runs/`、`diagnostic-acceptance/` 或 Containerlab 生成目录。反例测试对样本做内存或临时文件修改，不写回 fixtures。样本不包含密钥；如将来换样本，必须重新做凭据扫描并建立新版本记录。

v0.6 发布说明：这里的大多数 JSON 是从历史观测/轨迹提取的固定回归副本，不是合成现场。v0.3 `v04-failures/normal-acceptance.json` 原始摘录含个人工作站绝对路径，仍保留在本地、被 `.gitignore` 排除，干净副本不复制它。可提交的 `v04-failures/normal-acceptance-public.json` 是**脱敏的判定摘要**，只保留测试需要的 `FAIL` 事实；不是完整验收报告。历史 v0.5 SHA 清单因此仍对应原始本地文件，新发布材料另建版本记录，不能拿旧清单验证这个发布副本。
