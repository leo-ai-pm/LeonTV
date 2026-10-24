"""Server-owned assembly of the original execution engine; absent control is off."""
import copy
import threading
from contextlib import contextmanager
from pathlib import Path

from core.creator.store import CreatorBriefStore, CreatorError, digest
from core.creator.store import validate_state as validate_briefs
from core.execution.empirical import BudgetAnchor, EmpiricalExecutionPolicy
from core.execution.receipts import ReceiptCaptureSettings, read_json
from core.execution.runtime import ExecutionRuntime
from core.planner.runtime import PlannerRuntime
from core.planner.store import PlannerStore
from core.planner.store import validate_state as validate_planner

BACKEND = Path(__file__).resolve().parents[2]
_UNSET = object()
_control, _control_lock = _UNSET, threading.RLock()


def readonly():
    raise CreatorError("CONTROL_READ_ONLY", "Operator observation mode cannot change execution records", 409)


def operator_control():
    """Freeze one configuration per process; edits never hot-enable a worker."""
    global _control
    with _control_lock:
        if _control is _UNSET:
            from core.execution.control import load_control
            _control = load_control(BACKEND, static_roots=(BACKEND / "code", BACKEND.parent / "frontend/public"))
        return _control


def reset_control_for_tests():
    global _control
    with _control_lock:
        _control = _UNSET


def store_root(name, default):
    control = operator_control()
    return control.roots[name] if control is not None else default


class ReadOnlyBriefStore(CreatorBriefStore):
    @contextmanager
    def transaction(self):
        path = self.path("state.json")
        state = read_json(path, 64 * 1048576) if path.exists() else {"schema_version": 1, "keys": {}, "briefs": {}}
        validate_briefs(state)
        before = digest(state)
        yield state
        if digest(state) != before:
            readonly()


class ReadOnlyPlannerStore(PlannerStore):
    @contextmanager
    def transaction(self):
        path = self.path("state.json")
        state = read_json(path, 64 * 1048576) if path.exists() else {"schema_version": 1, "keys": {}, "tasks": {}}
        validate_planner(state)
        before = digest(state)
        yield state
        if digest(state) != before:
            readonly()


class ObservedPlanner(PlannerRuntime):
    def summary(self, task):
        result = super().summary(task)
        result["allowed_actions"] = []
        return result

    def recover(self, task_id):
        pass

    def create(self, *args, **kwargs):
        readonly()

    def action(self, *args, **kwargs):
        readonly()


def build_brief_store(default):
    control = operator_control()
    cls = ReadOnlyBriefStore if control is not None and control.document.mode == "observe_only" else CreatorBriefStore
    store = cls(store_root("creator", default))
    store.operator_control = control
    return store


def build_planner(default, briefs):
    control = operator_control()
    if control is not None and control.document.mode == "observe_only":
        runtime = ObservedPlanner(control.roots["planner"], briefs)
        runtime.store = ReadOnlyPlannerStore(control.roots["planner"])
        runtime.operator_control = control
        return runtime
    runtime = PlannerRuntime(store_root("planner", default), briefs)
    runtime.operator_control = control
    return runtime


