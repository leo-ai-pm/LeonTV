"""Images consume the native, paid-and-verified asset result without copying JSON."""
import copy
from types import SimpleNamespace

import pytest
from asset_design_fixture import authorize, design_case, fee, reserve
from media_ledger_fixture import image_fee
from test_execution_empirical import empirical_env as empirical_env
from test_image_policy import SYNTHETIC_PNG

from core.creator.store import CreatorError, digest
from core.execution.asset_media_handoff import (
    check_manifest_origin,
    manifest_origin,
    media_from_asset_design,
)
from core.execution.image_policy import MediaBudgetAnchor
from core.execution.media_ledger import records
from core.execution.policy import money
from core.execution.reference_derivation import ReferenceProfile
from core.execution.scope_budget import scope_envelope


def ready(case):
    _, grant = authorize(case)
    operation = reserve(case, grant)
    received = case.design.start(case.task['id'], operation['id'])['operation']
    return operation, received


def image_settings(case):
    data = case.old.anchor.model_dump()
    count = case.old.policy.max_submissions
    data.update(opening_risk_cny='76.37', opening_paid_call_count=48,
                account_limit_cny=money(76370000 + count * 1000000), account_max_calls=48 + count,
                parent_ledger_hash='b' * 64, parent_allocation_hash='c' * 64,
                parent_allocation_begin_hash='d' * 64)
    anchor = MediaBudgetAnchor.model_validate(data)
    return {'policy': case.old.policy, 'budget_anchor': anchor,
            'authority_guard': lambda scope: scope['anchor'] == anchor.frozen(),
            'fee_source': case.source, 'receipt_capture': case.old.settings,
            'reference_profile': ReferenceProfile(allow_lossy_jpeg=False).frozen()}


def test_native_manifest_fee_gate_origin_image_scope_and_internal_reference_are_one_journal(empirical_env):
    case = design_case(empirical_env)
    original = copy.deepcopy(case.env.runtime.store.read_only())
    operation, received = ready(case)
    with pytest.raises(CreatorError):
        manifest_origin(case.env.runtime.store.read_only(), case.task['id'], operation['id'])
    fee(case, received)
    media = media_from_asset_design(case.env.runtime, case.task['id'], operation['id'], **image_settings(case))
    quote = media.quote(case.task['id'], key='native-asset-image-quote')['quote']
    origin = quote['disclosure']['manifest_origin']
    assert origin['asset_operation_id'] == operation['id']
    assert origin['manifest_hash'] == received['manifest']['manifest_hash']
    assert quote['disclosure']['budget_anchor']['opening_risk_cny'] == '76.37'
    grant = media.grant(case.task['id'], quote['id'], disclosure_hash=quote['disclosure_hash'], consent=True,
                        key='native-asset-image-grant')['grant']
    first = media.reserve_submit(case.task['id'], grant['id'], 'S01', key='native-asset-first-image')['operation']
    first = media.submit(case.task['id'], first['id'])['operation']
    linked = SimpleNamespace(**{**vars(case.old), 'media': media})
    image_fee(linked, first)
    query = media.query(case.task['id'], first['id'], key='native-asset-first-query')['query']
    media.record_delivery(case.task['id'], first['id'], query['id'], SYNTHETIC_PNG)
    second = media.reserve_submit(case.task['id'], grant['id'], 'S02', key='native-asset-second-image')['operation']
    assert second['descriptor']['references'][0]['shot_id'] == 'S01'
    assert second['reference_derivations']
    payload = case.env.runtime.store.read_only()
    scope = next(scope for scope in records(payload, 'scope') if scope['id'] == second['scope_id'])
    assert check_manifest_origin(payload, scope)
    assert all(digest(payload[key]) == digest(value) for key, value in original.items())
    total = scope_envelope(payload, case.anchor.account_scope_id)
    assert total['risk_units'] == 76370000 + case.old.policy.max_submissions * 1000000
    assert total['max_calls'] == 48 + case.old.policy.max_submissions


def test_origin_requires_the_actual_asset_fee_and_manifest_even_if_all_local_hashes_are_rebuilt(empirical_env):
    case = design_case(empirical_env)
    operation, received = ready(case)
    fee(case, received)
    payload = case.env.runtime.store.read_only()
    proof = manifest_origin(payload, case.task['id'], operation['id'])
    scope = {'task_id': case.task['id'], 'account_id': case.anchor.account_scope_id,
             'asset_input': next(record for record in payload['asset_design'].values() if record['kind'] == 'scope')['asset_input'],
             'manifest': proof['manifest'], 'manifest_origin': proof['origin']}
    assert check_manifest_origin(payload, scope)
    attacks = []
    changed = copy.deepcopy(scope)
    changed['manifest_origin']['asset_operation_id'] = 'f' * 32
    attacks.append(changed)
    changed = copy.deepcopy(scope)
    changed['manifest']['frames'][0]['first_frame_prompt'] = 'invented private prompt'
    attacks.append(changed)
    changed = copy.deepcopy(scope)
    changed['manifest_origin']['asset_proof']['raw_hash'] = 'e' * 64
    attacks.append(changed)
    for changed in attacks:
        changed['manifest_origin']['origin_hash'] = digest({key: value for key, value in changed['manifest_origin'].items() if key != 'origin_hash'})
        with pytest.raises(CreatorError):
            check_manifest_origin(payload, changed)
    missing = copy.deepcopy(payload)
    current = missing['asset_design']['operation:' + operation['id']]
    del missing['asset_design']['claim:' + current['fee_id']]
    with pytest.raises(CreatorError):
        manifest_origin(missing, case.task['id'], operation['id'])
