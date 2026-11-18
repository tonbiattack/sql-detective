import { MAX_SCORE, COST_PER_EVIDENCE, answerIdFor, newGame, candidatesFor, openEvidence, selectSuspect, accuse, restoreGame } from './logic.js';

const $ = (id) => document.getElementById(id);
const STORAGE_KEY = 'sql-detective-web-v1';
let cases = [];
let game;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function save() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...game })); } catch { /* 保存できなくてもプレイ可能 */ }
}

function readSave() {
  try { return restoreGame(JSON.parse(localStorage.getItem(STORAGE_KEY)), cases); } catch { return null; }
}

function chooseCase(except = -1) {
  if (cases.length === 1) return 0;
  let index = Math.floor(Math.random() * cases.length);
  if (index === except) index = (index + 1) % cases.length;
  return index;
}

function announce(message) { $('feedback').textContent = message; }

function makeTable(card, className = 'evidence-table') {
  const wrapper = element('div', 'table-wrap');
  const table = element('table', className);
  const head = element('thead');
  const header = element('tr');
  for (const column of card.columns) header.append(element('th', '', column === 'user_id' ? '社員ID' : column));
  head.append(header);
  const body = element('tbody');
  for (const row of card.rows) {
    const tr = element('tr');
    for (const cell of row) tr.append(element('td', '', cell === null ? '—' : cell));
    body.append(tr);
  }
  table.append(head, body);
  wrapper.append(table);
  if (!card.rows.length) wrapper.append(element('p', '', '該当する記録はありません。'));
  return wrapper;
}

function renderEvidence(data) {
  const list = $('evidence-list');
  list.replaceChildren();
  data.evidence.forEach((card, index) => {
    const opened = game.opened.includes(card.id);
    const article = element('article', 'evidence-card');
    const head = element('div', 'evidence-head');
    const title = element('h3', '', card.title);
    const badge = element('span', 'evidence-cost', opened ? '閲覧済み' : '閲覧 1回');
    head.append(title, badge);
    const description = element('p', 'evidence-description', card.description);
    article.append(head, description);
    if (!opened && !game.result) {
      const button = element('button', 'evidence-toggle', 'この証拠を開く');
      button.type = 'button';
      button.addEventListener('click', () => {
        const before = candidatesFor(game, data).size;
        game = openEvidence(game, data, card.id);
        const after = candidatesFor(game, data).size;
        save(); render(); announce(`「${card.title}」を公開。条件に合う候補は${before}人から${after}人になりました。`);
      });
      article.append(button);
    }
    if (opened) {
      const content = element('div', 'evidence-body');
      content.append(makeTable(card));
      const sql = element('details', 'evidence-sql');
      sql.append(element('summary', '', 'この結果のSQLを見る'), element('pre', '', card.sql));
      content.append(sql);
      article.append(content);
    }
    list.append(article);
  });
}

function renderSuspects(data, candidates) {
  const list = $('suspect-list');
  list.replaceChildren();
  for (const suspect of data.suspects) {
    const remaining = candidates.has(suspect.id);
    const button = element('button', `suspect-button${game.selected === suspect.id ? ' selected' : ''}${remaining ? '' : ' eliminated'}`);
    button.type = 'button';
    button.disabled = Boolean(game.result);
    button.setAttribute('aria-pressed', String(game.selected === suspect.id));
    button.append(element('span', 'suspect-id', `社員 ${suspect.id}`));
    button.append(element('span', 'suspect-name', suspect.name));
    button.append(element('span', 'suspect-meta', `${suspect.department} / ${remaining ? '条件に一致' : '公開済み証拠の条件外'}`));
    button.addEventListener('click', () => {
      game = selectSuspect(game, data, suspect.id);
      save(); render(); announce(`${suspect.name}を選択しました。`);
    });
    list.append(button);
  }
}

function renderResult(data) {
  const panel = $('result-panel');
  panel.hidden = !game.result;
  if (!game.result) return;
  const culprit = data.suspects.find((suspect) => suspect.id === answerIdFor(data));
  panel.classList.toggle('result-win', game.result.correct);
  panel.classList.toggle('result-loss', !game.result.correct);
  $('result-title').textContent = game.result.correct ? '事件解決' : '告発は不正解';
  $('result-text').textContent = `真相：${culprit.name}（社員 ${culprit.id}） / スコア ${game.result.score}点`;
  const proof = $('result-proof');
  proof.replaceChildren(element('p', '', `決め手：${data.evidence.at(-1).description}`), makeTable(data.evidence.at(-1), 'proof-table'));
}

function render() {
  const data = cases[game.caseIndex];
  const candidates = candidatesFor(game, data);
  $('case-title').textContent = data.title;
  $('case-brief').textContent = data.brief;
  $('case-number').textContent = `${game.caseIndex + 1} / ${cases.length}`;
  $('case-note').textContent = '証拠はどれから開いても構いません。公開された条件に合わない社員も選択できます。';
  $('remaining').textContent = `${Math.max(0, MAX_SCORE - game.opened.length * COST_PER_EVIDENCE)}点`;
  $('evidence-count').textContent = `${game.opened.length}枚`;
  $('candidate-count').textContent = `${candidates.size}人`;
  renderEvidence(data);
  renderSuspects(data, candidates);
  const selected = data.suspects.find((suspect) => suspect.id === game.selected);
  $('selection-summary').textContent = selected ? `選択中：${selected.name}` : '告発する社員を選んでください。';
  $('accuse-button').disabled = !selected || Boolean(game.result);
  renderResult(data);
}

$('accuse-button').addEventListener('click', () => {
  game = accuse(game, cases[game.caseIndex]);
  save(); render();
  announce(game.result.correct ? '告発が的中しました。' : '告発は不正解でした。真相を確認できます。');
  $('result-panel').focus();
});
$('retry-button').addEventListener('click', () => { game = newGame(game.caseIndex); save(); render(); announce('同じ事件を最初からやり直します。'); window.scrollTo(0, 0); });
$('next-button').addEventListener('click', () => { game = newGame(chooseCase(game.caseIndex)); save(); render(); announce('新しい事件を開始しました。'); window.scrollTo(0, 0); });
$('reset-button').addEventListener('click', () => { game = newGame(chooseCase(game.caseIndex)); save(); render(); announce('新しい事件を開始しました。'); window.scrollTo(0, 0); });

async function init() {
  try {
    const response = await fetch('./cases.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.version !== 1 || !Array.isArray(data.cases) || !data.cases.length) throw new Error('事件データが不正です');
    cases = data.cases;
    game = readSave() || newGame(chooseCase());
    $('loading').hidden = true;
    $('game-content').hidden = false;
    render();
  } catch (error) {
    $('loading').textContent = `事件を読み込めませんでした。ページを再読み込みしてください。（${error.message}）`;
  }
}

init();
