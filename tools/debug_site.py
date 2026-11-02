import asyncio, json, sys
sys.path.insert(0, '.')
from playwright.async_api import async_playwright
from app.crawler import EXTRACT_JS, UA, load_sources
async def main():
    src = load_sources(); names = sys.argv[1:]
    async with async_playwright() as p:
        b = await p.chromium.launch(); ctx = await b.new_context(locale="ru-RU", user_agent=UA, ignore_https_errors=True, viewport={"width":1366,"height":900})
        for c in src['companies']:
            if c['name'] not in names: continue
            pg = await ctx.new_page()
            try:
                await pg.goto(c['url'], timeout=40000, wait_until="domcontentloaded"); await pg.wait_for_timeout(6000)
                items = await pg.evaluate(EXTRACT_JS, c['pattern'])
                links = await pg.eval_on_selector_all("a[href]", "els=>els.map(e=>e.href)")
                print("##", c['name'], pg.url[:80], "items", len(items), "links", len(links))
                for i in items[:3]: print("  ", i['title'][:60], i['url'][:90])
                import re
                for l in sorted(set(links))[:200]:
                    if re.search(r'vacanc|job|вакан', l, re.I): print("    L", l[:110])
            except Exception as e: print("##", c['name'], "ERR", str(e)[:150])
            await pg.close()
        await b.close()
asyncio.run(main())
