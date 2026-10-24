/* Browser-level smoke test: runs the real SPA (frontend/static/script.js) in jsdom against LIVE servers.
 *
 *   1. start PostgreSQL, the API (:8000) and the Flask front door (:5000) against a THROW-AWAY database that has
 *      users dba / developer / analyst with the passwords used below (e.g. right after `pytest`).
 *      Start the API with OLLAMA_BASE_URL=http://127.0.0.1:11434 and make sure no real Ollama is running on that port:
 *      this script starts a tiny stand-in model there (after first checking the "Ollama is down" message).
 *   2. cd tests/ui && npm install && node ui_smoke.js
 *
 * The stand-in model only returns canned SQL templates; attach → expansion → validation → execution → export are real.
 * Re-running needs seed salaries restored for EMP006/EMP007 only if you care about the data.
 * Tailwind/fonts are stubbed because jsdom has no layout engine; "tailwind is not defined" in the log is expected.
 */
const { JSDOM, requestInterceptor, VirtualConsole, CookieJar } = require('jsdom');
const fs = require('fs');
const BASE = 'http://localhost:5000';
const errors = [];
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log('  ✓', m); } else { fail++; console.log('  ✗ FAIL:', m); } };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const http = require('http');
let fakeOllama = null;
/* Stands in for the model ONLY in this UI test (no GPU/model needed). Everything after it is the real pipeline. */
function startFakeOllama() {
  fakeOllama = http.createServer((req, res) => {
    if (req.url.startsWith('/api/tags')) { res.setHeader('Content-Type', 'application/json'); return res.end(JSON.stringify({ models: [{ name: 'qwen2.5:3b' }] })); }
    let body = ''; req.on('data', c => body += c); req.on('end', () => {
      const user = (JSON.parse(body).messages || [])[1]?.content || ''; const q = (user.match(/Request: ([^\n]*)/g) || []).pop() || '';
      let reply = 'CLARIFY: Please be more specific.';
      if (/ATTACHED FILE/.test(user) && /update/i.test(q)) reply = '```sql\nUPDATE employees SET salary = {{salary}} WHERE employee_code = {{employee_code}};\n```';
      else if (/ATTACHED FILE/.test(user) && /remove|delete/i.test(q)) reply = '```sql\nDELETE FROM employees WHERE employee_code = {{employee_code}};\n```';
      else if (/departments and count/i.test(q)) reply = '```sql\nSELECT * FROM departments;\nSELECT COUNT(*) AS n FROM employees;\n```';
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ message: { content: reply } }));
    });
  }).listen(11434, '127.0.0.1');
}

const interceptor = requestInterceptor(req => {      // no CDN / fonts in the sandbox → stub them out
  if (!req.url.startsWith(BASE)) return new Response('', { status: 200, headers: { 'Content-Type': 'text/plain' } });
});

async function newBrowser() {
  const jar = new CookieJar();
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.detail?.stack || e.message)));
  vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));
  const dom = await JSDOM.fromURL(BASE + '/', { runScripts: 'dangerously', resources: { interceptors: [interceptor] }, cookieJar: jar, virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = async (path, o = {}) => {
    const url = new URL(path, BASE).href;
    const headers = { ...(o.headers || {}) };
    const cookie = jar.getCookieStringSync(url); if (cookie) headers.Cookie = cookie;
    let body = o.body;
    if (body && body.constructor && body.constructor.name === 'FormData') {
      const fd = new FormData();
      for (const [k, v] of body.entries()) {
        if (typeof v === 'string') fd.append(k, v);
        else { const buf = await new Promise(res => { const fr = new w.FileReader(); fr.onload = () => res(Buffer.from(fr.result)); fr.readAsArrayBuffer(v); }); fd.append(k, new Blob([buf]), v.name); }
      }
      body = fd;
    }
    const res = await fetch(url, { method: o.method || 'GET', headers, body, redirect: 'manual' });
    for (const c of res.headers.getSetCookie()) jar.setCookieSync(c, url);
    return res;
  };
  w.navigator.clipboard = { writeText() {} };
  await sleep(600);
  return { dom, w, d: w.document, jar };
}
const waitFor = async (fn, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (fn()) return true; } catch {} await sleep(100); } return false; };
const txt = d => d.body.textContent.replace(/\s+/g, ' ');

