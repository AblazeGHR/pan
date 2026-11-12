"""Frozen practical features on the merged production bundle and owned HTTP."""
import json
from pathlib import Path

from test_legal_offline_http import runtime
from test_real_queue_http_e2e import _create, _wait_for


def test_production_search_and_select_use_real_session_endpoints(runtime):
    from playwright.sync_api import sync_playwright, expect
    out = Path('.tmp/practical-main/production')
    out.mkdir(parents=True, exist_ok=True)
    targets = [_create(runtime, 'merge-' + name) for name in ('Alpha', 'Beta', 'Gamma')]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append({'method': request.method, 'url': request.url}))
        try:
            page.goto(runtime.base_url + '/react/', wait_until='networkidle')
            page.get_by_title('App settings', exact=True).click()
            page.get_by_role('tab', name='Search', exact=True).click()
            field = page.get_by_role('searchbox', name='Search settings', exact=True)
            field.fill('illegal queue')
            hit = page.get_by_test_id('settings-search-results').get_by_role('button').first
            expect(hit).to_be_visible()
            hit.click()
            expect(page.get_by_role('tab', name='Behavior', exact=True)).to_have_attribute('aria-selected', 'true')
            expect(page.get_by_test_id('illegal-queue-behavior-section')).to_be_visible()
            # A same-tab field label and hint come from one description, and jump to the real switch.
            page.get_by_role('tab', name='Search', exact=True).click()
            field.fill('automatically lock queued turning this off')
            field_hit = page.get_by_test_id('settings-search-hit-app-settings-modal.110')
            expect(field_hit).to_be_visible()
            field_hit.click()
            control = page.locator('[data-settings-id="app-settings-modal.110"]')
            expect(control).to_have_attribute('role', 'switch')
            expect(control).to_be_visible()
            assert 'ring-2' in control.get_attribute('class')
            # The independent Search tab and its touch-scroll strip remain usable on a narrow viewport.
            page.set_viewport_size({'width': 320, 'height': 844})
            page.get_by_role('tab', name='Search', exact=True).click()
            expect(field).to_be_visible()
            assert page.locator('.app-settings-card').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.screenshot(path=str(out / 'search-behavior-desktop.png'))
            page.keyboard.press('Escape')

            first = page.locator(f'[data-session-card-id="{targets[0]["id"]}"]')
            first.hover()
            first.get_by_test_id('session-actions-menu').click()
            page.get_by_role('button', name='Select', exact=True).click()
            bar = page.locator('.sidebar-selection-bar')
            expect(bar.get_by_text('1 selected', exact=False)).to_be_visible()
            # Sweep the remaining visible cards; no route change or sort drag.
            others = [page.locator(f'[data-session-card-id="{t["id"]}"]') for t in targets[1:]]
            boxes = sorted([card.bounding_box() for card in others], key=lambda box: box['y'])
            url = page.url
            page.mouse.move(boxes[0]['x'] + boxes[0]['width'] * .6, boxes[0]['y'] + boxes[0]['height'] / 2)
            page.mouse.down()
            page.mouse.move(boxes[-1]['x'] + boxes[-1]['width'] * .6, boxes[-1]['y'] + boxes[-1]['height'] / 2, steps=20)
            page.mouse.up()
            expect(bar.get_by_text('3 selected', exact=False)).to_be_visible()
            assert page.url == url
            expect(page.locator('[data-drag-ghost]')).to_have_count(0)

            bar.get_by_role('button', name='Actions', exact=False).click()
            page.get_by_role('menuitem', name='Manage', exact=True).click()
            expect(page.get_by_test_id('bulk-manage-modal')).to_be_visible()
            assert not any(r['method'] == 'PATCH' and '/api/sessions/' in r['url'] for r in requests)
            page.get_by_label('Can claim unmanaged', exact=True).check()
            page.get_by_test_id('bulk-apply').click()
            expect(page.get_by_text('Updated 3 selected Sessions', exact=False)).to_be_visible()
            for target in targets:
                assert runtime.request('GET', '/api/sessions/' + target['id'])['panAccess']['canClaimUnmanaged'] is True

            bar.get_by_role('button', name='Actions', exact=False).click()
            page.get_by_role('menuitem', name='MsgBridge', exact=True).click()
            checkbox = page.get_by_label('msgBridge enabled', exact=True)
            checkbox.set_checked(False)
            page.get_by_test_id('bulk-apply').click()
            expect(page.get_by_text('Updated msgBridge for 3 selected Sessions', exact=False)).to_be_visible()
            for target in targets:
                _wait_for(lambda: runtime.request('GET', '/api/sessions/' + target['id'])['msgBridgeEnabled'] is False)
            page.screenshot(path=str(out / 'select-bulk-desktop.png'))
            assert not errors
            assert all(':8767/' not in r['url'] and ':8768/' not in r['url'] for r in requests)
            (out / 'real-http.json').write_text(json.dumps({'base': runtime.base_url, 'errors': errors,
                'search': 'real Behavior control', 'select': 'sweep and bulk Manage/msgBridge persisted',
                'requests': requests}, indent=2), encoding='utf-8')
        except BaseException:
            page.screenshot(path=str(out / 'failure.png'))
            (out / 'failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
            raise
        finally:
            browser.close()
