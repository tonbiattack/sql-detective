// web/logic.js のルールを Node で検証する（外部依存なし）。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  MAX_SCORE, COST_PER_EVIDENCE, answerIdFor, newGame, candidatesFor,
  openEvidence, selectSuspect, accuse, restoreGame,
} from './logic.js';

const here = dirname(fileURLToPath(import.meta.url));
const dataset = JSON.parse(readFileSync(join(here, 'cases.json'), 'utf8'));
const cases = dataset.cases;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('全事件で決め手の証拠が1人を指す', () => {
  for (const data of cases) {
    const answer = answerIdFor(data);
    assert.ok(Number.isInteger(answer), `seed=${data.seed}`);
    assert.equal(data.evidence.at(-1).candidateIds.length, 1);
  }
});

test('答えはJSONに含まれない', () => {
  for (const data of cases) {
    assert.equal('answerId' in data, false);
  }
});

test('証拠を開くと候補が減るか変わらない', () => {
  for (const data of cases) {
    let game = newGame(0);
    let previous = candidatesFor(game, data).size;
    for (const card of data.evidence) {
      game = openEvidence(game, data, card.id);
      const current = candidatesFor(game, data).size;
      assert.ok(current <= previous, `seed=${data.seed}`);
      previous = current;
    }
    assert.equal(previous, 1);
  }
});

test('同じ証拠を二度開いてもコストは増えない', () => {
  const data = cases[0];
  let game = newGame(0);
  game = openEvidence(game, data, data.evidence[0].id);
  game = openEvidence(game, data, data.evidence[0].id);
  assert.equal(game.opened.length, 1);
});

test('スコアは閲覧枚数に応じて減る', () => {
  const data = cases[0];
  const answer = answerIdFor(data);
  let game = newGame(0);
  assert.equal(accuse(selectSuspect(game, data, answer), data).result.score, MAX_SCORE);
  game = openEvidence(game, data, data.evidence[0].id);
  assert.equal(accuse(selectSuspect(game, data, answer), data).result.score, MAX_SCORE - COST_PER_EVIDENCE);
});

test('誤告発は0点', () => {
  const data = cases[0];
  const answer = answerIdFor(data);
  const wrong = data.suspects.find((suspect) => suspect.id !== answer).id;
  const result = accuse(selectSuspect(newGame(0), data, wrong), data).result;
  assert.equal(result.correct, false);
  assert.equal(result.score, 0);
});

test('未選択では告発できない', () => {
  const data = cases[0];
  assert.equal(accuse(newGame(0), data).result, null);
});

test('告発後は操作が固まる', () => {
  const data = cases[0];
  const answer = answerIdFor(data);
  let game = accuse(selectSuspect(newGame(0), data, answer), data);
  game = openEvidence(game, data, data.evidence[0].id);
  game = selectSuspect(game, data, answer);
  assert.equal(game.opened.length, 0);
});

test('存在しないIDは無視される', () => {
  const data = cases[0];
  const game = selectSuspect(newGame(0), data, 9999);
  assert.equal(game.selected, null);
  assert.equal(openEvidence(game, data, 'nope').opened.length, 0);
});

test('保存データを復元できる', () => {
  const data = cases[0];
  const answer = answerIdFor(data);
  const game = accuse(selectSuspect(openEvidence(newGame(0), data, data.evidence[0].id), data, answer), data);
  const restored = restoreGame({ version: 1, ...game }, cases);
  assert.deepEqual(restored, game);
});

test('壊れた保存データは捨てる', () => {
  assert.equal(restoreGame(null, cases), null);
  assert.equal(restoreGame({ version: 2, caseIndex: 0, opened: [], selected: null, result: null }, cases), null);
  assert.equal(restoreGame({ version: 1, caseIndex: 9999, opened: [], selected: null, result: null }, cases), null);
  assert.equal(restoreGame({ version: 1, caseIndex: 0, opened: ['nope'], selected: null, result: null }, cases), null);
  assert.equal(restoreGame({ version: 1, caseIndex: 0, opened: [], selected: 9999, result: null }, cases), null);
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${name}\n  ${error.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
