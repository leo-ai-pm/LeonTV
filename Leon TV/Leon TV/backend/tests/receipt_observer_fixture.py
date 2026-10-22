"""Synthetic observer process, intentionally killed by D14 failure tests."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from test_planner_execution import Env, Provider, app_for, policy

from core.creator.store import CreatorBriefStore
from core.execution.policy import ExecutionPolicy, TrustedInputBounder
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.runtime import ExecutionRuntime
from core.planner.runtime import PlannerRuntime

root = Path(sys.argv[1]).resolve()
options = json.loads(sys.argv[2])
source_store = CreatorBriefStore(root / "briefs")
planner = PlannerRuntime(root / "planner", source_store)
base = policy().model_dump()
base["request_deadline_seconds"] = options.pop("deadline_seconds", 5)
p = ExecutionPolicy.model_validate(base)
bounder = TrustedInputBounder(p.frozen()["policy_hash"], "synthetic-detached-fixture", lambda wire, frozen: len(wire) + 1024)
wait_for_http = options.pop("kill_while_observing", False)
settings = ReceiptCaptureSettings(observation_wait_seconds=options.pop("observation_wait", 0.2), cleanup_grace_seconds=options.pop("grace", 1),
    worker_factory="receipt_worker_fixtures:make_transport", worker_options={"counter": str(root / "posts.jsonl"), **options})
runtime = ExecutionRuntime(root / "execution", planner, policy=p, enabled=True, api_key="synthetic-secret",
    transport=httpx.MockTransport(lambda request: None), input_bounder=bounder, receipt_capture=settings)
with TestClient(app_for(runtime)) as client:
    env = Env(client, runtime, Provider(), planner)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    if wait_for_http:
        limit = time.monotonic() + 3
        while not (root / "posts.jsonl").exists() and time.monotonic() < limit:
            time.sleep(0.01)
    else:
        env.wait(task, {"needs_reconciliation"}, seconds=4)
    payload = runtime.store.read_only()
    operation = next(iter(payload["operations"].values()))
    original = runtime.store.path("state.json").read_bytes()
    (root / "observer.json").write_text(json.dumps({"task_id": task["id"], "operation_id": operation["id"],
        "observer_pid": os.getpid(), "journal_sha256": hashlib.sha256(original).hexdigest()}))
    time.sleep(60)