class OperatorExecutionRuntime(ExecutionRuntime):
    """Only assembly/observation/admission differ; worker and journal are inherited."""
    def __init__(self, control, planner, *, api_key=None, capture=None):
        from core.execution.fees import FeeInbox, FrozenFeeSource
        self.control = control
        self.operator_observe_only = control.document.mode == "observe_only"
        self.fee_inbox = FeeInbox(control.roots["fees"], FrozenFeeSource.model_validate(control.document.fee_source.model_dump()))
        policy = EmpiricalExecutionPolicy.model_validate({key: value for key, value in control.document.policy.items() if key != "policy_hash"})
        anchor = BudgetAnchor.model_validate({key: value for key, value in control.document.budget_anchor.items() if key != "parent_anchor_hash"})
        super().__init__(control.roots["execution"], planner, policy=policy, budget_anchor=anchor,
            enabled=not self.operator_observe_only, api_key=api_key,
            anchor_guard=lambda value: control.guard(value, planner, self.store),
            fee_verifier=self.fee_inbox.verifier, receipt_capture=capture or ReceiptCaptureSettings())
        self._observer_stop = threading.Event()
        self._observer_thread = None
        self._startup_tasks = set(self.store.read_only()["tasks"])
        self._startup_lock = threading.RLock()

    def start_observer(self):
        if self.operator_observe_only or not self.control.document.observer_enabled or self._observer_thread is not None:
            return

        def observe():
            while not self._observer_stop.wait(0.5):
                try:
                    self.fee_inbox.drain(self, observe_only=False)
                except Exception:
                    # Native holds and prepared evidence remain; no network retry.
                    self._observer_stop.set()
        self._observer_thread = threading.Thread(target=observe, name="platform-fee-inbox", daemon=True)
        self._observer_thread.start()

    def close(self):
        self._observer_stop.set()
        if self._observer_thread is not None:
            self._observer_thread.join(3)
        super().close()

    def _admit(self, task_id):
        if self.operator_observe_only:
            readonly()
        self.control.verify_authority(BACKEND, for_dispatch=True)
        self.control.check_admission(self.planner, task_id)

    def quote(self, task_id, body):
        self._admit(task_id)
        return super().quote(task_id, body)

    def grant(self, task_id, body):
        self._admit(task_id)
        return super().grant(task_id, body)

    def action(self, task_id, body):
        if self.operator_observe_only:
            readonly()
        if body["action"] == "resume":
            self._admit(task_id)
        with self._startup_lock:
            result = super().action(task_id, body)
            if body["action"] == "resume" and not result["replayed"]:
                self._startup_tasks.discard(task_id)
        return result

    def revoke(self, *args, **kwargs):
        if self.operator_observe_only:
            readonly()
        return super().revoke(*args, **kwargs)

    def observe_fee(self, task_id, operation_id, evidence, *, auto_continue=True, **kwargs):
        if self.operator_observe_only:
            readonly()
        with self._startup_lock:
            may_continue = auto_continue is True and task_id not in self._startup_tasks
            return super().observe_fee(task_id, operation_id, evidence, auto_continue=may_continue, **kwargs)

    def policy_view(self):
        if not self.operator_observe_only:
            return super().policy_view()
        return {"available": False, "blocked_reason": "LIVE_DISABLED", "environment": self.environment(), "policy": None}

    def get(self, task_id):
        if not self.operator_observe_only:
            return super().get(task_id)
        payload = self.store.read_only()
        if task_id not in payload["tasks"]:
            raise CreatorError("NOT_FOUND", "Execution record not found", 404)
        detail = self.detail(payload, task_id)
        detail["allowed_actions"] = []
        return {"execution": detail}

    def by_key(self, task_id, namespace, key):
        if not self.operator_observe_only:
            return super().by_key(task_id, namespace, key)
        payload = self.store.read_only()
        record = payload["keys"].get(namespace + ":" + key)
        if not record or record["task_id"] != task_id:
            raise CreatorError("NOT_FOUND", "Execution request key not found", 404)
        result = self.resource_result(payload, task_id, namespace, record, True)
        return {namespace: result[namespace]}

    def events(self, task_id, after, limit):
        if not self.operator_observe_only:
            return super().events(task_id, after, limit)
        payload = self.store.read_only()
        if task_id not in payload["tasks"]:
            raise CreatorError("NOT_FOUND", "Execution record not found", 404)
        events = payload["tasks"][task_id]["events"]
        page = copy.deepcopy(events[after:after + limit])
        return {"events": page, "next_sequence": page[-1]["sequence"] if page else after, "has_more": after + limit < len(events)}


def build_execution_runtime(default, planner, *, credential_loader=None):
    control = operator_control()
    if control is None:
        return ExecutionRuntime(default, planner)
    if planner.store.root != control.roots["planner"] or planner.brief_store.root != control.roots["creator"]:
        raise CreatorError("CONTROL_UNAVAILABLE", "Operator control is unavailable", 503)
    key = None
    if control.document.mode == "enabled":
        control.verify_authority(BACKEND, for_dispatch=True)
        if credential_loader is None:
            def credential_loader():
                from config import load_config
                provider = load_config()["api_providers"]["tudou"]
                if provider["base_url"].rstrip("/").removesuffix("/v1") != "https://api.ai-tudou.net":
                    raise CreatorError("CONTROL_UNAVAILABLE", "Operator control is unavailable", 503)
                return provider["api_key"]
        key = credential_loader()
        if not isinstance(key, str) or not key:
            raise CreatorError("CONTROL_UNAVAILABLE", "Operator control is unavailable", 503)
    return OperatorExecutionRuntime(control, planner, api_key=key)
