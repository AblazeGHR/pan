"""Versioned r26 delta; keep old assertions and red evidence intact."""
import copy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
prior_path = ROOT / 'evidence/job-retirement-rev25-20261031/current43-matrix.json'
prior = json.loads(prior_path.read_text(encoding='utf-8'))
matrix = copy.deepcopy(prior)
policy = json.loads((HERE / 'unknown-policy.command.json').read_text())
manual = json.loads((HERE / 'manual.command.json').read_text())
mixed = json.loads((HERE / 'mixed-runtime-then-manual.command.json').read_text())
for result in (policy, manual, mixed):
    assert result['exit'] == 0 and not any(result['counts'][k] for k in ('failed', 'error', 'skipped'))
    for kind in ('log', 'junit'):
        assert hashlib.sha256((HERE / result[kind]).read_bytes()).hexdigest() == result[kind + 'Sha256']
matrix.update(revision=26, sourceSha=policy['sourceSha'],
    historicalMatrix=str(prior_path.relative_to(ROOT)),
    historicalMatrixSha256=hashlib.sha256(prior_path.read_bytes()).hexdigest(),
    qqValidationSourceSha=mixed['sourceSha'],
    wholeMigrationAccepted=False, realMigrationPerformed=False)
row = matrix['originalNodes'][8]
row['previousDisposition'] = copy.deepcopy(prior['originalNodes'][8])
row.update(status='user-approved-unknown-api-fallback-cancelled-reject-preserve-verified',
    decision='job-unknown-api-reject-approved-20261031',
    authority='D:/project/Pan/.workflow/job-unification-review-20261028/UNKNOWN_API_POLICY_AMENDMENT_20261031.md',
    cancelledAssertionScope='Only force-writing unknown legacy action.api and expecting implicit assign; original red preserved.',
    matchedRetainedSemantics='Unknown legacy API returns explicit error. Canonical POST/PUT rejects before definition/operation writes. '
        'Unknown immutable Action ref rejects. Synthetic old source raw bytes and digest retained through inventory and cold startup/tick; no implicit assign, queue, Run, Attempt or effect.',
    strictSuccessors=[n['node'] for n in policy['executionOrder']],
    currentExecution=[{**n, 'sourceSha': policy['sourceSha'], 'command': 'unknown-policy.command.json'} for n in policy['executionOrder']],
    remaining='No unknown-api fallback decision remains. Known assign/send/QQ/shell capabilities are unaffected. Real data migration and full combined integration were not performed.')
matrix['remainingNine'] = [copy.deepcopy(row) if r['ordinal'] == 9 else r for r in matrix['remainingNine']]
matrix['rev26Execution'] = {
    'manual': manual, 'mixedRuntimeThenManual': mixed, 'unknownApiPolicy': policy,
    'totalExecutions': 44, 'distinctNodes': 36,
    'historicalNodesNotRerun': 'All other r25 evidence remains historical; no blanket 351 rerun.',
    'combinedAcceptance': False,
}
matrix['rev26PolicyScope'] = row['cancelledAssertionScope']
matrix['unverified'] = [v for v in matrix.get('unverified', [])
                        if v != 'legacy unknown Action fallback decision']
for previous, current in zip(prior['originalNodes'], matrix['originalNodes']):
    assert previous['originalAssertions'] == current['originalAssertions']
(HERE / 'current43-matrix.json').write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'originalNodes': len(matrix['originalNodes']), 'policyNodes': len(row['strictSuccessors']),
    'qqMixedPassed': mixed['counts']['passed'], 'originalAssertionsUnchanged': True}))
