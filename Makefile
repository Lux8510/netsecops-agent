PY ?= python3
NPM ?= npm

.PHONY: help test test-core web demo publish-check clean

help:
	@echo "make test          离线全量：Agent 核心套件 + v1 拓扑套件（无需密钥/Docker）"
	@echo "make web           安装前端依赖并执行类型检查、单元测试、生产构建"
	@echo "make demo          启动回放工作台 http://127.0.0.1:5173/（读取 examples/）"
	@echo "make publish-check 检查示例与文档中的凭据/本机路径及失效链接"
	@echo "make clean         删除离线测试报告与本机采集结果"

test:
	$(PY) -B run_offline_tests.py

web:
	cd web && $(NPM) ci --no-audit --no-fund && $(NPM) run check

demo:
	cd web && $(NPM) ci --no-audit --no-fund && $(NPM) run dev

publish-check:
	$(PY) -B tools/check_publication.py

clean:
	rm -rf review-results v1/results web/dist
