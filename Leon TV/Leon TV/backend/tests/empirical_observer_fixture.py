"""D15 synthetic caller, deliberately terminated by process fault tests."""
import json
import os
import sys
import time
from pathlib import Path

from empirical_worker_fixtures import descriptor_for

from core.execution.receipts import ObservationEnded, ReceiptCaptureSettings
from core.execution.transport import ResponseUnconfirmed
from experiments.planner_live.empirical_capture import EmpiricalCaptureAdapter

root, options = Path(sys.argv[1]).resolve(), json.loads(sys.argv[2])
descriptor = descriptor_for(seconds=options.pop("seconds", 6))
settings = ReceiptCaptureSettings(observation_wait_seconds=options.pop("observation_wait", 4),
    worker_factory="empirical_worker_fixtures:make_transport", worker_options={"counter": str(root / "posts.jsonl"), **options})
adapter = EmpiricalCaptureAdapter(root / "receipts", settings=settings)
(root / "descriptor.json").write_text(json.dumps(descriptor.frozen()))
(root / "parent-ledger.json").write_text(json.dumps({"synthetic_only": True, "risk_fen": 100, "status": "submitting"}))
(root / "observer.json").write_text(json.dumps({"observer_pid": os.getpid(), "capture_id": descriptor.intent.capture_id}))
try:
    adapter.capture(descriptor, "SYNTHETIC-PRIVATE-KEY")
except (ObservationEnded, ResponseUnconfirmed):
    pass
(root / "observer-ended").write_text("not accepted")
time.sleep(60)
