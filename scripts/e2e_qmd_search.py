#!/usr/bin/env python3
"""Core E2E proof for the trimmed QMD search UI.

Environment:
  QMD_SEARCH_BASE_URL=http://127.0.0.1:8768
  QMD_VISUAL_PROOF_DIR=/tmp/qmd-visual-proof
  QMD_REDACT_SCREENSHOTS=1
  QMD_E2E_QUERY=wiki
  CHROME_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
"""
import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except Exception:
    print('Playwright is required: python3 -m pip install playwright && python3 -m playwright install chromium', file=sys.stderr)
    raise

BASE = os.environ.get('QMD_SEARCH_BASE_URL', 'http://127.0.0.1:8768').rstrip('/')
OUT = Path(os.environ.get('QMD_VISUAL_PROOF_DIR', '/tmp/qmd-visual-proof'))
REDACT = os.environ.get('QMD_REDACT_SCREENSHOTS', '') == '1'
CHROME = os.environ.get('CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
QUERY = os.environ.get('QMD_E2E_QUERY', 'wiki')


def fetch_json(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return json.loads(response.read().decode())


async def screenshot(page, name):
    if REDACT:
        await page.add_style_tag(content='.result pre, .path, datalist { color: transparent !important; text-shadow: 0 0 10px rgba(220,230,255,.85) !important; } mark { background: rgba(148,163,184,.35) !important; color: transparent !important; }')
    await page.screenshot(path=str(OUT / name), full_page=True)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    health = fetch_json('/health')
    assert health.get('ok') is True, health
    facets = fetch_json('/api/facets')
    assert isinstance(facets.get('users'), list), facets
    assert isinstance(facets.get('channels'), list), facets
    users = len(facets.get('users') or [])
    channels = len(facets.get('channels') or [])
    facet_blob = json.dumps(facets)
    assert 'U-DEMO' not in facet_blob and 'C-DEMO' not in facet_blob, 'fake demo facets leaked into /api/facets'

    api_search = fetch_json('/api/search?q=' + urllib.parse.quote(QUERY) + '&mode=lex&collection=all&n=5')
    assert 'results' in api_search, api_search
    assert api_search.get('returned', 0) >= 1, api_search

    async with async_playwright() as pw:
        launch = {'headless': True}
        if Path(CHROME).exists():
            launch['executable_path'] = CHROME
        browser = await pw.chromium.launch(**launch)
        context = await browser.new_context(viewport={'width': 1360, 'height': 920}, device_scale_factor=1, permissions=['clipboard-read', 'clipboard-write'])
        page = await context.new_page()
        events = []
        page.on('console', lambda msg: events.append({'type': 'console', 'level': msg.type, 'text': msg.text}))
        page.on('pageerror', lambda err: events.append({'type': 'pageerror', 'text': str(err)}))
        page.on('response', lambda resp: events.append({'type': 'response', 'url': resp.url, 'status': resp.status}) if '/api/' in resp.url or resp.url.endswith('/health') else None)

        await page.goto(BASE + '/', wait_until='networkidle')
        await screenshot(page, '01-initial.png')
        assert await page.locator('#query').is_visible()
        assert await page.locator('#telemetry').count() == 0
        assert await page.locator('#aiAssist').count() == 0
        assert await page.locator('.board-bar').count() == 0
        assert await page.locator('[data-nav-action="saved"]').count() == 0

        await page.click('#healthButton')
        await page.wait_for_function("document.querySelector('#status').textContent.includes('Service OK')", timeout=10000)

        if channels:
            await page.fill('#query', 'in:')
            await page.wait_for_function("document.querySelectorAll('#querySuggest button[data-suggest-index]').length > 0", timeout=10000)
            suggest_text = await page.locator('#querySuggest').inner_text()
            assert 'No matching channel suggestions' not in suggest_text, suggest_text
            await page.locator('#querySuggest button[data-suggest-index]').first.click()
            assert 'in:#' in await page.locator('#query').input_value()
            await page.fill('#query', '')
        if users:
            await page.fill('#query', 'user:')
            await page.wait_for_function("document.querySelectorAll('#querySuggest button[data-suggest-index]').length > 0", timeout=10000)
            user_suggest_text = await page.locator('#querySuggest').inner_text()
            assert 'No matching user suggestions' not in user_suggest_text, user_suggest_text
            await page.keyboard.press('Escape')
            await page.fill('#query', '')

        await page.select_option('#mode', 'lex')
        await page.select_option('#limit', '10')
        await page.select_option('#sort', 'relevance')
        await page.fill('#query', QUERY)
        async with page.expect_response(lambda response: '/api/search' in response.url, timeout=120000) as search_response:
            await page.click('button.primary')
        response = await search_response.value
        assert response.status == 200, response.status
        await page.wait_for_function("!document.querySelector('#status').textContent.includes('Searching')", timeout=120000)
        status = await page.locator('#status').inner_text()
        assert 'Error:' not in status, status
        assert await page.locator('.result').count() >= 1, status
        await screenshot(page, '02-results.png')

        await page.locator('.result button[data-action="json"]').first.click()
        assert await page.locator('#docDialog').evaluate('dialog => dialog.open')
        await page.click('#closeDialog')
        await page.locator('.result button[data-action="open"]').first.click()
        assert await page.locator('#docDialog').evaluate('dialog => dialog.open')
        await page.click('#closeDialog')
        await page.locator('.result button[data-action="copy-uri"]').first.click()
        await page.wait_for_timeout(200)
        copied_status = await page.locator('#status').inner_text()
        assert 'Copied' in copied_status or 'Clipboard unavailable' in copied_status, copied_status

        await page.fill('#within', 'unlikely-term-for-core-e2e')
        await page.click('#clearFiltersButton')
        assert await page.locator('#within').input_value() == ''
        await page.click('#clearButton')
        assert await page.locator('#query').input_value() == ''
        assert await page.locator('.result').count() == 0
        await screenshot(page, '03-cleared.png')

        errors = [event for event in events if event.get('type') == 'pageerror' or (event.get('type') == 'console' and event.get('level') == 'error') or (event.get('type') == 'response' and int(event.get('status', 0)) >= 400)]
        report = {'base': BASE, 'health': health, 'facet_counts': {'users': users, 'channels': channels}, 'status': status, 'events': events, 'errors': errors, 'screenshots': sorted(path.name for path in OUT.glob('*.png'))}
        (OUT / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        assert not errors, errors
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
