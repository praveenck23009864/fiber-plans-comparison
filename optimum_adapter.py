"""Adapted from optimum-browserbase/scrape_optimum.py; stops at quotes."""
import asyncio
import os
import re
import time
from urllib.parse import urlsplit
from browserbase import Browserbase
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from optimum_models import (PLAN_PATTERN, LookupFailure, parse_card, parse_address,
                            same_address, verified_address, page_failure)

CARD_SCRIPT = r'''heading => {
    const pattern = /^\d+(?:\.\d+)?\s*(?:Mbps|Gig|Gbps)\s+Internet$/i;
    let node = heading.parentElement;
    while (node && node !== document.body) {
        const text = node.innerText || '';
        const headings = [...node.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')]
            .filter(el => pattern.test((el.innerText || '').trim()));
        if (headings.length > 1) return '';
        if (/\$\s*[\d,.]+\s*\/\s*mo\.?/i.test(text)) return text;
        node = node.parentElement;
    }
    return '';
}'''

async def choices_from(locator):
    result = []
    for element in await locator.all():
        if not await element.is_visible():
            continue
        label = await element.evaluate("el => (el.labels && [...el.labels].map(l => l.innerText).join(' ')) || el.getAttribute('aria-label') || el.innerText || ''")
        label = ' '.join(label.split())
        if label:
            result.append((element, label, await element.get_attribute('id')))
    return result

async def choose(items, requested, ask, kind):
    if not items:
        raise LookupFailure('address_not_found', 'No usable address choices were returned.')
    candidate = parse_address(items[0][1]) if len(items) == 1 else None
    if candidate and same_address(requested, candidate) and kind != 'unit':
        index = 0
    else:
        index = await ask(kind, [{'label': label, 'service_location_id': sid if kind != 'address' else None}
                                 for _, label, sid in items])
    element, label, sid = items[index]
    parsed = parse_address(label)
    if parsed and not same_address(requested, parsed, allow_missing_unit=kind == 'address'):
        if requested.unit or not same_address(requested.model_copy(update={'unit': parsed.unit}), parsed):
            raise LookupFailure('address_mismatch', 'Selected address differs from the requested street, city, state, ZIP or unit. Start a corrected search.')
    return element, parsed, sid

async def navigate(page, address, ask):
    response = await page.goto('https://www.optimum.com/check-availability', wait_until='domcontentloaded', timeout=60000)
    if response and response.status in (401, 403, 429):
        raise LookupFailure('quota_exceeded' if response.status == 429 else 'access_denied', 'Optimum rejected the lookup request.')
    try:
        await page.get_by_role('button', name=re.compile(r'^(Opt Out|Reject Marketing Cookies)$', re.I)).click(timeout=3000)
    except PlaywrightTimeoutError:
        pass
    page_failure(await page.locator('body').inner_text())
    await page.get_by_role('combobox').fill(address.label())
    deadline = time.monotonic() + 30
    items = []
    while time.monotonic() < deadline:
        page_failure(await page.locator('body').inner_text())
        items = await choices_from(page.get_by_role('option'))
        if not items:
            items = await choices_from(page.locator('.pac-item'))
        if not items:
            pattern = re.compile(r'^\d+.+,\s*[^,]+,\s*[A-Z]{2}(?:[ ,]+\d{5}(?:-\d{4})?)?(?:,\s*USA)?$', re.I)
            items = await choices_from(page.get_by_text(pattern))
        if items:
            break
        await asyncio.sleep(.5)
    if not items:
        raise LookupFailure('timeout', 'Address suggestions did not arrive. Service availability is unknown.')
    element, _, _ = await choose(items, address, ask, 'address')
    await element.click()
    await page.get_by_role('button', name='Check availability', exact=True).click()
    selected, location_id = None, None
    handled = set()
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        body = await page.locator('body').inner_text()
        page_failure(body)
        headings = page.get_by_role('heading', name=PLAN_PATTERN)
        if '/buy-flow/list-quotes' in page.url and await headings.count() and await headings.first.is_visible():
            expected = address
            if selected and not address.unit and selected.unit:
                expected = address.model_copy(update={'unit': selected.unit})
            return verified_address(body, expected, selected), location_id
        residential = page.get_by_role('button', name='No, stay with residential services', exact=True)
        if await residential.is_visible():
            await residential.click()
            continue
        items = await choices_from(page.get_by_role('radio'))
        if items:
            fingerprint = tuple((label, sid) for _, label, sid in items)
            if fingerprint not in handled:
                kind = 'unit' if re.search(r'apartment|select.*unit', body, re.I) else 'service_location'
                started = time.monotonic()
                element, parsed, sid = await choose(items, address, ask, kind)
                deadline += time.monotonic() - started
                selected, location_id = parsed, sid
                await element.check()
                await page.get_by_role('button', name='Continue', exact=True).click()
                handled.add(fingerprint)
                continue
        select = page.locator('select:visible')
        if await select.count() == 1 and re.search(r'apartment|unit', body, re.I):
            options = await select.locator('option').evaluate_all("els => els.filter(e => e.value && !e.disabled).map(e => ({value:e.value,label:e.textContent.trim()}))")
            if options:
                index = await ask('unit', [{'label': o['label'], 'service_location_id': None} for o in options])
                unit = re.sub(r'^(?:unit|apt\.?|apartment)\s*', '', options[index]['label'], flags=re.I)
                if address.unit and address.unit.upper() != unit.upper():
                    raise LookupFailure('address_mismatch', 'Selected unit differs from the requested unit.')
                selected = address.model_copy(update={'unit': unit})
                await select.select_option(options[index]['value'])
                await page.get_by_role('button', name='Continue', exact=True).click()
                continue
        if not items and re.search(r'(?:enter|provide|select) (?:your |an? )?(?:apartment|unit)(?: number)?', body, re.I):
            raise LookupFailure('unit_required', 'Optimum requires an apartment or unit. Enter it in the form and start a new search.')
        await asyncio.sleep(.5)
    raise LookupFailure('timeout', 'Optimum did not confirm the address and return plans in time. Availability is unknown.')

