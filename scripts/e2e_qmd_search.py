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
LINK_PROOF = os.environ.get('QMD_E2E_LINK_PROOF', '') == '1'


LINK_PROOF_BODY = """---
channel_id: C-LINKS
channel_name: links
---
# Slack chunk fixture

## 2026-05-04T12:00:00+00:00 | Alice Linker | ts=1714824000.000100 thread_ts=1714824000.000100
Slack markdown link <https://example.com/docs?x=1&y=2|docs portal>, bare https://example.org/path?x=1&y=2, and mailto:help@example.com should be clickable.
Unsafe Slack link <javascript:alert(1)|bad <img src=x onerror=alert(1)>> and escaped label <https://example.net/safe|evil &lt;script&gt;alert(1)&lt;/script&gt;> must not execute.
"""


LINK_PROOF_RESULT = {
    'title': 'Thread in #links',
    'file': 'qmd://slack-api-chunks/link-proof.md',
    'path': 'qmd://slack-api-chunks/link-proof.md',
    'snippet': LINK_PROOF_BODY,
    'text': LINK_PROOF_BODY,
    'score': 1.0,
    'backend': 'fixture',
    'meta': {'kind': 'chunks', 'corpus': 'slack-api-chunks', 'channel': 'links', 'date': '2026-05-04'},
}


