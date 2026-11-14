import asyncio,json,datetime
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 out=Path('/root/violet-release-audit/evidence')
 creds=dict(line.split('=',1) for line in Path('/root/paper-maintenance-preview-data/preview-auth.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
 result={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checks':[],'errors':[]}
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
  context=await browser.new_context(http_credentials={'username':creds['DASHBOARD_USER'],'password':creds['DASHBOARD_PASSWORD']},viewport={'width':1440,'height':1000},reduced_motion='reduce')
  page=await context.new_page()
  page.on('pageerror',lambda e: result['errors'].append(str(e)[:200]))
  await page.goto('https://preview.trade.187-52-118-25.sslip.io/aurora/?basic=1',wait_until='domcontentloaded')
  await page.wait_for_timeout(6000)
  result['title']=await page.title()
  result['buttons']=await page.get_by_role('button').all_text_contents()
  result['initial_text']=(await page.locator('body').inner_text())[:15000]
  await page.screenshot(path=str(out/'dashboard-desktop.png'),full_page=True)
  for label in ['Market Scanner','Portfolio Analytics','Risk Management','System Health','Binance Workspace','Solana Research','Global Overview']:
   matches=page.get_by_role('button',name=label,exact=True)
   if await matches.count()==1:
    await matches.click(); await page.wait_for_timeout(1000)
    result['checks'].append({'navigation':label,'body_excerpt':(await page.locator('body').inner_text())[:1200]})
  for label in ['1h', 'EMA 9/21', 'VWAP', 'BB(20)', 'macd']:
   matches=page.get_by_role('button',name=label,exact=True)
   if await matches.count()==1:
    await matches.click(); await page.wait_for_timeout(1500)
    result['checks'].append({'chart_control':label,'visible':await matches.is_visible()})
  result['canvas_count']=await page.locator('canvas').count()
  canvas=page.locator('canvas').first
  if await canvas.count():
   box=await canvas.bounding_box()
   if box:
    x,y=box['x']+box['width']/2,box['y']+box['height']/2
    await page.mouse.move(x,y);await page.mouse.wheel(0,-200)
    await page.mouse.down();await page.mouse.move(x+60,y,steps=10);await page.mouse.up()
    result['checks'].append({'chart_pan_zoom':'input_delivered','economic_correctness':'NOT_TESTED'})
  await page.screenshot(path=str(out/'dashboard-chart.png'),full_page=True)
  await page.set_viewport_size({'width':412,'height':915})
  await page.wait_for_timeout(1000)
  result['mobile_overflow']=await page.evaluate('document.documentElement.scrollWidth > innerWidth')
  await page.screenshot(path=str(out/'dashboard-mobile.png'),full_page=True)
  await browser.close()
 (out/'browser-results.json').write_text(json.dumps(result,indent=2))
 print(json.dumps({'title':result.get('title'),'errors':result['errors'],'navigation_checks':len(result['checks']),'mobile_overflow':result.get('mobile_overflow')},indent=2))
asyncio.run(main())
