// 証拠閲覧と告発のルール。DOMを扱わず、UIから独立させる。
export const MAX_SCORE = 100;
export const COST_PER_EVIDENCE = 20;

// 正解は「決め手」の証拠（最後のカード）が示す唯一の候補から導く。
// 事件データに答えを直接持たせず、証拠そのものから決まるようにしている。
export function answerIdFor(caseData) {
  const decisive = caseData.evidence.at(-1);
  return decisive.candidateIds.length === 1 ? decisive.candidateIds[0] : null;
}

export function newGame(caseIndex) {
  return { caseIndex, opened: [], selected: null, result: null };
}

export function candidatesFor(game, caseData) {
  let candidates = new Set(caseData.suspects.map((suspect) => suspect.id));
  for (const id of game.opened) {
    const card = caseData.evidence.find((evidence) => evidence.id === id);
    if (!card) continue;
    const matches = new Set(card.candidateIds);
    candidates = new Set([...candidates].filter((candidate) => matches.has(candidate)));
  }
  return candidates;
}

export function openEvidence(game, caseData, cardId) {
  if (game.result || game.opened.includes(cardId) || !caseData.evidence.some((card) => card.id === cardId)) return game;
  return { ...game, opened: [...game.opened, cardId] };
}

export function selectSuspect(game, caseData, id) {
  if (game.result || !caseData.suspects.some((suspect) => suspect.id === id)) return game;
  return { ...game, selected: id };
}

export function accuse(game, caseData) {
  if (game.result || game.selected === null || !caseData.suspects.some((suspect) => suspect.id === game.selected)) return game;
  const correct = game.selected === answerIdFor(caseData);
  return { ...game, result: { correct, score: correct ? Math.max(0, MAX_SCORE - game.opened.length * COST_PER_EVIDENCE) : 0 } };
}

export function restoreGame(raw, cases) {
  if (!raw || raw.version !== 1 || !Number.isInteger(raw.caseIndex) || raw.caseIndex < 0 || raw.caseIndex >= cases.length) return null;
  const caseData = cases[raw.caseIndex];
  if (!Array.isArray(raw.opened) || new Set(raw.opened).size !== raw.opened.length || raw.opened.some((id) => !caseData.evidence.some((card) => card.id === id))) return null;
  if (raw.selected !== null && !caseData.suspects.some((suspect) => suspect.id === raw.selected)) return null;
  let state = { ...newGame(raw.caseIndex), opened: [...raw.opened], selected: raw.selected };
  if (raw.result !== null) {
    if (!raw.result || state.selected === null) return null;
    state = accuse(state, caseData);
  }
  return state;
}
