// 実ブラウザ（Chrome/Edge のヘッドレス）でページを操作し、致命的エラーと
// 通しプレイを確認する。外部依存なし。CDP を WebSocket で直接叩く。
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const URL_UNDER_TEST = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
];
const PORT = 9333;
const profile = mkdtempSync(join(tmpdir(), 'sqldet-'));

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function fetchJson(path) {
  const response = await fetch(`http://127.0.0.1:${PORT}${path}`);
  return response.json();
}

async function waitForDevtools() {
  for (let i = 0; i < 60; i += 1) {
    try { return await fetchJson('/json/version'); } catch { await sleep(250); }
  }
  throw new Error('DevTools に接続できませんでした');
}

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', reject, { once: true });
    });
    const cdp = new Cdp(ws);
    ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id && cdp.pending.has(message.id)) {
        const { resolve, reject } = cdp.pending.get(message.id);
        cdp.pending.delete(message.id);
        message.error ? reject(new Error(message.error.message)) : resolve(message.result);
      } else if (message.method) {
        cdp.events.push(message);
      }
    });
    return cdp;
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || '評価エラー');
    }
    return result.result.value;
  }
  close() { this.ws.close(); }
}

async function main() {
  let browser;
  let lastError;
  for (const executable of BROWSERS) {
    try {
      browser = spawn(executable, [
        '--headless=new', `--remote-debugging-port=${PORT}`,
        `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
        '--disable-gpu', '--window-size=1280,900', 'about:blank',
      ], { stdio: 'ignore' });
      await waitForDevtools();
      break;
    } catch (error) {
      lastError = error;
      browser?.kill();
      browser = undefined;
    }
  }
  if (!browser) throw lastError || new Error('ブラウザが見つかりません');

  const targets = await fetchJson('/json/list');
  const page = targets.find((target) => target.type === 'page');
  const cdp = await Cdp.connect(page.webSocketDebuggerUrl);

  const errors = [];
  cdp.ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') {
      errors.push(message.params.exceptionDetails.exception?.description || '例外');
    }
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      errors.push(message.params.args.map((arg) => arg.value ?? arg.description).join(' '));
    }
    if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') {
      errors.push(message.params.entry.text);
    }
  });
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Page.enable');
  await cdp.send('Page.navigate', { url: URL_UNDER_TEST });
  await sleep(1500);

  const checks = [];
  const check = (name, ok, detail = '') => checks.push({ name, ok, detail });

  const loaded = await cdp.evaluate(`!document.getElementById('game-content').hidden`);
  check('ゲーム画面が表示される', loaded);

  const title = await cdp.evaluate(`document.getElementById('case-title').textContent`);
  check('事件タイトルが出る', Boolean(title) && title !== '事件を読み込み中', title);

  const brief = await cdp.evaluate(`document.getElementById('case-brief').textContent`);
  check('依頼文が出る', brief.length > 10, brief.slice(0, 40));

  const suspects = await cdp.evaluate(`document.querySelectorAll('.suspect-button').length`);
  check('容疑者が10人出る', suspects === 10, String(suspects));

  const cards = await cdp.evaluate(`document.querySelectorAll('.evidence-card').length`);
  check('証拠カードが出る', cards >= 2, String(cards));

  // 証拠を1枚開く
  await cdp.evaluate(`document.querySelector('.evidence-toggle').click()`);
  await sleep(300);
  const opened = await cdp.evaluate(`document.querySelectorAll('.evidence-table').length`);
  check('証拠を開くと結果表が出る', opened >= 1, String(opened));

  const sqlShown = await cdp.evaluate(`document.querySelectorAll('.evidence-sql pre').length`);
  check('対応するSQLを閲覧できる', sqlShown >= 1, String(sqlShown));

  const remaining = await cdp.evaluate(`document.getElementById('remaining').textContent`);
  check('コストが減る', remaining === '80点', remaining);

  // 全証拠を開いて候補を1人に絞る
  await cdp.evaluate(`
    (() => {
      let guard = 0;
      while (document.querySelector('.evidence-toggle') && guard++ < 10) {
        document.querySelector('.evidence-toggle').click();
      }
    })()
  `);
  await sleep(300);
  const candidates = await cdp.evaluate(`document.getElementById('candidate-count').textContent`);
  check('全証拠で候補が1人になる', candidates === '1人', candidates);

  // 候補（条件に一致）を選んで告発
  await cdp.evaluate(`document.querySelector('.suspect-button:not(.eliminated)').click()`);
  await sleep(200);
  const enabled = await cdp.evaluate(`!document.getElementById('accuse-button').disabled`);
  check('告発ボタンが有効になる', enabled);

  await cdp.evaluate(`document.getElementById('accuse-button').click()`);
  await sleep(400);
  const resultShown = await cdp.evaluate(`!document.getElementById('result-panel').hidden`);
  check('結果が表示される', resultShown);

  const resultTitle = await cdp.evaluate(`document.getElementById('result-title').textContent`);
  check('正解なら事件解決', resultTitle === '事件解決', resultTitle);

  const proof = await cdp.evaluate(`document.querySelectorAll('#result-proof .proof-table').length`);
  check('告発後に根拠が提示される', proof >= 1, String(proof));

  // 次の事件へ
  await cdp.evaluate(`document.getElementById('next-button').click()`);
  await sleep(400);
  const nextHidden = await cdp.evaluate(`document.getElementById('result-panel').hidden`);
  const nextOpened = await cdp.evaluate(`document.querySelectorAll('.evidence-table').length`);
  check('次の事件で状態がリセットされる', nextHidden && nextOpened === 0);

  // リロードしても破綻しない
  await cdp.evaluate(`document.querySelector('.evidence-toggle').click()`);
  await sleep(200);
  await cdp.send('Page.reload');
  await sleep(1500);
  const afterReload = await cdp.evaluate(`document.querySelectorAll('.evidence-table').length`);
  check('リロード後も状態が復元される', afterReload >= 1, String(afterReload));

  // スマホ幅
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  });
  await sleep(400);
  const overflow = await cdp.evaluate(`document.documentElement.scrollWidth - document.documentElement.clientWidth`);
  check('スマホ幅で横スクロールが出ない', overflow <= 1, `overflow=${overflow}px`);

  const tapTarget = await cdp.evaluate(`
    Math.min(...[...document.querySelectorAll('button')]
      .filter((b) => b.getBoundingClientRect().height > 0)
      .map((b) => b.getBoundingClientRect().height))
  `);
  check('ボタンが44px以上', tapTarget >= 44, `${tapTarget.toFixed(1)}px`);

  check('Consoleに致命的エラーがない', errors.length === 0, errors.join(' | ').slice(0, 200));

  cdp.close();
  browser.kill();
  await new Promise((resolve) => {
    browser.once('exit', resolve);
    setTimeout(resolve, 5000);
  });
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* 一時ディレクトリは残ってよい */ }

  let failed = 0;
  for (const item of checks) {
    if (!item.ok) failed += 1;
    console.log(`${item.ok ? 'ok' : 'not ok'} - ${item.name}${item.detail ? ` (${item.detail})` : ''}`);
  }
  console.log(`\n${checks.length - failed}/${checks.length} passed`);
  process.exit(failed ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* noop */ }
  process.exit(1);
});
