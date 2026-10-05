"""Negative controls for the diagnosed S0 unordered-set serialization issue."""
import copy
from staff_golden.parity_compare import evidence_compare


def fixture():
    return {'oz_state': {'trigger': {'type': 'set', 'value': ['DI', 'STO']}},
            'telegram': [[1, 'offline', 'alert']]}


def test_only_declared_set_order_is_unordered():
    old, new = fixture(), fixture()
    new['oz_state']['trigger']['value'].reverse()
    result = evidence_compare(old, new)
    assert result['equal'] and not result['raw_hash_equal']
    for kind in ('list', 'tuple', 'dict'):
        a, b = copy.deepcopy(old), copy.deepcopy(new)
        a['oz_state']['trigger']['type'] = b['oz_state']['trigger']['type'] = kind
        assert not evidence_compare(a, b)['equal']


def test_members_multiplicity_type_and_alerts_still_fail():
    for mutation in ('member', 'duplicate', 'kind', 'alert'):
        old, new = fixture(), fixture()
        if mutation == 'member': new['oz_state']['trigger']['value'][0] = 'RSI'
        if mutation == 'duplicate': new['oz_state']['trigger']['value'].append('DI')
        if mutation == 'kind': new['oz_state']['trigger']['type'] = 'frozenset'
        if mutation == 'alert': new['telegram'][0][2] = 'changed'
        assert not evidence_compare(old, new)['equal']


def test_plain_arrays_and_set_shaped_alert_payloads_remain_ordered():
    old = {'oz_state': ['DI', 'STO'], 'telegram': {'type': 'set', 'value': ['a', 'b']}}
    new = copy.deepcopy(old)
    new['oz_state'].reverse()
    assert not evidence_compare(old, new)['equal']
    new = copy.deepcopy(old)
    new['telegram']['value'].reverse()
    assert not evidence_compare(old, new)['equal']
