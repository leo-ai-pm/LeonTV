"""Synthetic D15 fixtures. No config, parent production ledger or real HTTP."""
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
from receipt_worker_fixtures import make_transport as receipt_transport

from core.creator.store import digest
from core.execution.policy import now
from experiments.planner_live.empirical_capture import (
    EmpiricalIntent,
    FrozenEmpiricalDescriptor,
    RiskAllocation,
    empirical_wire_for,
)


def descriptor_for(*, seconds=6, parameter="max_tokens", environment="synthetic", context=None):
    context = context if context is not None else [{"role": "user", "content": {"instruction": "SYNTHETIC private 中文"}}]
    wire = empirical_wire_for(context, model="synthetic-text-model", chat_output_parameter=parameter, max_output_tokens=2048)
    issued = now() - timedelta(milliseconds=10)
    intent = EmpiricalIntent(capture_id=uuid.uuid4().hex, intent_id="synthetic-intent-" + uuid.uuid4().hex,
        task_id="synthetic-task", operation_key="synthetic-operation-" + uuid.uuid4().hex,
        authorization_hash="a" * 64, source_snapshot_hash="b" * 64, rules_hash="c" * 64,
        context_hash=digest(context), wire_hash=digest(wire), endpoint="https://synthetic.example/v1/chat/completions",
        model="synthetic-text-model", chat_output_parameter=parameter, max_output_tokens=2048,
        issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=seconds)).isoformat(), environment=environment)
    allocation = RiskAllocation(allocation_id="synthetic-risk-" + uuid.uuid4().hex, intent_id=intent.intent_id,
        intent_hash=intent.intent_hash, operation_key=intent.operation_key, authorization_hash=intent.authorization_hash,
        parent_ledger_hash=digest({"synthetic_reserved_fen": 100, "status": "submitting"}),
        begin_hash=digest({"synthetic_begin": intent.intent_id}), begun_at=issued.isoformat())
    return FrozenEmpiricalDescriptor.build(intent, allocation, context, wire)


def rebind(descriptor, *, intent=None, allocation=None, context=None, wire=None):
    intent = intent or descriptor.intent
    allocation = allocation or replace(descriptor.allocation, intent_id=intent.intent_id,
        intent_hash=intent.intent_hash, operation_key=intent.operation_key, authorization_hash=intent.authorization_hash)
    context = json.loads(descriptor.context_json) if context is None else context
    return FrozenEmpiricalDescriptor.build(intent, allocation, context, descriptor.wire if wire is None else wire)


def make_transport(options):
    transport = receipt_transport(options)

    def handle(request):
        trace = options.get("wire_trace")
        if trace:
            Path(trace).write_text(json.dumps({"wire": request.content.decode(), "wire_hash": digest(request.content.decode()),
                "wire_sha256": hashlib.sha256(request.content).hexdigest(), "method": request.method, "url": str(request.url)}))
        return transport.handle_request(request)

    return httpx.MockTransport(handle)
