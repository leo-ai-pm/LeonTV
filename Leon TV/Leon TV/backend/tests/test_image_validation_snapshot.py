"""Pure descriptor proof reuse is bounded to one fresh journal reconstruction."""
import copy
import json

from media_ledger_fixture import authorize, media_case, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_image_policy import make_submit, reseal

from core.creator.store import digest
from core.execution import image_policy
from core.execution.store import validate_state


def test_each_fresh_journal_read_rebuilds_identical_submit_descriptor_once(empirical_env, monkeypatch):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    reserve(case, grant)
    state = json.loads(case.media.store.path("state.json").read_text())
    rebuilds = []
    original = image_policy._submit_data

    def rebuild(**kwargs):
        rebuilds.append(kwargs["permit"]["operation_id"])
        return original(**kwargs)

    monkeypatch.setattr(image_policy, "_submit_data", rebuild)
    for _ in range(2):
        rebuilds.clear()
        validate_state(state)
        assert len(rebuilds) == 1, "one snapshot repeatedly rebuilt the same pure descriptor"


def test_snapshot_reuse_has_an_entry_cap_and_drops_unstored_proofs(monkeypatch):
    original = image_policy._submit_data
    calls = []
    base = make_submit().frozen()

    def rebuild(**kwargs):
        calls.append(kwargs["permit"]["nonce"])
        return original(**kwargs)

    monkeypatch.setattr(image_policy, "_submit_data", rebuild)
    with image_policy._descriptor_snapshot():
        for index in range(33):
            value = copy.deepcopy(base)
            value["permit"]["nonce"] = f"{index + 100:032x}"
            value["permit"]["permit_hash"] = digest({key: item for key, item in value["permit"].items() if key != "permit_hash"})
            last = reseal(value)
            image_policy.FrozenImageRequest.from_frozen(last)
        assert len(image_policy._SNAPSHOT_PROOFS.get()["inputs"]) == 32
        image_policy.FrozenImageRequest.from_frozen(last)
        assert len(calls) == 34
    assert image_policy._SNAPSHOT_PROOFS.get() is None


def test_snapshot_reuse_has_a_total_byte_cap(monkeypatch):
    original = image_policy._submit_data
    base = make_submit().frozen()
    # Each input still satisfies its existing descriptor size bound. The sum
    # of independently valid proofs must also stay bounded during a read.
    maximum = len(json.dumps(base, ensure_ascii=False).encode("utf-8")) * 2
    monkeypatch.setattr(image_policy, "MAX_DESCRIPTOR_BYTES", maximum)
    calls = []

    def rebuild(**kwargs):
        calls.append(kwargs["permit"]["nonce"])
        return original(**kwargs)

    monkeypatch.setattr(image_policy, "_submit_data", rebuild)
    with image_policy._descriptor_snapshot():
        for index in range(4):
            value = copy.deepcopy(base)
            value["permit"]["nonce"] = f"{index + 200:032x}"
            value["permit"]["permit_hash"] = digest({key: item for key, item in value["permit"].items() if key != "permit_hash"})
            last = reseal(value)
            image_policy.FrozenImageRequest.from_frozen(last)
        facts = image_policy._SNAPSHOT_PROOFS.get()
        assert facts["bytes"] <= maximum
        assert len(facts["inputs"]) < 4
        image_policy.FrozenImageRequest.from_frozen(last)
        assert len(calls) == 5
