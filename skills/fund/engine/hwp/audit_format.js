#!/usr/bin/env node
// audit_format.js — 한국어 제안서·보고서 서식 프리셋 준수 검사 (.hwp 전용)
//
//   node scripts/audit_format.js <file.hwp> [--preset <preset.json>] [--json]
//
// 프리셋 근거와 각 항목의 의미는 references/korean-doc-format.md 참고.
// op 응답의 success 는 "덮이지 않았음"을 보장하지 않는다. 제출 전 반드시 이 체커를 통과시켜라.

import { readFileSync } from 'node:fs';
import { inflateRawSync } from 'node:zlib';
import * as CFB from './vendor/cfb/cfb.js';

// ── 프리셋 (최종본v2 실측, 2026-08) ────────────────────────────────
const DEFAULT_PRESET = {
  page_mm: { top: 15, bottom: 15, left: 20, right: 20, header: 10, footer: 10 },
  lineSpacing: 160,
  spacingBefore: 0,
  spacingAfter: 0,
  letterSpacing: -5,
  bulletLevels: [[2000, -3000], [5000, 0], [7000, 0]],
  headerFill: '#E3F4E3',
  cellMarginRange: [100, 200],   // HWPUNIT, 0.35~0.70mm
  bodyFontPt: 1100,              // 본문 11pt (pt×100). append_paragraph 기본값은 10pt 라 그냥 두면 어긋난다
  conformOk: 0.95,               // 표 헤더 서식: 이 이상이면 통과
  conformWarn: 0.85,             // 이 아래면 Error. 사이면 Warning (사람이 만든 레퍼런스도 90% 안팎이라 100% 강제는 비현실적)
};

function loadPreset(args) {
  const idx = args.indexOf('--preset');
  if (idx < 0 || !args[idx + 1]) return DEFAULT_PRESET;
  const ext = JSON.parse(readFileSync(args[idx + 1], 'utf8'));
  const p = ext.page || {};
  const b = ext.body || {};
  const t = ext.table || {};
  const bullets = (ext.bodyBulletLevels || [])
    .filter((l) => l.count > 1)
    .map((l) => [l.marginLeft, l.indent]);
  return {
    page_mm: {
      top: p.topMm ?? DEFAULT_PRESET.page_mm.top,
      bottom: p.bottomMm ?? DEFAULT_PRESET.page_mm.bottom,
      left: p.leftMm ?? DEFAULT_PRESET.page_mm.left,
      right: p.rightMm ?? DEFAULT_PRESET.page_mm.right,
      header: p.headerMm ?? DEFAULT_PRESET.page_mm.header,
      footer: p.footerMm ?? DEFAULT_PRESET.page_mm.footer,
    },
    lineSpacing: b.lineSpacing ?? DEFAULT_PRESET.lineSpacing,
    spacingBefore: b.spacingBefore ?? DEFAULT_PRESET.spacingBefore,
    spacingAfter: b.spacingAfter ?? DEFAULT_PRESET.spacingAfter,
    letterSpacing: b.letterSpacing ?? DEFAULT_PRESET.letterSpacing,
    bulletLevels: bullets.length > 0 ? bullets : DEFAULT_PRESET.bulletLevels,
    headerFill: t.headerFillColor === null ? '없음' : (t.headerFillColor ?? DEFAULT_PRESET.headerFill),
    cellMarginRange: t.cellMarginHwpunit
      ? [Math.max(0, t.cellMarginHwpunit - 50), t.cellMarginHwpunit + 50]
      : DEFAULT_PRESET.cellMarginRange,
    bodyFontPt: b.fontPtRaw ?? DEFAULT_PRESET.bodyFontPt,
    conformOk: t.conformOk ?? DEFAULT_PRESET.conformOk,
    conformWarn: t.conformWarn ?? DEFAULT_PRESET.conformWarn,
  };
}

const PRESET = loadPreset(process.argv.slice(2));

const TAG = { BORDER_FILL: 20, CHAR_SHAPE: 21, PARA_SHAPE: 25, PARA_HEADER: 66, PARA_TEXT: 67, PARA_CHAR_SHAPE: 68, LIST_HEADER: 72, PAGE_DEF: 73 };
const HWPUNIT_PER_MM = 7200 / 25.4;
// 8 WCHAR 를 차지하는 인라인/확장 컨트롤 — 건너뛰지 않으면 'tbl ' 페이로드가 본문으로 샌다
const WIDE_CTRL = new Set([1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]);

