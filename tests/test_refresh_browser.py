import unittest
from playwright.async_api import async_playwright, expect

class RefreshBrowser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        self.page=await self.browser.new_page(viewport={'width':1440,'height':1000})
        self.posts=[]; self.loads=[]
        self.providers=[dict(provider=p,name=n,scope=s,last_success_at=None,last_attempt_at=None,last_error=None,job=None) for p,n,s in [('jio','JioFiber','general'),('act','ACT Fibernet','Chennai'),('hathway','Hathway','Chennai')]]
        async def handler(route):
            if route.request.method=='POST':
                selected=route.request.post_data_json['provider']; self.posts.append(selected)
                for p in self.providers:
                    if selected in ('all',p['provider']):
                        p['job']=dict(job_id=p['provider']+'-fixture',status='queued',error=None,added=0,updated=1,unchanged=3,inactive=0)
                await route.fulfill(status=202,json={'status':'queued'}); return
            await route.fulfill(json=dict(providers=self.providers,schedule_enabled=False,interval_hours=24))
        await self.page.route('**/api/refresh',handler)
        async def act_plans(route):
            response=await route.fetch()
            payload=await response.json()
            if self.providers[1]['job'] and self.providers[1]['job']['status']=='completed':
                payload['data'][0]['price']+=10
            await route.fulfill(json=payload)
        await self.page.route('**/api/plans/act',act_plans)
        self.page.on('request',lambda r:self.loads.append(r.url) if '/api/plans/' in r.url else None)
        await self.page.goto('http://127.0.0.1:5173')
    async def asyncTearDown(self): await self.browser.close(); await self.pw.stop()

    async def test_status_reload_stale_warning_and_comparison_preserved(self):
        await self.page.get_by_label('Provider',exact=True).select_option('ACT Fibernet')
        card=self.page.locator('.bc-card').first
        await expect(card).to_be_visible()
        await card.get_by_role('button',name='Add to compare',exact=True).click()
        scope=self.page.get_by_role('region',name='ACT Fibernet refresh',exact=True)
        await scope.get_by_role('button',name='Refresh latest plans for ACT Fibernet').click()
        await expect(scope.get_by_role('status')).to_have_text('Queued')
        await expect(scope.get_by_role('button')).to_be_disabled()
        self.providers[1]['job']['status']='running'
        await expect(scope.get_by_role('status')).to_have_text('Running',timeout=6000)
        before=len([u for u in self.loads if u.endswith('/act')])
        self.providers[1]['job']['status']='completed'; self.providers[1]['last_success_at']='2026-10-26T00:00:00Z'
        await expect(scope.get_by_role('status')).to_have_text('Completed',timeout=6000)
        await expect(scope.get_by_text('0 added · 1 updated · 3 unchanged · 0 inactive')).to_be_visible()
        await self.page.wait_for_timeout(300)
        self.assertGreater(len([u for u in self.loads if u.endswith('/act')]),before)
        await expect(self.page.get_by_label('Provider',exact=True)).to_have_value('ACT Fibernet')
        await expect(self.page.get_by_role('button',name='Compare (1)',exact=True)).to_be_visible()
        self.providers[1]['job']['status']='failed'; self.providers[1]['last_error']='Fixture timeout'
        await expect(scope.get_by_role('alert')).to_contain_text('Saved data may be stale',timeout=6000)
        await expect(card).to_be_visible()

    async def test_refresh_all_prevents_duplicate_submissions(self):
        button=self.page.get_by_role('button',name='Refresh all providers',exact=True)
        await expect(button).to_be_enabled(); await button.click()
        await expect(button).to_be_disabled()
        self.assertEqual(self.posts,['all'])
        for p in self.providers:
            await expect(self.page.get_by_role('region',name=p['name']+' refresh',exact=True).get_by_role('status')).to_have_text('Queued')

if __name__=='__main__': unittest.main()
