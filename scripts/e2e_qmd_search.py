#!/usr/bin/env python3
"""Browser E2E + visual proof for the QMD search UI.

Environment:
  QMD_SEARCH_BASE_URL=http://127.0.0.1:8768
  QMD_VISUAL_PROOF_DIR=/tmp/qmd-visual-proof
  QMD_REDACT_SCREENSHOTS=1
  QMD_REQUIRE_REAL_FACETS=1
  CHROME_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
"""
import asyncio, json, os, sys, urllib.request
from pathlib import Path
try:
    from playwright.async_api import async_playwright
except Exception:
    print('Playwright is required: python3 -m pip install playwright && python3 -m playwright install chromium', file=sys.stderr)
    raise
BASE = os.environ.get('QMD_SEARCH_BASE_URL', 'http://127.0.0.1:8768').rstrip('/')
OUT = Path(os.environ.get('QMD_VISUAL_PROOF_DIR', '/tmp/qmd-visual-proof'))
REDACT = os.environ.get('QMD_REDACT_SCREENSHOTS', '') == '1'
REQUIRE_REAL = os.environ.get('QMD_REQUIRE_REAL_FACETS', '') == '1'
CHROME = os.environ.get('CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
def fetch_json(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode())
async def screenshot(page, name):
    if REDACT:
        await page.add_style_tag(content="#queryAutocomplete *, .avatar-dot, .result :not(button):not(summary), .result mark { color: transparent !important; text-shadow: 0 0 10px rgba(220,230,255,.85) !important; } .result mark { background: rgba(148,163,184,.35) !important; }")
    await page.screenshot(path=str(OUT / name), full_page=True)
async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    facets = fetch_json('/api/facets')
    fake = json.dumps(facets)
    assert 'U-DEMO' not in fake and 'C-DEMO' not in fake, 'fake demo facets leaked into /api/facets'
    users = len(facets.get('users') or [])
    channels = len(facets.get('channels') or [])
    if REQUIRE_REAL:
        assert users > 0 and channels > 0, {'users': users, 'channels': channels}
    async with async_playwright() as pw:
        launch = {'headless': True}
        if Path(CHROME).exists(): launch['executable_path'] = CHROME
        browser = await pw.chromium.launch(**launch)
        context = await browser.new_context(viewport={'width': 1600, 'height': 1000}, device_scale_factor=1, permissions=['clipboard-read', 'clipboard-write'])
        page = await context.new_page()
        events = []
        page.on('console', lambda msg: events.append({'type': 'console', 'level': msg.type, 'text': msg.text}))
        page.on('pageerror', lambda err: events.append({'type': 'pageerror', 'text': str(err)}))
        page.on('response', lambda resp: events.append({'type': 'response', 'url': resp.url, 'status': resp.status}) if '/api/' in resp.url or resp.url == BASE + '/' else None)
        await page.goto(BASE + '/', wait_until='networkidle')
        await screenshot(page, '01-initial.png')
        await page.fill('#query', 'user:')
        await page.wait_for_timeout(400)
        ac_text = await page.locator('#queryAutocomplete').inner_text()
        if users:
            assert 'No local user facets' not in ac_text and ac_text.strip(), ac_text
        else:
            assert 'No local user facets' in ac_text, ac_text
        assert 'U-DEMO' not in ac_text and 'C-DEMO' not in ac_text
        await screenshot(page, '02-autosuggest-user.png')
        await page.fill('#query', 'in:')
        await page.wait_for_timeout(400)
        channel_text = await page.locator('#queryAutocomplete').inner_text()
        if channels:
            assert 'No local channel facets' not in channel_text and channel_text.strip(), channel_text
        else:
            assert 'No local channel facets' in channel_text, channel_text
        await screenshot(page, '03-autosuggest-channel.png')
        await page.keyboard.press('Escape')
        search_mode = os.environ.get('QMD_E2E_SEARCH_MODE')
        if search_mode:
            await page.click(f'[data-mode="{search_mode}"]')
        await page.fill('#query', os.environ.get('QMD_E2E_QUERY', 'wiki'))
        async with page.expect_response(lambda r: '/api/search' in r.url, timeout=120000) as search_response:
            await page.click('button.search-submit')
        response = await search_response.value
        assert response.status == 200, response.status
        await page.wait_for_function("!document.querySelector('#status').textContent.includes('Searching')", timeout=120000)
        status = await page.locator('#status').inner_text()
        assert 'Error:' not in status, status
        await screenshot(page, '04-search-results.png')
        await page.click('[data-mode="lex"]'); assert await page.locator('#mode').input_value() == 'lex'
        await page.click('[data-mode="vec"]'); assert await page.locator('#mode').input_value() == 'vec'
        await page.click('[data-mode="hybrid"]'); assert await page.locator('#mode').input_value() == 'hybrid'
        await page.select_option('#limit', '10')
        await page.select_option('#sort', 'date-desc')
        await page.locator('input[name="collection"][value="raw"]').check()
        await page.locator('#slackWeight').evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
        assert '60%' in await page.locator('#slackWeightLabel').inner_text()
        await page.locator('#obsidianWeight').evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
        assert '40%' in await page.locator('#obsidianWeightLabel').inner_text()
        await page.click('[data-view="grid"]'); assert 'grid-results' in (await page.locator('#results').get_attribute('class') or '')
        await page.click('[data-view="list"]'); assert 'grid-results' not in (await page.locator('#results').get_attribute('class') or '')
        await page.click('#timelineView'); assert 'Timeline view' in await page.locator('#status').inner_text()
        if await page.locator('.facet-dropdown summary').count():
            await page.locator('.facet-dropdown summary').first.click()
            if await page.locator('button[data-facet-kind]').count():
                await page.locator('button[data-facet-kind]').first.click()
                assert await page.locator('.active-facets').inner_text()
        await page.locator('#telemetry summary').click(); await page.click('#refreshTelemetry'); await page.wait_for_timeout(300); assert await page.locator('#telemetryBody').inner_text()
        if await page.locator('.result').count():
            await page.locator('.result details.raw-toggle summary').first.click()
            await page.locator('.result button[data-action="json"]').first.click(); assert await page.locator('#docDialog').evaluate('el => el.open'); await page.keyboard.press('Escape')
            await page.locator('.result button[data-action="copy-uri"]').first.click(); await page.wait_for_timeout(100); assert 'Copied' in await page.locator('#status').inner_text()
            await page.locator('.result button[data-action="copy-snippet"]').first.click(); await page.wait_for_timeout(100); assert 'Copied' in await page.locator('#status').inner_text()
            await page.locator('.result button[data-action="copy-citation"]').first.click(); await page.wait_for_timeout(100); assert 'Copied' in await page.locator('#status').inner_text()
            await page.locator('.result button[data-action="open"]').first.click(); assert await page.locator('#docDialog').evaluate('el => el.open'); await page.keyboard.press('Escape')
        await page.click('#shareSearch'); await page.wait_for_timeout(100); assert 'Copied' in await page.locator('#status').inner_text()
        await page.click('#whyResults'); assert await page.locator('#docDialog').evaluate('el => el.open'); await page.keyboard.press('Escape')
        await page.click('#saveSearch'); assert await page.locator('#docDialog').evaluate('el => el.open')
        await page.fill('#saveSearchName', 'E2E saved search')
        await page.click('#confirmSaveSearch'); await page.wait_for_timeout(200)
        assert 'Saved search' in await page.locator('#status').inner_text()
        await page.click('[data-nav-action="saved"]'); assert await page.locator('#docDialog').evaluate('el => el.open')
        assert await page.locator('[data-run-saved]').count(); await page.locator('[data-copy-saved]').first.click(); await page.wait_for_timeout(100); assert 'Copied' in await page.locator('#status').inner_text()
        await page.locator('[data-delete-saved]').first.click(); await page.wait_for_timeout(100); await page.keyboard.press('Escape')
        await page.click('#historyButton'); assert await page.locator('#docDialog').evaluate('el => el.open')
        if await page.locator('[data-copy-history]').count(): await page.locator('[data-copy-history]').first.click(); await page.wait_for_timeout(100); assert 'Copied' in await page.locator('#status').inner_text()
        if await page.locator('[data-run-history]').count(): await page.locator('[data-run-history]').first.click(); await page.wait_for_timeout(300)
        if await page.locator('#docDialog').evaluate('el => el.open'): await page.keyboard.press('Escape')
        await page.click('#docsButton'); assert await page.locator('#docDialog').evaluate('el => el.open'); await page.keyboard.press('Escape')
        await page.click('#moreMenu'); assert await page.locator('#docDialog').evaluate('el => el.open')
        await page.click('[data-modal-action="help"]'); assert await page.locator('#docDialog').evaluate('el => el.open'); await page.keyboard.press('Escape')
        await page.click('[data-nav-action="settings"]'); assert await page.locator('#docDialog').evaluate('el => el.open'); await page.keyboard.press('Escape')
        await page.fill('#channel', 'nonexistent-channel-e2e')
        await page.fill('#user', 'nonexistent-user-e2e')
        await page.select_option('#includeDms', 'Include all DMs')
        await page.click('[data-range="7"]')
        await page.locator('details.side-section summary:has-text("Advanced")').click(); await page.fill('#within', 'wiki')
        await page.click('#clearFilters')
        assert await page.locator('#query').input_value() == ''
        assert (await page.locator('#results').inner_text()).strip() == ''
        await screenshot(page, '05-after-clear.png')
        errors = [e for e in events if e.get('type') == 'pageerror' or (e.get('type') == 'console' and e.get('level') == 'error') or (e.get('type') == 'response' and int(e.get('status', 0)) >= 400)]
        report = {'base': BASE, 'users': users, 'channels': channels, 'status': status, 'events': events, 'errors': errors, 'screenshots': sorted(p.name for p in OUT.glob('*.png'))}
        (OUT / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        assert not errors, errors
        await browser.close()
if __name__ == '__main__':
    asyncio.run(main())