def fetch_json(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return json.loads(response.read().decode())


async def screenshot(page, name):
    if REDACT:
        await page.add_style_tag(content='.result pre, .result .excerpt, .path, .preview-source, .preview-snippet, .transcript-line, .transcript-card, datalist { color: transparent !important; text-shadow: 0 0 10px rgba(220,230,255,.85) !important; } mark { background: rgba(148,163,184,.35) !important; color: transparent !important; }')
    await page.screenshot(path=str(OUT / name), full_page=True)


async def assert_safe_external_link(locator, expected_text):
    attrs = await locator.evaluate("node => ({href: node.getAttribute('href'), target: node.getAttribute('target'), rel: node.getAttribute('rel'), text: node.textContent})")
    assert attrs['text'] == expected_text, attrs
    assert attrs['target'] == '_blank', attrs
    rel = attrs['rel'] or ''
    assert 'noopener' in rel and 'noreferrer' in rel, attrs


async def run_link_proof():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        launch = {'headless': True}
        if Path(CHROME).exists():
            launch['executable_path'] = CHROME
        browser = await pw.chromium.launch(**launch)
        context = await browser.new_context(viewport={'width': 1360, 'height': 920}, device_scale_factor=1)
        page = await context.new_page()
        await page.route('**/health', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok': True, 'service': 'fixture', 'maxResults': 500, 'root': 'fixture'})))
        await page.route('**/api/facets', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps({'users': [], 'channels': []})))
        await page.route('**/api/search?**', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps({'returned': 1, 'matched': 1, 'fetched': 1, 'elapsedMs': 1, 'results': [LINK_PROOF_RESULT]})))
        await page.route('**/api/get?**', lambda route: route.fulfill(status=200, content_type='text/plain; charset=utf-8', body=LINK_PROOF_BODY))

        await page.goto(BASE + '/', wait_until='networkidle')
        await page.fill('#query', 'links')
        async with page.expect_response(lambda response: '/api/search' in response.url, timeout=30000):
            await page.click('button.primary')
        await page.wait_for_function("document.querySelectorAll('.result .excerpt a').length >= 3", timeout=30000)
        await page.wait_for_function("document.querySelectorAll('#previewBody .chat-message .message a').length >= 3", timeout=30000)

        await assert_safe_external_link(page.locator('.result .excerpt a[href="https://example.com/docs?x=1&y=2"]').first, 'docs portal')
        await assert_safe_external_link(page.locator('#previewBody .chat-message .message a[href="https://example.com/docs?x=1&y=2"]').first, 'docs portal')
        await assert_safe_external_link(page.locator('.result .excerpt a[href="https://example.org/path?x=1&y=2"]').first, 'https://example.org/path?x=1&y=2')
        mailto_attrs = await page.locator('.result .excerpt a[href="mailto:help@example.com"]').first.evaluate("node => ({href: node.getAttribute('href'), text: node.textContent})")
        assert mailto_attrs == {'href': 'mailto:help@example.com', 'text': 'mailto:help@example.com'}, mailto_attrs
        assert await page.locator('a[href^="javascript:"]').count() == 0
        assert await page.locator('.result .excerpt img, #previewBody .message img, .result .excerpt script, #previewBody script').count() == 0
        escaped_label_html = await page.locator('#previewBody .chat-message .message').first.inner_html()
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in escaped_label_html, escaped_label_html
        await screenshot(page, 'link-proof.png')
        report = {'base': BASE, 'mode': 'link-proof', 'screenshots': sorted(path.name for path in OUT.glob('*.png'))}
        (OUT / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        await browser.close()


async def main():
    if LINK_PROOF:
        await run_link_proof()
        return
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

    transcript_api = fetch_json('/api/search?q=' + urllib.parse.quote('huddle') + '&mode=lex&collection=transcripts&n=3')
    assert 'results' in transcript_api, transcript_api
    if transcript_api.get('returned', 0):
        assert any((item.get('meta') or {}).get('kind') == 'transcripts' for item in transcript_api.get('results') or []), transcript_api

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
        assert await page.locator('#previewPanel').is_visible()
        assert await page.locator('#activeFilters').inner_text()
        assert await page.locator('#telemetry').count() == 0
        assert await page.locator('#aiAssist').count() == 0
        assert await page.locator('.board-bar').count() == 0
        assert await page.locator('[data-nav-action="saved"]').count() == 0

        await page.click('#healthButton')
        await page.wait_for_function("document.querySelector('#status').textContent.includes('Service OK')", timeout=10000)

        entity_probe = await page.evaluate("""
        () => {
            facetState = {
                users: [{name: 'Ada Lovelace', id: 'U12345', realName: 'Ada Lovelace', slackName: 'ada', count: 3}],
                channels: [{name: 'search-ui', id: 'C67890', count: 4}],
            };
            highlightTerms = [];
            selectedResultIndex = -1;
            lastResults = [{
                title: '#search-ui on 2026-05-04',
                file: 'qmd://slack-api-chunks/all-feeds/C67890/replies/1714800000-000100.md',
                score: 1,
                snippet: '## 2026-05-04T01:02:03+00:00 | Ada Lovelace | ts=1714800000.000100 thread_ts=1714800000.000100\\nHi <@U12345>, see <#C67890|search-ui> for context.',
                backend: 'fixture',
                meta: {corpus: 'Slack', kind: 'chunks', date: '2026-05-04', channel: 'search-ui', channelId: 'C67890', slackLinks: []},
            }];
            renderResults();
            renderPreview(lastResults[0], {loading: false, body: lastResults[0].snippet, error: ''});
            const cardUser = document.querySelector('.result .entity-chip[data-entity-type="user"][data-entity-id="U12345"]');
            const cardChannel = document.querySelector('.result .entity-chip[data-entity-type="channel"][data-entity-id="C67890"]');
            const previewUser = document.querySelector('#previewBody .entity-chip[data-entity-type="user"][data-entity-id="U12345"]');
            const previewChannel = document.querySelector('#previewBody .entity-chip[data-entity-type="channel"][data-entity-id="C67890"]');
            const channelLink = document.querySelector('#previewBody .entity-link[data-entity-type="channel"][href*="/archives/C67890"]');
            return {
                cardText: document.querySelector('.result .excerpt')?.textContent || '',
                previewText: document.querySelector('#previewBody')?.textContent || '',
                cardUser: Boolean(cardUser),
                cardChannel: Boolean(cardChannel),
                previewUser: Boolean(previewUser),
                previewChannel: Boolean(previewChannel),
                channelHref: channelLink?.href || '',
            };
        }
        """)
        assert entity_probe['cardUser'] and entity_probe['cardChannel'], entity_probe
        assert entity_probe['previewUser'] and entity_probe['previewChannel'], entity_probe
        assert '@U12345' not in entity_probe['cardText'] and '<#C67890' not in entity_probe['previewText'], entity_probe
        assert entity_probe['channelHref'].endswith('/archives/C67890'), entity_probe
        await page.locator('.result .entity-chip[data-entity-type="user"][data-entity-id="U12345"]').first.click()
        assert await page.locator('#user').input_value() == 'Ada Lovelace'
        await page.locator('.result .entity-chip[data-entity-type="channel"][data-entity-id="C67890"]').first.click()
        assert await page.locator('#channel').input_value() == 'search-ui'
        await page.click('#clearFiltersButton')
        await page.click('#clearButton')
        await page.evaluate("""facets => {
            facetState = {users: facets.users || [], channels: facets.channels || []};
            renderQuerySuggest();
            renderActiveFilters();
        }""", facets)

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
        if channels:
            known_channel = next((channel.get('name', '') for channel in facets.get('channels') or [] if channel.get('name')), '')
            if known_channel:
                await page.fill('#query', f'in:{known_channel} platform')
                await page.wait_for_function("document.querySelector('#activeFilters').textContent.includes('Channel: #')", timeout=10000)
                await page.fill('#query', '')

        await page.select_option('#mode', 'lex')
        await page.select_option('#limit', '10')
        await page.select_option('#sort', 'relevance')
        await page.select_option('#relativeTime', 'last-year')
        await page.fill('#query', 'platform')
        async with page.expect_response(lambda response: '/api/search' in response.url, timeout=120000) as relative_response:
            await page.click('button.primary')
        assert (await relative_response.value).status == 200
        await page.wait_for_function("!document.querySelector('#status').textContent.includes('Searching')", timeout=120000)
        relative_status = await page.locator('#status').inner_text()
        assert 'Error:' not in relative_status, relative_status
        assert 'Last year' in await page.locator('#activeFilters').inner_text()
        assert await page.locator('#resultsMeta').inner_text()
        assert 'found' in await page.locator('#resultTabs').inner_text()
        await page.click('#clearFiltersButton')

        if QUERY.lower() in {'huddle', 'standup', 'transcript', 'transcripts'}:
            await page.check('input[name="collection"][value="transcripts"]')
            await page.wait_for_function("document.querySelector('#activeFilters').textContent.includes('Transcripts')", timeout=10000)
        await page.fill('#query', QUERY)
        async with page.expect_response(lambda response: '/api/search' in response.url, timeout=120000) as search_response:
            await page.click('button.primary')
        response = await search_response.value
        assert response.status == 200, response.status
        await page.wait_for_function("!document.querySelector('#status').textContent.includes('Searching')", timeout=120000)
        status = await page.locator('#status').inner_text()
        assert 'Error:' not in status, status
        assert await page.locator('.result').count() >= 1, status
        await page.locator('.result').first.click()
        await page.wait_for_function("document.querySelector('#previewBody pre.preview-source, #previewBody .transcript-lines, #previewBody .chat-lines') !== null", timeout=120000)
        assert await page.locator('.result.selected').count() >= 1
        chips_text = await page.locator('#activeFilters').inner_text()
        assert ('Sources: Slack, Wiki' in chips_text) or ('Slack messages' in chips_text and 'Wiki' in chips_text), chips_text
        summary_text = await page.locator('#resultTabs').inner_text()
        assert 'found' in summary_text, summary_text
        if await page.locator('#resultTabs button[data-result-filter]').count():
            await page.locator('#resultTabs button[data-result-filter]').first.click()
            await page.wait_for_function("!document.querySelector('#status').textContent.includes('Searching')", timeout=120000)
            narrowed_chips = await page.locator('#activeFilters').inner_text()
            assert 'Channel:' in narrowed_chips or 'User:' in narrowed_chips, narrowed_chips
            assert await page.locator('.result').count() >= 1
        await screenshot(page, '02-results.png')

        await page.locator('.result details.result-more summary').first.click()
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

        if not await page.locator('#within').is_visible():
            await page.click('#moreFiltersButton')
        await page.fill('#within', 'unlikely-term-for-core-e2e')
        await page.wait_for_function("document.querySelector('#activeFilters').textContent.includes('Within: unlikely-term-for-core-e2e')", timeout=10000)
        await page.click('#clearFiltersButton')
        assert await page.locator('#within').input_value() == ''
        await page.click('#clearButton')
        assert await page.locator('#query').input_value() == ''
        assert await page.locator('.result').count() == 0
        assert await page.locator('#previewBody').inner_text()
        await screenshot(page, '03-cleared.png')

        errors = [event for event in events if event.get('type') == 'pageerror' or (event.get('type') == 'console' and event.get('level') == 'error') or (event.get('type') == 'response' and int(event.get('status', 0)) >= 400)]
        report = {'base': BASE, 'health': health, 'facet_counts': {'users': users, 'channels': channels}, 'status': status, 'events': events, 'errors': errors, 'screenshots': sorted(path.name for path in OUT.glob('*.png'))}
        (OUT / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        assert not errors, errors
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