function* walk(buf) {
  let p = 0;
  while (p + 4 <= buf.length) {
    const hdr = buf.readUInt32LE(p); p += 4;
    const tag = hdr & 0x3ff, level = (hdr >> 10) & 0x3ff;
    let size = (hdr >> 20) & 0xfff;
    if (size === 0xfff) { size = buf.readUInt32LE(p); p += 4; }
    yield { tag, level, off: p, size };
    p += size;
  }
}

function text(buf, off, size) {
  let out = '', i = 0;
  while (i + 1 < size) {
    const c = buf.readUInt16LE(off + i);
    if (WIDE_CTRL.has(c)) { i += 16; continue; }
    if (c >= 32) out += String.fromCharCode(c);
    i += 2;
  }
  return out;
}

function load(file) {
  const cfb = CFB.read(readFileSync(file), { type: 'buffer' });
  const get = (name) => {
    const e = cfb.FullPaths.map((p, i) => [p, cfb.FileIndex[i]])
      .find(([p]) => p.replace(/^[^/]*\//, '') === name);
    if (!e) return null;
    return inflateRawSync(Buffer.from(e[1].content));
  };
  const di = get('DocInfo');
  const bodies = [];
  for (let n = 0; n < 32; n++) {
    const b = get(`BodyText/Section${n}`);
    if (!b) break;
    bodies.push(b);
  }
  return { di, bodies };
}

function analyze(file) {
  const { di, bodies } = load(file);
  const ps = [], cs = [], bf = [];
  for (const r of walk(di)) {
    if (r.tag === TAG.PARA_SHAPE) ps.push(r.off);
    else if (r.tag === TAG.CHAR_SHAPE) cs.push(r.off);
    else if (r.tag === TAG.BORDER_FILL) bf.push(r.off);
  }
  const fillColor = (id) => {
    if (id < 1 || id > bf.length) return null;
    const p = bf[id - 1] + 2 + 24 + 6;          // attr(2) + 4면×6 + 대각선(6)
    const type = di.readUInt32LE(p);
    if (!(type & 1)) return '없음';
    const c = di.readUInt32LE(p + 4);
    return '#' + [c & 255, (c >> 8) & 255, (c >> 16) & 255]
      .map((v) => v.toString(16).toUpperCase().padStart(2, '0')).join('');
  };

  const out = {
    page: null, lineSpacing: new Map(), spBefore: new Map(), spAfter: new Map(),
    letter: new Map(),
    fontSize: new Map(), bullets: new Map(), headerCells: 0, headerFill: new Map(),
    headerBold: 0, headerCenter: 0, cellMargins: new Map(),
    sectionHeads: { withBlank: 0, without: 0, samples: [] },
    literalNumbered: [],
  };
  const bump = (m, k) => m.set(k, (m.get(k) || 0) + 1);

  for (const r of walk(bodies[0])) {
    if (r.tag === TAG.PAGE_DEF && r.size >= 32) {
      const g = (k) => +(bodies[0].readUInt32LE(r.off + k) / HWPUNIT_PER_MM).toFixed(1);
      out.page = { left: g(8), right: g(12), top: g(16), bottom: g(20), header: g(24), footer: g(28) };
      break;
    }
  }

  for (const body of bodies) {
    const recs = [...walk(body)];
    const bodyParas = [];          // level 0 = 본문 문단만. 셀 문단을 섞으면 인접성 판단이 무너진다.
    let pending = null;
    let paraLevel = -1;            // PARA_CHAR_SHAPE 는 자식 레코드라 자기 level 이 한 단 깊다 — 문단 level 을 따로 기억한다

    for (let i = 0; i < recs.length; i++) {
      const r = recs[i];
      if (r.tag === TAG.PARA_HEADER) {
        pending = { level: r.level, psId: body.readUInt16LE(r.off + 8), text: '' };
        paraLevel = r.level;
        if (r.level === 0) bodyParas.push(pending);
      } else if (r.tag === TAG.PARA_TEXT && pending) {
        pending.text = text(body, r.off, r.size);
        const o = pending.level === 0 ? ps[pending.psId] : undefined;
        if (o !== undefined) {   // 본문 문단(level 0)만 — 셀 문단은 줄간격 110% 등 별도 규격이라 섞으면 지배값이 뒤집힌다
          const attr = di.readUInt32LE(o);
          bump(out.lineSpacing, di.readInt32LE(o + 24));
          bump(out.spBefore, di.readInt32LE(o + 16));
          bump(out.spAfter, di.readInt32LE(o + 20));
          if (((attr >> 23) & 3) === 3) {
            bump(out.bullets, `${di.readInt32LE(o + 4)},${di.readInt32LE(o + 12)}`);
          }
        }
        pending = null;
      } else if (r.tag === TAG.PARA_CHAR_SHAPE) {
        for (let k = 0; k < r.size / 8; k++) {
          const id = body.readUInt32LE(r.off + 8 * k + 4);
          if (cs[id] === undefined) continue;
          bump(out.letter, di.readInt8(cs[id] + 21));
          // 본문(level 0)만 — 표 셀(level 2)은 크기 규칙이 다르다.
          // 헤딩도 level 0 이지만 수가 적어 지배값은 본문 크기가 된다.
          if (paraLevel === 0) bump(out.fontSize, di.readUInt16LE(cs[id] + 42));
        }
      } else if (r.tag === TAG.LIST_HEADER && r.size >= 34) {
        if (body.readUInt16LE(r.off + 10) !== 0) continue;   // 헤더행(row 0)만
        const bfId = body.readUInt16LE(r.off + 32);
        if (bfId === 0) continue;                            // 표가 아닌 리스트(꼬리말 등)
        out.headerCells++;
        bump(out.headerFill, fillColor(bfId));
        for (let k = 0; k < 4; k++) bump(out.cellMargins, body.readUInt16LE(r.off + 24 + 2 * k));
        for (let j = i + 1; j < Math.min(i + 6, recs.length); j++) {
          const q = recs[j];
          if (q.tag === TAG.PARA_HEADER) {
            const o = ps[body.readUInt16LE(q.off + 8)];
            if (o !== undefined && ((di.readUInt32LE(o) >> 2) & 7) === 3) out.headerCenter++;
          } else if (q.tag === TAG.PARA_CHAR_SHAPE) {
            let bold = q.size > 0;
            for (let k = 0; k < q.size / 8; k++) {
              const id = body.readUInt32LE(q.off + 8 * k + 4);
              if (cs[id] === undefined || !((di.readUInt16LE(cs[id] + 46) >> 1) & 1)) bold = false;
            }
            if (bold) out.headerBold++;
            break;
          }
        }
      }
    }

    for (let i = 0; i < bodyParas.length; i++) {
      const t = bodyParas[i].text.trim();
      if (/^[가-하]\.\s/.test(t)) {
        if (i > 0 && bodyParas[i - 1].text.trim() === '') out.sectionHeads.withBlank++;
        else { out.sectionHeads.without++; if (out.sectionHeads.samples.length < 5) out.sectionHeads.samples.push(t.slice(0, 24)); }
      }
      if (/^\d+\.\s/.test(t) && t.length > 30) out.literalNumbered.push(t.slice(0, 30));
    }
  }
  return out;
}

const grade = (r) => (r >= PRESET.conformOk ? null : r >= PRESET.conformWarn ? 'Warning' : 'Error');
const top = (m) => [...m.entries()].sort((a, b) => b[1] - a[1])[0] || [null, 0];

function checks(a) {
  const rows = [];
  const add = (n, name, got, want, sev) => rows.push({ n, name, got, want, ok: sev === null, sev });

  const p = a.page;
  if (!p) add(1, '쪽 여백', 'PAGE_DEF 없음', '-', 'Error');
  else {
    const bad = Object.entries(PRESET.page_mm).filter(([k, v]) => Math.abs(p[k] - v) > 0.2);
    const pm = PRESET.page_mm;
    add(1, '쪽 여백 (위/아래/왼/오/머리/꼬리 mm)',
      `${p.top}/${p.bottom}/${p.left}/${p.right}/${p.header}/${p.footer}`,
      `${pm.top}/${pm.bottom}/${pm.left}/${pm.right}/${pm.header}/${pm.footer}`, bad.length ? 'Error' : null);
  }

  const [ls] = top(a.lineSpacing);
  add(2, '줄간격 (지배값 %)', String(ls), String(PRESET.lineSpacing), ls === PRESET.lineSpacing ? null : 'Error');

  const [sb] = top(a.spBefore), [sa] = top(a.spAfter);
  add(3, '문단간격 위/아래 (지배값)', `${sb}/${sa}`, `${PRESET.spacingBefore}/${PRESET.spacingAfter}`,
    (sb === PRESET.spacingBefore && sa === PRESET.spacingAfter) ? null : 'Warning');

  const [lsp] = top(a.letter);
  add(4, '자간 (지배값 %)', String(lsp), String(PRESET.letterSpacing), lsp === PRESET.letterSpacing ? null : 'Warning');

  const allowed = new Set(PRESET.bulletLevels.map((x) => x.join(',')));
  const strayB = [...a.bullets.keys()].filter((k) => !allowed.has(k));
  add(5, '글머리표 단계 (왼여백,내어쓰기)',
    a.bullets.size ? [...a.bullets.entries()].map(([k, v]) => `${k}×${v}`).join(' ') : '없음',
    PRESET.bulletLevels.map((x) => x.join(',')).join(' / '), strayB.length ? 'Warning' : null);

  const hc = a.headerCells;
  const fillOk = (a.headerFill.get(PRESET.headerFill) || 0);
  add(6, '표 헤더 채우기', hc ? `${PRESET.headerFill} ${fillOk}/${hc}` : '표 없음',
    `${PRESET.headerFill} 전부`, hc ? grade(fillOk / hc) : null);
  add(7, '표 헤더 볼드', hc ? `${a.headerBold}/${hc}` : '표 없음', '전부',
    hc ? grade(a.headerBold / hc) : null);
  add(8, '표 헤더 가운데 정렬', hc ? `${a.headerCenter}/${hc}` : '표 없음', '전부',
    hc ? grade(a.headerCenter / hc) : null);

  const [cm] = top(a.cellMargins);
  add(9, '표 셀 여백 (지배값 HWPUNIT)', hc ? String(cm) : '표 없음', '100~200 (0.35~0.70mm)',
    hc && (cm < PRESET.cellMarginRange[0] || cm > PRESET.cellMarginRange[1]) ? 'Warning' : null);

  const sh = a.sectionHeads;
  add(10, '가·나·다 헤딩 앞 빈 문단', `${sh.withBlank}/${sh.withBlank + sh.without}`, '전부',
    sh.without ? 'Warning' : null);

  add(11, '리터럴 번호 문단 (1. 2. 3.)',
    a.literalNumbered.length ? `${a.literalNumbered.length}건 — ${a.literalNumbered.slice(0, 2).join(' / ')}` : '0',
    '공고 원문 제목이 아니면 set_numbered_list', a.literalNumbered.length ? 'Warning' : null);

  const [bfs] = top(a.fontSize);
  add(12, '본문 글자 크기 (지배값 pt)',
    bfs ? (bfs / 100).toFixed(bfs % 100 ? 1 : 0) : '본문 없음',
    String(PRESET.bodyFontPt / 100),
    bfs && bfs !== PRESET.bodyFontPt ? 'Error' : null);

  return rows;
}

const args = process.argv.slice(2);
const file = args.find((x) => !x.startsWith('--'));
if (!file) { console.error('usage: node audit_format.js <file.hwp> [--preset preset.json] [--json]'); process.exit(2); }
const rows = checks(analyze(file));
if (args.includes('--json')) {
  console.log(JSON.stringify({ file, rows }, null, 2));
} else {
  const mark = (r) => (r.sev === 'Error' ? '❌' : r.sev === 'Warning' ? '⚠️ ' : '✅');
  console.log(`\n서식 프리셋 검사 — ${file}\n`);
  for (const r of rows) console.log(`${mark(r)} ${String(r.n).padStart(2)}. ${r.name}\n      실측 ${r.got}   기대 ${r.want}`);
  const e = rows.filter((r) => r.sev === 'Error').length, w = rows.filter((r) => r.sev === 'Warning').length;
  console.log(`\nError ${e} · Warning ${w}`);
  process.exit(e ? 1 : 0);
}
