# AIM Platform Makefile v1.0
# 标准化开发→测试→部署→运维流程

.PHONY: help test deploy restart status rollback verify clean sync

# 默认目标
help:
	@echo "AIM Platform 标准化命令"
	@echo ""
	@echo "开发:"
	@echo "  make test        语法检查 + 导入测试"
	@echo "  make verify      部署前差异检查"
	@echo ""
	@echo "部署:"
	@echo "  make deploy      部署到所有 Agent + 重启"
	@echo "  make deploy-dry  预览部署（不执行）"
	@echo "  make deploy Z1   只部署 ZS0001"
	@echo "  make sync        全量同步（覆盖所有 Agent 目录）"
	@echo ""
	@echo "运维:"
	@echo "  make restart     重启所有 Agent"
	@echo "  make restart Z1  重启 ZS0001"
	@echo "  make status      查看所有 Agent 状态"
	@echo "  make health      健康检查"
	@echo ""
	@echo "回滚:"
	@echo "  make rollback    回滚到上次 commit"

# ── 测试 ──
test:
	@echo "=== 语法检查 ==="
	@for f in aim-client/main.py aim_nats_sdk.py aim-client/security.py aim-client/registry.py aim-client/session.py aim-client/group_admission.py aim-client/context.py; do \
		[ -f "$$f" ] && python3 -c "import py_compile; py_compile.compile('$$f', doraise=True)" && echo "  ✅ $$f" || echo "  ❌ $$f"; \
	done
	@echo ""
	@echo "=== 导入测试（3.14）==="
	@python3 -c "\
import sys; sys.path.insert(0, 'aim-client'); \
from aim_nats_sdk import AIMNatsSDK; print('  ✅ aim_nats_sdk'); \
from security import SecurityModel; print('  ✅ security'); \
from context import ContextCard; print('  ✅ context')" 2>&1 || echo "  ⚠️  部分导入失败（可能缺依赖）"

# ── 验证（不部署，只对比差异）──
verify:
	@bash scripts/deploy.sh --verify

# ── 部署 ──
deploy:
	@bash scripts/deploy.sh --restart

deploy-dry:
	@bash scripts/deploy.sh --dry-run

# 指定 Agent 部署（make Z1 / make Z2 / make Z3）
Z1:
	@bash scripts/deploy.sh --agent ZS0001 --restart

Z2:
	@bash scripts/deploy.sh --agent ZS0002 --restart

Z3:
	@bash scripts/deploy.sh --agent ZS0003 --restart

sync:
	@echo "=== 全量同步（覆盖）==="
	@bash scripts/deploy.sh --restart

# ── 重启 ──
restart:
	@if [ -z "$(filter-out $@,$(MAKECMDGOALS))" ]; then \
		echo "=== 重启全部 Agent ==="; \
		for agent in ZS0001 ZS0003; do \
			svc="com.aim.agent.$$agent"; \
			launchctl kickstart -k gui/$$(id -u)/$$svc 2>/dev/null && echo "  ✅ $$agent" || echo "  ❌ $$agent"; \
		done; \
		echo "  ⚠️  ZS0002: 吉量管理"; \
	fi

# ── 状态 ──
status:
	@echo "=== AIM Agent 状态 ==="
	@ps aux | grep -E "aim-client|registry|alertd|healthd|issue-worker" | grep -v grep | awk '{split($$11,a,"/"); ver=a[5]; cmd=$$12" "$$13" "$$14; printf "  %-6s %-6s %s %s\n", $$2, ver, substr(cmd,1,30), $$15}' || echo "  无运行进程"
	@echo ""
	@echo "=== Queue 积压 ==="
	@for agent in ZS0001 ZS0002 ZS0003; do \
		qf="$$HOME/.aim/agents/$$agent/queue.jsonl"; \
		[ -f "$$qf" ] && python3 -c "import json; lines=open('$$qf').readlines(); a=sum(1 for l in lines if json.loads(l).get('op')=='ack'); e=len(lines)-a; print(f'  $$agent: {len(lines)} total, {e} pending, {a} acked')" || echo "  $$agent: no queue"; \
	done
	@echo ""
	@echo "=== Git ==="
	@git log --oneline -1

# ── 健康检查 ──
health:
	@echo "=== 健康检查 ==="
	@for agent in ZS0001 ZS0003; do \
		adapter="$$HOME/.aim/agents/$$agent/adapter.sh"; \
		if [ -f "$$adapter" ]; then \
			result=$$(timeout 15 bash "$$adapter" info 2>/dev/null | head -1 || echo "TIMEOUT"); \
			echo "  $$agent: $$result"; \
		else \
			echo "  $$agent: adapter not found"; \
		fi; \
	done
	@echo "  ZS0002: 吉量管理"

# ── 回滚 ──
rollback:
	@echo "=== 回滚到上一个版本 ==="
	@git log --oneline -5
	@echo ""
	@echo "上一次 commit: $$(git log --oneline -1)"
	@echo "上上次 commit: $$(git log --oneline --skip=1 -1)"
	@echo ""
	@read -p "确认回滚? (输入 yes 继续) " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		git revert --no-edit HEAD && \
		make deploy && \
		echo "✅ 回滚完成"; \
	else \
		echo "已取消"; \
	fi

# ── 清理 ──
clean:
	@echo "=== 清理 ==="
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
	@find . -name "*.pyc" -delete 2>/dev/null
	@find . -name "*.bak.*" -not -path "*/.git/*" -delete 2>/dev/null
	@echo "✅ __pycache__, .pyc, .bak.* 已清理"

# 捕获 make restart Z1 之类的参数
%:
	@:
