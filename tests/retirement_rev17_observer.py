"""Read-only failure diagnostics for unchanged owned QQ tests."""
import json
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != 'call' or not report.failed:
        return
    q = item.funcargs.get('qq_http')
    if q is None:
        return
    states = []
    with q.h.service.transaction(readonly=True) as db:
        for job in q.h.service._all(db, 'job'):
            states.append(q.h.service.final.get(job['jobId']))
    print('R17_UNCHANGED_QQ_FAILURE=' + json.dumps({
        'node': item.nodeid, 'externalRequests': q.rows,
        'states': states}, ensure_ascii=True))
