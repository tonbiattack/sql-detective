// 画面確認用スクリーンショット（PC・スマホ）。外部依存なし。
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const URL_UNDER_TEST = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const OUT_DIR = process.argv[3] || '.';
const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
];
const PORT = 9334;
const profile = mkdtempSync(join(tmpdir(), 'sqldet-shot-'));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); }
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
    const result = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    return result.result.value;
  }
  async shot(path) {
    const { data } = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    writeFileSync(path, Buffer.from(data, 'base64'));
  }
  close() { this.ws.close(); }
}

async function main() {
  let browser;
  for (const executable of BROWSERS) {
    try {
      browser = spawn(executable, [
        '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
        '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank',
      ], { stdio: 'ignore' });
      await waitForDevtools();
      break;
    } catch {
      browser?.kill();
      browser = undefined;
    }
  }
  if (!browser) throw new Error('ブラウザが見つかりません');

  const targets = await fetchJson('/json/list');
  const cdp = await Cdp.connect(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  // PC: 初期画面
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Page.navigate', { url: URL_UNDER_TEST });
  await sleep(1600);
  await cdp.shot(join(OUT_DIR, 'shot-pc-start.png'));

  // PC: 証拠を開いた状態
  await cdp.evaluate(`document.querySelector('.evidence-toggle').click()`);
  await sleep(400);
  await cdp.shot(join(OUT_DIR, 'shot-pc-evidence.png'));

  // PC: 結果表示
  await cdp.evaluate(`
    (() => { let g = 0; while (document.querySelector('.evidence-toggle') && g++ < 10) document.querySelector('.evidence-toggle').click(); })()
  `);
  await sleep(300);
  await cdp.evaluate(`document.querySelector('.suspect-button:not(.eliminated)').click()`);
  await sleep(200);
  await cdp.evaluate(`document.getElementById('accuse-button').click()`);
  await sleep(500);
  await cdp.shot(join(OUT_DIR, 'shot-pc-result.png'));

  // スマホ: 初期画面と結果
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await cdp.evaluate(`localStorage.clear()`);
  await cdp.send('Page.reload');
  await sleep(1600);
  await cdp.shot(join(OUT_DIR, 'shot-mobile-start.png'));
  await cdp.evaluate(`document.querySelector('.evidence-toggle').click()`);
  await sleep(400);
  await cdp.shot(join(OUT_DIR, 'shot-mobile-evidence.png'));

  cdp.close();
  browser.kill();
  await new Promise((resolve) => { browser.once('exit', resolve); setTimeout(resolve, 5000); });
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* noop */ }
  console.log('スクリーンショットを保存しました');
}

main().catch((error) => { console.error(error); process.exit(1); });
