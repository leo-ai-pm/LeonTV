"""One frozen parent envelope cannot be independently allocated twice."""
import copy

import pytest

from core.creator.store import CreatorError
from core.execution.scope_budget import scope_envelope, validate_shared_scopes


def case():
    return {'accounts': {'account': {'id': 'account', 'limit_units': 75370000, 'max_calls': 47}},
            'operations': {}, 'media': {}, 'asset_design': {}}


def add_scope(payload, namespace, name, opening, risk, calls, *, opening_calls=47, allocation=None):
    from core.execution.policy import money, units
    payload[namespace]['scope:' + name] = {'kind': 'scope', 'id': name, 'account_id': 'account',
        'anchor': {'opening_risk_cny': opening, 'scope_risk_cny': risk,
                   'scope_max_calls': calls, 'opening_paid_call_count': opening_calls,
                   'account_limit_cny': money(units(opening) + units(risk)),
                   'account_max_calls': opening_calls + calls,
                   'parent_allocation_hash': allocation or name}}


def test_full_parent_and_both_native_namespaces_are_counted():
    payload = case()
    add_scope(payload, 'media', 'image', '75.37', '2', 2)
    before = copy.deepcopy(payload)
    add_scope(payload, 'asset_design', 'asset', '77.37', '1', 1, opening_calls=49)
    assert scope_envelope(payload, 'account') == {'risk_units': 78370000, 'max_calls': 50}
    validate_shared_scopes(payload, before)


def test_a_padded_frozen_anchor_is_never_reduced_to_actual_operations():
    payload = case()
    add_scope(payload, 'media', 'image', '80', '2', 2, opening_calls=60)
    assert scope_envelope(payload, 'account') == {'risk_units': 82000000, 'max_calls': 62}


@pytest.mark.parametrize('attack', ['opening', 'calls', 'two_scopes', 'allocation', 'total_risk'])
def test_cross_namespace_scope_reservation_rejects_double_use(attack):
    before = case()
    add_scope(before, 'media', 'image', '75.37', '2', 2)
    after = copy.deepcopy(before)
    opening, risk, calls = '77.37', '1', 49
    if attack == 'opening':
        opening = '75.37'
    elif attack == 'calls':
        calls = 47
    elif attack == 'total_risk':
        risk = '23'
    add_scope(after, 'asset_design', 'asset', opening, risk, 1, opening_calls=calls,
              allocation='image' if attack == 'allocation' else None)
    if attack == 'two_scopes':
        add_scope(after, 'media', 'second-image', '77.37', '1', 1, opening_calls=49)
    with pytest.raises(CreatorError, match='Shared execution allocations'):
        validate_shared_scopes(after, before)


def test_same_uuid_does_not_make_different_tools_the_same_fee_owner():
    payload = case()
    key = 'tudou-request-sha256:' + 'a' * 64
    payload['operations']['same'] = {'id': 'same', 'account_id': 'account',
        'empirical': {'fee_evidence': {'platform_log_id': key}}}
    payload['asset_design']['claim:' + key] = {'kind': 'claim', 'id': key, 'operation_id': 'same', 'owner': 'asset_design'}
    with pytest.raises(CreatorError):
        validate_shared_scopes(payload)