async def extract(page):
    records = {}
    for heading in await page.get_by_role('heading', name=PLAN_PATTERN).all():
        if not await heading.is_visible():
            continue
        name = (await heading.inner_text()).strip()
        card = await heading.evaluate(CARD_SCRIPT)
        if not card:
            raise LookupFailure('extraction_failed', 'Could not isolate a plan card. No results were saved.')
        record = parse_card(name, card)
        if name in records and record != records[name]:
            raise LookupFailure('extraction_failed', 'Conflicting plan cards were returned. No results were saved.')
        records[name] = record
    if not records:
        raise LookupFailure('extraction_failed', 'No internet plan cards were found. Availability is unknown.')
    return list(records.values())

async def scrape(address, ask):
    key, project = os.getenv('BROWSERBASE_API_KEY'), os.getenv('BROWSERBASE_PROJECT_ID')
    if not key or not project:
        raise LookupFailure('not_configured', 'Backend Browserbase credentials are missing.')
    client = Browserbase(api_key=key, timeout=30, max_retries=0)
    session = None
    try:
        creation = asyncio.create_task(asyncio.to_thread(client.sessions.create, project_id=project, api_timeout=600))
        try:
            session = await asyncio.shield(creation)
        except asyncio.CancelledError:
            session = await creation
            raise
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(session.connect_url, timeout=60000)
            page = await browser.contexts[0].new_page()
            page.set_default_timeout(15000)
            try:
                verified, location_id = await navigate(page, address, ask)
                records = await extract(page)
                source = urlsplit(page.url)
                return {'verified_address': verified.model_dump(), 'service_location_id': location_id,
                        'plans': records, 'source_url': f'{source.scheme}://{source.netloc}{source.path}'}
            finally:
                await browser.close()
    except PlaywrightTimeoutError:
        raise LookupFailure('timeout', 'The browser timed out. Service availability is unknown.') from None
    except Exception as exc:
        if isinstance(exc, LookupFailure):
            raise
        status = getattr(exc, 'status_code', None)
        if status in (402, 429):
            raise LookupFailure('quota_exceeded', 'Browserbase quota or rate limit exceeded.') from None
        if status in (401, 403):
            raise LookupFailure('access_denied', 'Browserbase rejected backend access.') from None
        if type(exc).__name__ in ('APIConnectionError', 'APITimeoutError'):
            raise LookupFailure('connection_failed', 'The backend could not connect to Browserbase. Service availability is unknown.') from None
        import logging
        logging.getLogger(__name__).error('Optimum lookup failed (%s); no plans saved.', type(exc).__name__)
        raise LookupFailure('lookup_failed', 'The lookup failed technically. Service availability is unknown.') from None
    finally:
        if session is not None:
            try:
                await asyncio.to_thread(client.sessions.update, session.id, project_id=project, status='REQUEST_RELEASE')
            except Exception:
                import logging
                logging.getLogger(__name__).error('Browserbase release could not be confirmed; session TTL is 600 seconds.')
        await asyncio.to_thread(client.close)