async function login(b, user, pw) {
  await waitFor(() => b.d.querySelector('#login-form'));
  b.d.querySelector('#username').value = user; b.d.querySelector('#password').value = pw;
  b.d.querySelector('#login-form').dispatchEvent(new b.w.Event('submit', { cancelable: true, bubbles: true }));
}

(async () => {
  console.log('\n[1] login screen & failed login');
  let b = await newBrowser(), d = b.d, w = b.w;
  const fl = await waitFor(() => d.querySelector('#login-form')); ok(fl, 'login form rendered');
  ok(/Database online/.test(txt(d)), 'shows real database health');
  await login(b, 'developer', 'wrong-password-1');
  ok(await waitFor(() => /Invalid username or password/.test(txt(d))), 'wrong password shows backend message');

  console.log('\n[2] developer session');
  await login(b, 'developer', 'DevPassw0rd!x');
  ok(await waitFor(() => d.querySelector('#nav')), 'shell rendered after login');
  ok(/Database Developer/.test(txt(d)), 'role label shown');
  ok(/My Change Requests/.test(txt(d)) && !/Audit Logs/.test(txt(d)), 'developer nav: has My Change Requests, no Audit Logs');
  ok(await waitFor(() => /PostgreSQL · ai_database_test\s*Online/.test(txt(d))), 'DB status pill is live');
  ok(await waitFor(() => /Offline/.test(d.querySelector('#pill-ai').textContent)), 'AI pill honestly shows Ollama offline');

  console.log('\n[3] generate with Ollama down → friendly message');
  d.querySelector('#nl').value = 'Show all employees'; d.querySelector('#nl').dispatchEvent(new w.Event('input'));
  d.querySelector('#gen-btn').click();
  ok(await waitFor(() => /AI service is unavailable\. Please start Ollama\./.test(txt(d))), 'exact AI-unavailable message displayed');

  console.log('\n[4] developer SELECT through validate → execute');
  const setSql = async (sql, op) => {
    w.eval(`state.studio.nl='test request'; state.studio.sql=${JSON.stringify(sql)}; state.studio.gen={valid:true,operation_type:'${op}',tables_considered:['employees']}; state.studio.error=null; state.studio.result=null; state.studio.ticket=null; studioRender();`);
    await w.eval('revalidate()');
  };
  await setSql('SELECT employee_code, name, salary FROM employees ORDER BY employee_code LIMIT 3', 'SELECT');
  ok(await waitFor(() => /Ready — review before executing/.test(txt(d))), 'valid SELECT shows "Ready"');
  d.querySelector('#run-btn').click();
  ok(await waitFor(() => d.querySelectorAll('.data-table tbody tr').length === 3), 'result table has 3 real rows');
  ok(/EMP001/.test(txt(d)), 'result contains EMP001');

  console.log('\n[5] developer UPDATE → DBA approval required, nothing executes');
  await setSql("UPDATE employees SET salary = 61234 WHERE employee_code = 'EMP006'", 'UPDATE');
  ok(await waitFor(() => /DBA Approval Required/.test(txt(d))), 'status shows DBA Approval Required');
  d.querySelector('#run-btn').click();
  ok(await waitFor(() => /Ticket #\d+/.test(txt(d)) && /PENDING/.test(txt(d))), 'ticket created, status PENDING');
  const ticketId = /Ticket #(\d+)/.exec(txt(d))[1];

  console.log('\n[6] developer: forbidden / invalid SQL');
  await setSql('SELECT * FROM users', 'SELECT');
  ok(await waitFor(() => /Validation failed/.test(txt(d))), 'users table blocked in validation UI');
  ok(d.querySelector('#run-btn').disabled, 'Proceed button disabled for invalid SQL');

  console.log('\n[7] developer cannot see DBA screens');
  w.eval("go('audit')");
  ok(await waitFor(() => /permission to perform this operation/.test(txt(d))), 'audit view → backend 403 message');
  w.eval("go('approvals')");
  ok(await waitFor(() => /My Change Requests/.test(txt(d)) && /Waiting for DBA review/.test(txt(d))), 'developer sees own pending request');
  d.querySelector('#logout').click();
  ok(await waitFor(() => d.querySelector('#login-form')), 'logout returns to login');

  console.log('\n[8] DBA approves');
  await login(b, 'dba', 'DbaPassw0rd!x');
  ok(await waitFor(() => d.querySelector('#nav')), 'DBA shell rendered');
  ok(/Pending Approvals/.test(txt(d)) && /Audit Logs/.test(txt(d)), 'DBA nav has approvals + audit');
  ok(await waitFor(() => d.querySelector('#bell-dot') && !d.querySelector('#bell-dot').classList.contains('hidden')), 'notification bell shows unread count');
  d.querySelector('#bell').click();
  ok(await waitFor(() => /New Database Change Request/.test(txt(d))), 'notification text present');
  w.eval("go('approvals')");
  ok(await waitFor(() => d.querySelector('#app-btn')), 'approval ticket detail rendered with Approve button');
  ok(/UPDATE/.test(txt(d)) && /61234/.test(txt(d)) && /test request/.test(txt(d)), 'ticket shows operation, SQL and NL request');
  ok(!!d.querySelector('#rej-btn'), 'Reject button present');
  d.querySelector('#app-btn').click();
  ok(await waitFor(() => /Nothing awaiting review|Executed/.test(txt(d)), 10000), 'queue updates after approval');

  console.log('\n[9] audit view');
  w.eval("go('audit')");
  ok(await waitFor(() => d.querySelectorAll('.data-table tbody tr').length > 5), 'audit table populated');
  ok(/APPROVAL_APPROVED/.test(txt(d)) && /SQL_EXECUTED/.test(txt(d)), 'audit contains approval + execution entries');

  console.log('\n[10] schema + settings');
  w.eval("go('schema')");
  ok(await waitFor(() => /employees/.test(txt(d)) && /audit_logs/.test(txt(d))), 'DBA sees schema incl. audit_logs');
  ok(!/password_hash/.test(txt(d)), 'users table / password_hash never shown');
  w.eval("go('settings')");
  ok(await waitFor(() => /Role permission matrix/.test(txt(d)) && /DBA approval/.test(txt(d))), 'permission matrix rendered');

  console.log('\n[11] Excel Batch Upload section is gone');
  ok(!/Excel Batch Upload/.test(txt(d)) && !d.querySelector('[data-nav="excel"]'), 'no Excel Batch Upload in navigation');
  w.eval("go('employees')");
  ok(await waitFor(() => /Employee Directory/.test(txt(d))) && !/Upload spreadsheet|Download template/.test(txt(d)), 'directory has no upload/template buttons');

  console.log('\n[12] attach a file in Natural Language Intent → "update" runs for every row');
  startFakeOllama(); await sleep(300);
  w.eval("go('studio')");
  ok(await waitFor(() => d.querySelector('#attach-btn')), 'Attach file button present in the intent panel');
  const csv = new w.File(['employee_code,salary\nEMP006,61000\nEMP007,39000\n'], 'salary_update.csv');
  const inp = d.querySelector('#attach-file');
  Object.defineProperty(inp, 'files', { value: [csv], configurable: true });
  inp.dispatchEvent(new w.Event('change'));
  ok(await waitFor(() => /salary_update\.csv/.test(txt(d)) && /2 rows/.test(txt(d))), 'attachment chip shows file name and row count');
  ok(/Update the employees in the file/.test(txt(d)), 'file-specific example prompts shown');
  d.querySelector('#attach-preview').click();
  ok(await waitFor(() => /EMP007/.test(d.querySelector('#modal-root').textContent)), 'preview modal lists the file rows');
  d.querySelector('#im-close').click();
  d.querySelector('#nl').value = 'Update the salary of the employees in the file'; d.querySelector('#nl').dispatchEvent(new w.Event('input'));
  d.querySelector('#gen-btn').click();
  ok(await waitFor(() => /Runs once for each of the 2 rows/.test(txt(d)), 10000), 'AI template expanded: "Runs once for each of the 2 rows"');
  ok(/UPDATE employees SET salary = '?61000'? WHERE employee_code = 'EMP006'/.test(txt(d)) && /salary = '?39000'? WHERE employee_code = 'EMP007'/.test(txt(d)), 'file values are filled into the expanded SQL');
  ok(/Proceed &\s*Execute\s*\(2 queries\)/.test(txt(d)), 'button shows the query count');
  d.querySelector('#run-btn').click();
  ok(await waitFor(() => /2 queries executed successfully in one transaction/.test(txt(d)), 10000), 'both updates executed in one transaction');

  console.log('\n[13] multiple queries + result download');
  d.querySelector('#manual-btn').click();
  ok(await waitFor(() => d.querySelector('#sql-edit')), 'manual SQL editor opens');
  const ta = d.querySelector('#sql-edit');
  ta.value = 'SELECT employee_code, name FROM employees ORDER BY 1 LIMIT 2; SELECT department_name FROM departments ORDER BY 1 LIMIT 3'; ta.dispatchEvent(new w.Event('input'));
  ok(await waitFor(() => /2 queries/.test(txt(d)) && /Ready — review before executing/.test(txt(d))), 'two queries validated together');
  d.querySelector('#run-btn').click();
  ok(await waitFor(() => /Query 1 · 2 rows/.test(txt(d)) && /Query 2 · 3 rows/.test(txt(d)), 10000), 'one result tab per query');
  d.querySelector('[data-rtab="1"]').click();
  ok(await waitFor(() => /department_name/.test(d.querySelector('#studio-right').textContent)), 'second tab shows the second query result');
  ok(!!d.querySelector('#dl-csv') && !!d.querySelector('#dl-xlsx'), 'CSV and Excel download buttons present');
  w.URL.createObjectURL = () => 'blob:test'; w.URL.revokeObjectURL = () => {};
  d.querySelector('#dl-csv').click();
  ok(await waitFor(() => /Downloaded query_results_\d+_\d+\.csv/.test(txt(d))), 'CSV download completes');
  d.querySelector('#dl-xlsx').click();
  ok(await waitFor(() => /Downloaded query_results_\d+_\d+\.xlsx/.test(txt(d))), 'Excel download completes');

  console.log('\n[14] several questions in one go (AI writes 2 queries)');
  d.querySelector('#attach-remove').click();
  ok(await waitFor(() => d.querySelector('#attach-btn')), 'attached file can be removed');
  d.querySelector('#nl').value = 'Show departments and count employees'; d.querySelector('#nl').dispatchEvent(new w.Event('input'));
  d.querySelector('#gen-btn').click();
  ok(await waitFor(() => /COUNT\(\*\) AS n FROM employees/.test(d.querySelector('#studio-right').textContent) && /2 queries/.test(txt(d)), 10000), 'multi-statement AI answer shown as 2 queries');
  ok(/Generated SQL/.test(d.querySelector('#studio-right').textContent) && !/Result\s*Query/.test(d.querySelector('#studio-right').textContent), 'previous result cleared when new SQL is generated');

  console.log('\n[15] nothing leaks to the next user on the same browser tab');
  d.querySelector('#logout').click();
  ok(await waitFor(() => d.querySelector('#login-form')), 'signed out');
  await login(b, 'analyst', 'AnaPassw0rd!x');
  ok(await waitFor(() => d.querySelector('#studio-right') && /No SQL yet/.test(d.querySelector('#studio-right').textContent)), 'studio is empty for the next user (no old SQL/results)');
  ok(!/salary_update\.csv/.test(txt(d)) && !d.querySelector('#attach-remove'), 'previous attachment is not shown');
  w.eval("state.studio.nl='x'");
  d.querySelector('#manual-btn').click();
  const ta2 = await waitFor(() => d.querySelector('#sql-edit')) && d.querySelector('#sql-edit');
  ta2.value = "DELETE FROM employees WHERE employee_code = 'EMP001'; SELECT 1"; ta2.dispatchEvent(new w.Event('input'));
  ok(await waitFor(() => /Permission denied/.test(txt(d))) && d.querySelector('#run-btn').disabled, 'analyst: script containing a write is "Permission denied" and cannot run');

  console.log('\n[16] XSS safety');
  w.eval(`state.studio.nl='<img src=x onerror=alert(1)>'; state.studio.gen={valid:true,operation_type:'SELECT',target_table:'<b>x</b>',tables_considered:['<script>boom()</script>']}; state.studio.sql="SELECT '<img src=x onerror=alert(2)>'"; go('studio'); studioRender();`);
  await sleep(300);
  ok(!d.querySelector('#view img') && !d.querySelector('#view script'), 'no injected elements from user/AI content');

  console.log('\n--- runtime errors captured:', errors.filter(e => !/Could not load resource|Not implemented/i.test(e)).length);
  errors.filter(e => !/Could not load resource|Not implemented/i.test(e)).slice(0, 8).forEach(e => console.log('   ', e.slice(0, 300)));
  console.log(`\nRESULT: ${pass} passed, ${fail} failed`);
  if (fakeOllama) fakeOllama.close();
  process.exit(fail || errors.some(e => /jsdomError.*(TypeError|ReferenceError)/.test(e)) ? 1 : 0);
})().catch(e => { console.error('TEST CRASH', e); process.exit(2); });
