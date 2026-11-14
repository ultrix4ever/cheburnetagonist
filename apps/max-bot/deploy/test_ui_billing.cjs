// Mock-API browser check of the billing parts of «Пользователи MAX». Usage: node test_ui_billing.cjs DIST_DIR OUT_DIR
const { chromium } = require('playwright');
const fs = require('fs'), http = require('http'), path = require('path'), assert = require('assert');
(async () => {
  const [root, out] = process.argv.slice(2);
  const server = http.createServer((req, res) => { const p = path.join(root, req.url === '/' ? 'index.html' : req.url.split('?')[0]); fs.readFile(p, (e, b) => { if (e) { res.writeHead(404); return res.end() } res.setHeader('Content-Type', p.endsWith('.js') ? 'application/javascript' : p.endsWith('.css') ? 'text/css' : p.endsWith('.html') ? 'text/html' : 'application/octet-stream'); res.end(b) }) });
  await new Promise(r => server.listen(18093, '127.0.0.1', r));
  const browser = await chromium.launch({ headless: true });
  const t = Math.floor(Date.now() / 1000), D = 86400;
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } }); const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('acontrol_token', 'test-only'));
    const snapshot = {
      people: [{ id: 1, name: 'Тестовый пользователь', max_id: '87149564', active: 1, free: false, phone: '+79531234567', phone_verified: true, registered: t - D, quota: 5, limit: 3, used: 3, legacy: 4, allowed_servers: [1, 3],
                 grants: [{ id: 7, slots: 3, starts: t - 5 * D, expires: t + 25 * D, source: 'payment' }] },
               { id: 2, name: 'Owner', max_id: '100000001', active: 1, free: true, phone_verified: false, registered: null, quota: 5, limit: 5, used: 1, legacy: 0, phone: '', allowed_servers: [1, 3], grants: [] }],
      profiles: [{ id: 1, server_id: 1, name: 'Телефон', person_id: 1, state: 'active', has_config: true, billing_state: null, suspended_at: null, legacy: 0 },
                 { id: 2, server_id: 3, name: 'Ноутбук', person_id: 1, state: 'paused', has_config: true, billing_state: 'suspended', suspended_at: t - 2 * D, legacy: 0 },
                 { id: 4, server_id: 1, name: 'Старый', person_id: 1, state: 'active', has_config: false, billing_state: null, suspended_at: null, legacy: 1 },
                 { id: 3, server_id: 1, name: 'Роутер', person_id: 2, state: 'revoked', has_config: true, billing_state: null, suspended_at: null, legacy: 0 }],
      servers: [{ id: 1, name: 'Германия', online: true }, { id: 3, name: 'Хельсинки', online: true }],
      replacements: [], jobs: [], audit: [], operations: [], billing: { price: 100, period_days: 30, max_slots: 20 },
      payments: [{ id: 'VPN-123456', max_id: '87149564', person_id: 1, slots: 3, amount: 300, state: 'submitted', receipt_mid: 'mid', grant_id: null, created: t - 600, decided: null },
                 { id: 'VPN-654321', max_id: '87149564', person_id: 1, slots: 3, amount: 300, state: 'confirmed', receipt_mid: null, grant_id: 7, created: t - 5 * D, decided: t - 5 * D }],
    };
    const writes = [];
    await page.route('**/api/**', async route => {
      const req = route.request(), u = new URL(req.url()); let body = {}, status = 200;
      if (u.pathname === '/api/max-users' && req.method() === 'GET') body = snapshot;
      else if (u.pathname.startsWith('/api/max-users/')) { writes.push([req.method(), u.pathname, req.postDataJSON()]); body = { id: 1, result: 'ok' } }
      else if (u.pathname === '/api/config') body = { version: 'test' };
      else if (u.pathname === '/api/auth/2fa') body = { enabled: false };
      else if (['/api/servers', '/api/cascades', '/api/stats/nodes'].includes(u.pathname)) body = [];
      else if (u.pathname === '/api/stats/overview') body = { per_server: [] };
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    });
    const step = async (name, password = 'pw') => { await page.locator('input[type=password]').fill(password); await page.locator('input[type=password]').press('Enter'); await page.waitForTimeout(300) };
    await page.goto('http://127.0.0.1:18093');
    await page.getByRole('button', { name: 'Пользователи MAX', exact: true }).click();
    await page.getByRole('heading', { name: 'Платежи, ждущие решения' }).waitFor();
    assert(await page.getByText('3 ключа до ', { exact: false }).first().isVisible(), 'grant summary missing');
    assert(await page.getByText('+ старых без оплаты: 4').isVisible());
    await page.getByRole('button', { name: 'Подтвердить', exact: true }).click(); await step();
    const confirm = writes.find(w => w[1] === '/api/max-users/payments/VPN-123456/confirm');
    assert(confirm && confirm[2].password === 'pw', 'payment confirm request not sent');
    await page.getByRole('button', { name: 'Тестовый пользователь', exact: true }).click();
    await page.getByRole('button', { name: 'Добавить', exact: true }).waitFor();
    assert(await page.getByText(/Остановлен за неоплату с/).isVisible());
    assert(await page.getByText('Старый, без оплаты').isVisible());
    await page.getByLabel('Добавить оплаченные ключи вручную').fill('2');
    await page.getByRole('button', { name: 'Добавить', exact: true }).click(); await step();
    const grant = writes.find(w => w[1] === '/api/max-users/people/1/grant');
    assert(grant && grant[2].slots === 2, 'manual grant not sent');
    await page.getByRole('button', { name: 'Аннулировать', exact: true }).click(); await step();
    assert(writes.find(w => w[1] === '/api/max-users/grants/7/cancel'), 'grant cancel not sent');
    assert(await page.getByText(/\+79531234567 ✓ · сам,/).isVisible(), 'verified phone / self-registration not shown');
    await page.getByRole('button', { name: 'Переименовать' }).first().click();
    const input = page.locator('td form input').first(); await input.fill('Ноутбук Маши');
    await page.locator('td form').first().getByRole('button', { name: 'Сохранить' }).click(); await step();
    const rename = writes.find(w => w[1] === '/api/max-users/profiles/1/rename');
    assert(rename && rename[2].name === 'Ноутбук Маши' && rename[2].password === 'pw', 'rename not sent');
    assert(await page.getByRole('button', { name: /Удалить пользователя \(сначала отзовите ключи: 3\)/ }).isDisabled(), 'delete must be blocked while keys live');
    await page.getByRole('button', { name: 'Owner', exact: true }).click();
    const del = page.getByRole('button', { name: 'Удалить пользователя', exact: true });
    assert(await del.isEnabled()); await del.click(); await step();
    assert(writes.find(w => w[1] === '/api/max-users/people/2/delete'), 'delete not sent');
    await page.getByRole('button', { name: 'Тестовый пользователь', exact: true }).click();
    await page.screenshot({ path: path.join(out, 'billing-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: 'Изменить' }).first().click();
    const free = page.getByLabel('Бесплатный аккаунт — ключи без оплаты');
    assert.equal(await free.isChecked(), false); await free.check();
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click(); await step();
    const save = writes.find(w => w[0] === 'PUT');
    assert(save && save[2].free === true && !('max_id' in save[2]), 'free flag not saved');
    assert.deepEqual(errors, []);
    console.log('billing UI ok;', writes.length, 'writes');
  } finally { await browser.close(); server.close() }
})().catch(e => { console.error(e); process.exit(1) });
