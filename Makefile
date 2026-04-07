REGISTRY   := csdread
NAMESPACE  := agents

RUNNER_IMAGE  := $(REGISTRY)/agent-runner
GMAIL_IMAGE   := $(REGISTRY)/gmail-mcp
GCAL_IMAGE    := $(REGISTRY)/gcal-mcp

RUNNER_TAG    ?= 3
GMAIL_TAG     ?= 2
GCAL_TAG      ?= 2

# ─── Build ───────────────────────────────────────────────────────────────────

.PHONY: build build-runner build-gmail build-gcal

build: build-runner build-gmail build-gcal

build-runner:
	docker build -t $(RUNNER_IMAGE):$(RUNNER_TAG) runner/

build-gmail:
	docker build -t $(GMAIL_IMAGE):$(GMAIL_TAG) mcps/gmail/

build-gcal:
	docker build -t $(GCAL_IMAGE):$(GCAL_TAG) mcps/gcal/

# ─── Push ────────────────────────────────────────────────────────────────────

.PHONY: push push-runner push-gmail push-gcal

push: push-runner push-gmail push-gcal

push-runner:
	docker push $(RUNNER_IMAGE):$(RUNNER_TAG)

push-gmail:
	docker push $(GMAIL_IMAGE):$(GMAIL_TAG)

push-gcal:
	docker push $(GCAL_IMAGE):$(GCAL_TAG)

# ─── Build + Push combined ───────────────────────────────────────────────────

.PHONY: release release-runner release-gmail release-gcal

release: build push

release-runner: build-runner push-runner

release-gmail: build-gmail push-gmail

release-gcal: build-gcal push-gcal

# ─── Deploy ──────────────────────────────────────────────────────────────────

.PHONY: deploy deploy-ns deploy-rbac deploy-mcps deploy-briefing deploy-all

deploy-ns:
	kubectl apply -f k8s/agents/namespace.yaml

deploy-rbac:
	kubectl apply -f k8s/agents/rbac/

deploy-mcps:
	kubectl apply -f k8s/agents/gmail-mcp/
	kubectl apply -f k8s/agents/gcal-mcp/
	kubectl apply -f k8s/agents/mac-bridge/

deploy-briefing:
	kubectl apply -f k8s/agents/daily-briefing/cronjob.yaml

deploy-all: deploy-ns deploy-rbac deploy-mcps update-config deploy-briefing

# ─── Config (AGENT.md + mcp.json) ────────────────────────────────────────────

.PHONY: update-config

update-config:
	kubectl create configmap daily-briefing-config \
		--from-file=AGENT.md=prompts/daily-briefing/AGENT.md \
		--from-file=mcp.json=k8s/agents/daily-briefing/mcp.json \
		-n $(NAMESPACE) \
		--dry-run -o yaml | kubectl apply -f -

# ─── Manual job trigger ───────────────────────────────────────────────────────

.PHONY: run run-clean

# Delete existing manual job and reapply (keeps a single persistent manual job)
run:
	kubectl delete job briefing-manual-1 -n $(NAMESPACE) --ignore-not-found
	kubectl apply -f k8s/agents/daily-briefing/job-manual.yaml
	@echo "Job started. Run 'make logs' to follow output."

# One-off job with a unique timestamped name (keeps history)
run-once:
	@JOB_NAME=briefing-run-$$(date +%s) && \
	sed "s/name: briefing-manual-1/name: $$JOB_NAME/" \
		k8s/agents/daily-briefing/job-manual.yaml | kubectl apply -f - && \
	echo "Started job: $$JOB_NAME"

# ─── Logs ─────────────────────────────────────────────────────────────────────

.PHONY: logs status

logs:
	kubectl logs -n $(NAMESPACE) -f job/briefing-manual-1 -c agent-runner

status:
	@echo "=== CronJobs ==="
	kubectl get cronjobs -n $(NAMESPACE)
	@echo ""
	@echo "=== Jobs ==="
	kubectl get jobs -n $(NAMESPACE)
	@echo ""
	@echo "=== Pods ==="
	kubectl get pods -n $(NAMESPACE)

# ─── Rollout restarts (after pushing new images) ──────────────────────────────

.PHONY: restart-mcps restart-gmail restart-gcal

restart-gmail:
	kubectl rollout restart deployment/gmail-mcp -n $(NAMESPACE)

restart-gcal:
	kubectl rollout restart deployment/gcal-mcp -n $(NAMESPACE)

restart-mcps: restart-gmail restart-gcal

# ─── Help ─────────────────────────────────────────────────────────────────────

.PHONY: help

help:
	@echo "Usage: make [target] [VAR=value]"
	@echo ""
	@echo "Build"
	@echo "  build              Build all images"
	@echo "  build-runner       Build agent-runner image"
	@echo "  build-gmail        Build gmail-mcp image"
	@echo "  build-gcal         Build gcal-mcp image"
	@echo ""
	@echo "Push"
	@echo "  push               Push all images"
	@echo "  push-runner        Push agent-runner image"
	@echo ""
	@echo "Release (build + push)"
	@echo "  release            Build and push all images"
	@echo "  release-runner     Build and push agent-runner only"
	@echo ""
	@echo "Deploy"
	@echo "  deploy-all         Deploy everything (ns, rbac, mcps, config, cronjob)"
	@echo "  deploy-briefing    Apply cronjob.yaml only"
	@echo "  update-config      Reload AGENT.md + mcp.json into ConfigMap"
	@echo ""
	@echo "Run"
	@echo "  run                Delete + reapply job-manual.yaml"
	@echo "  run-once           Start a new uniquely-named job (keeps history)"
	@echo "  logs               Follow logs from briefing-manual job"
	@echo "  status             Show cronjobs, jobs, and pods"
	@echo ""
	@echo "Variables"
	@echo "  RUNNER_TAG=N       Override agent-runner image tag (default: $(RUNNER_TAG))"
	@echo "  GMAIL_TAG=N        Override gmail-mcp image tag (default: $(GMAIL_TAG))"
	@echo "  GCAL_TAG=N         Override gcal-mcp image tag (default: $(GCAL_TAG))"
