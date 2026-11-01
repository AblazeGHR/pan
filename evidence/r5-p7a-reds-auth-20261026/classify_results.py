"""Build identity-level classification from retained JUnit and successor inventory."""
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

out = Path(__file__).resolve().parent


def read_result(file):
    result = {}
    for test in ET.parse(out / file).iter('testcase'):
        identity = test.attrib['classname'] + '::' + test.attrib['name']
        error = test.find('failure')
        if error is None:
            error = test.find('error')
        result[identity] = {
            'status': 'failed' if error is not None else ('skipped' if test.find('skipped') is not None else 'passed'),
            'detail': error.attrib.get('message', '') if error is not None else '',
        }
    return result


def normalize(identity):
    module, name = identity.split('::', 1)
    return module.removesuffix('.py').replace('/', '.').replace('\\', '.') + '::' + name


def base(identity):
    return identity.split('[', 1)[0]


triage = read_result('triage-current.xml')
handoff = read_result('final.xml')
verification = read_result('verification.xml')
accepted = read_result('acceptance.xml')
qa = {item['id']: item for item in json.loads((out / 'qa-remaining.json').read_text(encoding='utf-8'))}
baseline = json.loads((out / 'baseline-summary.json').read_text(encoding='utf-8'))
absent = set(baseline['absentAtBaseline'])
inventory = json.loads((out / 'retirement-map.json').read_text(encoding='utf-8'))
mapping = {normalize(item['old']): item for item in inventory['retired']}
accepted_by_base = {}
for identity, value in accepted.items():
    accepted_by_base.setdefault(base(identity), []).append((identity, value))


def successors(identity):
    item = mapping.get(base(identity))
    if item:
        return item['successors']
    return [identity]


def coverage(identities):
    rows = []
    for identity in identities:
        normalized = normalize(identity)
        matches = [(normalized, accepted[normalized])] if normalized in accepted else accepted_by_base.get(base(normalized), [])
        assert matches, ('Uncollected successor', identity)
        assert all(value['status'] == 'passed' for _, value in matches), ('Nonpassing successor', identity)
        rows.extend(name for name, _ in matches)
    return sorted(set(rows))


all_ids = set(qa)
for result in (triage, handoff, verification):
    all_ids.update(name for name, value in result.items() if value['status'] == 'failed')
rows = []
for identity in sorted(all_ids):
    spec = mapping.get(base(identity))
    before = triage.get(identity, {})
    if spec:
        category = 'legacy_protocol_rewritten_or_retired'
        reason = spec['reason']
    elif ('test_plugin_events::' in identity or 'test_plugin_event_lifecycle::' in identity
          or 'test_job_final_clients::' in identity):
        category = 'legacy_protocol_rewritten_or_retired'
        reason = 'The real plugin/MCP transport now uses canonical pages, grants, Trigger admission and Job Run state; retained history never becomes an execution host.'
    elif 'test_job_lifecycle_prevention::' in identity:
        category = 'legacy_protocol_rewritten_or_retired'
        reason = 'P5b startup grace is asserted before advancing a controlled clock; then orphan reconciliation, dead owned process tree and no marker execution remain required.'
    elif 'test_scheduler_time_events::test_broadcast_' in identity:
        category = 'legacy_protocol_rewritten_or_retired'
        reason = 'Independent subscriptions have no effect-order promise: preserve both names, exactly one failed and one completed, immutable Event payload and no redelivery effects.'
    elif 'test_locker_order_scroll_live::' in identity or 'test_user_cards_live::' in identity:
        category = 'environment_dependency_repaired'
        reason = 'Installed locked Node dependencies from the offline cache and built the checkout production bundle; original browser test and assertions pass unchanged.'
    elif 'test_event_store_http::' in identity:
        category = 'product_or_fixture_defect_fixed'
        reason = 'Install Automation routes/middleware during fixture construction before TestClient makes its first request; HTTP replay and original Run identity assertions remain intact.'
    elif 'test_mcp_handbook.TestDescriptionCallChain::' in identity:
        category = 'product_or_fixture_defect_fixed'
        reason = 'Integration handoff already passed; the temporary canonical tool-description regression was corrected and the handbook pointer contract reverified.'
    elif before.get('status') == 'passed' and identity in qa:
        category = 'integrated_fix_reverified'
        reason = 'Already passed in the bb675a69 handoff triage. Reverified unchanged identity in the final suite; no claim that P7a implemented the earlier integrated fix.'
    else:
        raise AssertionError(('Unclassified identity', identity))
    successor_ids = successors(identity)
    verified = coverage(successor_ids)
    rows.append({
        'id': identity, 'qaRemaining71': identity in qa,
        'category': category, 'reason': reason,
        'triageStatus': before.get('status', 'not_collected'),
        'triageDetail': before.get('detail', ''),
        'handoffStatus': handoff.get(identity, {}).get('status', 'not_collected'),
        'finalStatus': accepted.get(identity, {}).get('status', 'retired_with_verified_successor'),
        'baseline7f303faa': ('absent' if identity in absent else 'same_identity_passed') if identity in qa else 'no_baseline_defect_claim',
        'successors': successor_ids, 'verifiedSuccessorCases': verified,
    })

for item in inventory['retired']:
    coverage(item['successors'])
assert len(qa) == 71
assert all(value['status'] == 'passed' for value in accepted.values())
summary = {
    'qa71': dict(Counter(row['category'] for row in rows if row['qaRemaining71'])),
    'allTrackedIdentities': len(rows),
    'allTrackedCategories': dict(Counter(row['category'] for row in rows)),
    'retiredOrRewrittenDefinitions': len(inventory['retired']),
    'triage': dict(Counter(value['status'] for value in triage.values())),
    'handoff': dict(Counter(value['status'] for value in handoff.values())),
    'expandedVerification': dict(Counter(value['status'] for value in verification.values())),
    'finalAcceptance': dict(Counter(value['status'] for value in accepted.values())),
    'unclassified': [], 'remainingNewFailures': [],
    'allSuccessorsExecutedAndPassed': True,
    'baselineQaSameIdentityPassed': baseline['sameIdentityPassed'],
    'baselineAbsent': len(absent), 'baselineExistingProductFailures': 0,
}
(out / 'classification.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')
(out / 'classification-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
