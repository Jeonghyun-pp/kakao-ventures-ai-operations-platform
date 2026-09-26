#!/usr/bin/env node
// format-learn.js — 레퍼런스 HWP 에서 서식 프리셋 JSON 을 자동 추출
//
//   node scripts/format-learn.js <reference.hwp> [--json] [--out preset.json]
//
// audit_format.js 의 검증된 파서를 기반으로 확장.
// 산출물: 서식 프리셋 JSON + 사용자 확인 필요 항목 리스트.

import { readFileSync, writeFileSync } from 'node:fs';
import { inflateRawSync } from 'node:zlib';
import { resolve, basename } from 'node:path';
import * as CFB from './vendor/cfb/cfb.js';

const TAG = {
  FACE_NAME: 19,
  BORDER_FILL: 20, CHAR_SHAPE: 21, PARA_SHAPE: 25,
  ID_MAPPINGS: 17,
  PARA_HEADER: 66, PARA_TEXT: 67, PARA_CHAR_SHAPE: 68,
  LIST_HEADER: 72, PAGE_DEF: 73, CTRL_HEADER: 71,
};
const HWPUNIT_PER_MM = 7200 / 25.4;
const WIDE_CTRL = new Set([1,2,3,4,5,6,7,8,11,12,14,15,16,17,18,19,20,21,22,23]);

const ALIGN_NAMES = ['justify','left','right','center','distribute','split'];

// ── record walker (from audit_format.js) ──
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

function textDecode(buf, off, size) {
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

// ── helpers ──
const bump = (m, k) => m.set(k, (m.get(k) || 0) + 1);
const top = (m) => [...m.entries()].sort((a, b) => b[1] - a[1])[0] || [null, 0];
const topN = (m, n) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);

// ── DocInfo parsers ──

function parseFaceNames(di) {
  const names = [];  // index → name (per-slot, but we collect all in order)
  for (const r of walk(di)) {
    if (r.tag === TAG.FACE_NAME && r.size >= 6) {
      const attr = di.readUInt8(r.off);
      const nameLen = di.readUInt16LE(r.off + 1);
      let name = '';
      for (let j = 0; j < nameLen && (r.off + 3 + j * 2 + 1) < r.off + r.size; j++) {
        name += String.fromCharCode(di.readUInt16LE(r.off + 3 + j * 2));
      }
      names.push(name);
    }
  }
  return names;
}

function parseIdMappings(di) {
  for (const r of walk(di)) {
    if (r.tag === TAG.ID_MAPPINGS && r.size >= 8) {
      const nBinData = di.readInt32LE(r.off);
      const counts = [];
      for (let j = 0; j < Math.min(r.size / 4, 20); j++) {
        counts.push(di.readInt32LE(r.off + j * 4));
      }
      return counts;
    }
  }
  return [];
}

function resolveFontName(faceNames, idMappings, csOff, di, slot) {
  // CharShape has 7 font face ID refs at offsets 0..13 (UINT16 each)
  // slot 0=한글, 1=영문, 2=한자, 3=일어, 4=기타, 5=기호, 6=사용자
  if (csOff === undefined) return null;
  const faceId = di.readUInt16LE(csOff + slot * 2);
  // face IDs are per-slot: each slot has its own count in ID_MAPPINGS
  // ID_MAPPINGS order: binData(1), hangulFace(2), englishFace(3), hanjaFace(4), japaneseFace(5), otherFace(6), symbolFace(7), userFace(8), ...
  // So slot 0 (한글) faces start at index = sum of counts before slot 2
  // But typically all slots share the same face name table in practice.
  // Simpler approach: face names are stored in slot order, each slot has idMappings[slot+1] entries
  const slotCounts = [];
  for (let s = 0; s < 7; s++) slotCounts.push(idMappings[s + 1] || 0);
  let base = 0;
  for (let s = 0; s < slot; s++) base += slotCounts[s];
  const idx = base + faceId;
  return faceNames[idx] || `[face ${faceId} slot ${slot}]`;
}

function fillColor(di, bf, id) {
  if (id < 1 || id > bf.length) return null;
  const p = bf[id - 1] + 2 + 24 + 6;
  const type = di.readUInt32LE(p);
  if (!(type & 1)) return null;
  const c = di.readUInt32LE(p + 4);
  return '#' + [c & 255, (c >> 8) & 255, (c >> 16) & 255]
    .map((v) => v.toString(16).toUpperCase().padStart(2, '0')).join('');
}

// ── heading pattern matchers ──
const HEADING_PATTERNS = [
  { pattern: 'roman',  regex: /^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.\s/, label: '대분류 Ⅰ.Ⅱ.…' },
  { pattern: 'num.',   regex: /^\d{1,2}\.\s/, label: '항목 1. 2.…', minLen: 10 },
  { pattern: 'num)',   regex: /^\d+\)\s/, label: '1) 2) 3)…' },
  { pattern: 'ka.',    regex: /^[가-하]\.\s/, label: '가. 나. 다.…' },
  { pattern: '(num)',  regex: /^\(\d+\)\s/, label: '(1) (2)…' },
  { pattern: '(ka)',   regex: /^\([가-하]\)\s/, label: '(가) (나)…' },
  { pattern: '[title]', regex: /^\[.+\]/, label: '표 제목 […]' },
  { pattern: '*note',  regex: /^\*/, label: '주석 *…' },
];

// ── main analyze ──
function analyze(file) {
  const { di, bodies } = load(file);

  // DocInfo indexes
  const psOffsets = [], csOffsets = [], bfOffsets = [];
  for (const r of walk(di)) {
    if (r.tag === TAG.PARA_SHAPE) psOffsets.push(r.off);
    else if (r.tag === TAG.CHAR_SHAPE) csOffsets.push(r.off);
    else if (r.tag === TAG.BORDER_FILL) bfOffsets.push(r.off);
  }

  const faceNames = parseFaceNames(di);
  const idMappings = parseIdMappings(di);

  // ── page setup ──
  let page = null;
  for (const body of bodies) {
    for (const r of walk(body)) {
      if (r.tag === TAG.PAGE_DEF && r.size >= 32) {
        const g = (k) => +(body.readUInt32LE(r.off + k) / HWPUNIT_PER_MM).toFixed(1);
        const w = +(body.readUInt32LE(r.off + 0) / HWPUNIT_PER_MM).toFixed(1);
        const h = +(body.readUInt32LE(r.off + 4) / HWPUNIT_PER_MM).toFixed(1);
        page = { width: w, height: h, left: g(8), right: g(12), top: g(16), bottom: g(20), header: g(24), footer: g(28) };
        break;
      }
    }
    if (page) break;
  }

  // ── body paragraph collection ──
  const bodyStats = {
    lineSpacing: new Map(), spBefore: new Map(), spAfter: new Map(),
    letterSpacing: new Map(), fontSize: new Map(), fontKo: new Map(), fontEn: new Map(),
    alignment: new Map(), charRatio: new Map(),
    bulletLevels: new Map(),
  };

  // ── cell paragraph collection ──
  const cellStats = {
    fontSize: new Map(), fontKo: new Map(), fontEn: new Map(),
    lineSpacing: new Map(), alignment: new Map(), letterSpacing: new Map(),
  };

  // ── table header collection ──
  const tableStats = {
    headerCells: 0, headerFill: new Map(), headerBold: 0, headerCenter: 0,
    cellMargins: new Map(),
    allCellMargins: [],  // [l, r, t, b] per cell for analysis
  };

  // ── heading collection ──
  const headingData = {};
  for (const hp of HEADING_PATTERNS) headingData[hp.pattern] = { count: 0, sizes: new Map(), bold: 0, nonBold: 0, underline: 0, align: new Map(), blankBefore: 0, total: 0 };

  // ── cell bullet collection ──
  const cellBulletStats = { count: 0, marginLeft: new Map(), indent: new Map(), align: new Map() };

  // ── section heading blank-before ──
  const sectionHeadBlank = { withBlank: 0, without: 0 };

  // helper to read CharShape props
  const csProps = (id) => {
    const o = csOffsets[id];
    if (o === undefined) return null;
    const sizePt100 = di.readUInt16LE(o + 42);
    const attrs = di.readUInt32LE(o + 46);
    const bold = !!((attrs >> 1) & 1);
    const italic = !!(attrs & 1);
    const underlineType = (attrs >> 2) & 3;
    const koFont = resolveFontName(faceNames, idMappings, o, di, 0);
    const enFont = resolveFontName(faceNames, idMappings, o, di, 1);
    const letterSp = di.readInt8(o + 21);
    return { sizePt100, bold, italic, underline: underlineType > 0, koFont, enFont, letterSp };
  };

  const psProps = (id) => {
    const o = psOffsets[id];
    if (o === undefined) return null;
    const attr = di.readUInt32LE(o);
    const alignIdx = (attr >> 2) & 7;
    const headingType = (attr >> 23) & 3;  // 3 = 글머리
    const marginLeft = di.readInt32LE(o + 4);
    const marginRight = di.readInt32LE(o + 8);
    const indent = di.readInt32LE(o + 12);
    const lineSpacing = di.readInt32LE(o + 24);
    const spBefore = di.readInt32LE(o + 16);
    const spAfter = di.readInt32LE(o + 20);
    return { align: ALIGN_NAMES[alignIdx] || String(alignIdx), headingType, marginLeft, marginRight, indent, lineSpacing, spBefore, spAfter };
  };

  for (const body of bodies) {
    const recs = [...walk(body)];
    const bodyParas = [];
    let pending = null;
    let paraLevel = -1;
    let lastCsId = -1;

    for (let i = 0; i < recs.length; i++) {
      const r = recs[i];

      if (r.tag === TAG.PARA_HEADER) {
        const psId = body.readUInt16LE(r.off + 8);
        pending = { level: r.level, psId, text: '', csIds: [] };
        paraLevel = r.level;
        if (r.level === 0) bodyParas.push(pending);

      } else if (r.tag === TAG.PARA_TEXT && pending) {
        pending.text = textDecode(body, r.off, r.size);

        const ps = psProps(pending.psId);

        if (pending.level === 0 && ps) {
          // body paragraph stats
          bump(bodyStats.lineSpacing, ps.lineSpacing);
          bump(bodyStats.spBefore, ps.spBefore);
          bump(bodyStats.spAfter, ps.spAfter);
          bump(bodyStats.alignment, ps.align);
          if (ps.headingType === 3) {
            bump(bodyStats.bulletLevels, `${ps.marginLeft},${ps.indent}`);
          }
        } else if (pending.level >= 1 && ps) {
          // cell paragraph stats
          bump(cellStats.lineSpacing, ps.lineSpacing);
          bump(cellStats.alignment, ps.align);

          // cell bullet detection: level ≥ 1, text starts with bullet prefix
          const trimmed = pending.text.trim();
          if (/^[•·▪\-–]/.test(trimmed) && ps) {
            cellBulletStats.count++;
            bump(cellBulletStats.marginLeft, ps.marginLeft);
            bump(cellBulletStats.indent, ps.indent);
            bump(cellBulletStats.align, ps.align);
          }
        }

      } else if (r.tag === TAG.PARA_CHAR_SHAPE && pending) {
        for (let k = 0; k < r.size / 8; k++) {
          const csId = body.readUInt32LE(r.off + 8 * k + 4);
          pending.csIds.push(csId);
          const cp = csProps(csId);
          if (!cp) continue;

          if (paraLevel === 0) {
            bump(bodyStats.fontSize, cp.sizePt100);
            bump(bodyStats.fontKo, cp.koFont);
            bump(bodyStats.fontEn, cp.enFont);
            bump(bodyStats.letterSpacing, cp.letterSp);
          } else if (paraLevel >= 1) {
            bump(cellStats.fontSize, cp.sizePt100);
            bump(cellStats.fontKo, cp.koFont);
            bump(cellStats.fontEn, cp.enFont);
            bump(cellStats.letterSpacing, cp.letterSp);
          }
        }

      } else if (r.tag === TAG.LIST_HEADER && r.size >= 34) {
        const row = body.readUInt16LE(r.off + 10);
        const bfId = body.readUInt16LE(r.off + 32);
        if (bfId === 0) continue;

        // cell margins (all cells, not just header)
        const margins = [];
        for (let k = 0; k < 4; k++) margins.push(body.readUInt16LE(r.off + 24 + 2 * k));

        if (row === 0) {
          // header row
          tableStats.headerCells++;
          bump(tableStats.headerFill, fillColor(di, bfOffsets, bfId));
          for (const m of margins) bump(tableStats.cellMargins, m);
          tableStats.allCellMargins.push(margins);

          // check bold & center in next records
          for (let j = i + 1; j < Math.min(i + 6, recs.length); j++) {
            const q = recs[j];
            if (q.tag === TAG.PARA_HEADER) {
              const psp = psProps(body.readUInt16LE(q.off + 8));
              if (psp && psp.align === 'center') tableStats.headerCenter++;
            } else if (q.tag === TAG.PARA_CHAR_SHAPE) {
              let bold = q.size > 0;
              for (let k = 0; k < q.size / 8; k++) {
                const id = body.readUInt32LE(q.off + 8 * k + 4);
                const cp = csProps(id);
                if (!cp || !cp.bold) bold = false;
              }
              if (bold) tableStats.headerBold++;
              break;
            }
          }
        }
      }
    }

    // heading analysis — scan body paragraphs
    for (let i = 0; i < bodyParas.length; i++) {
      const bp = bodyParas[i];
      const t = bp.text.trim();

      for (const hp of HEADING_PATTERNS) {
        if (hp.minLen && t.length < hp.minLen) continue;
        if (!hp.regex.test(t)) continue;

        const hd = headingData[hp.pattern];
        hd.count++;
        hd.total++;

        // read CharShape of first run
        if (bp.csIds.length > 0) {
          const cp = csProps(bp.csIds[0]);
          if (cp) {
            bump(hd.sizes, cp.sizePt100);
            if (cp.bold) hd.bold++;
            else hd.nonBold++;
            if (cp.underline) hd.underline++;
          }
        }

        // alignment
        const psp = psProps(bp.psId);
        if (psp) bump(hd.align, psp.align);

        // blank before (for 가.나.다. and similar)
        if (hp.pattern === 'ka.') {
          if (i > 0 && bodyParas[i - 1].text.trim() === '') sectionHeadBlank.withBlank++;
          else sectionHeadBlank.without++;
        }

        break;  // first match wins
      }
    }
  }

  return { page, bodyStats, cellStats, tableStats, headingData, cellBulletStats, sectionHeadBlank, faceNames: faceNames.length };
}

// ── build preset JSON ──
function buildPreset(file, a) {
  const warnings = [];
  const confirms = [];

  // page
  const p = a.page || {};
  const bodyWidth = p.width ? +(p.width - (p.left || 0) - (p.right || 0)).toFixed(1) : null;
  const pageSize = (Math.abs((p.width || 0) - 210) < 1 && Math.abs((p.height || 0) - 297) < 1) ? 'a4'
    : (Math.abs((p.width || 0) - 182) < 1 && Math.abs((p.height || 0) - 257) < 1) ? 'b5' : 'custom';
  const orientation = (p.width || 0) > (p.height || 0) ? 'landscape' : 'portrait';

  // body
  const [bodyFontPt] = top(a.bodyStats.fontSize);
  const [bodyFontKo] = top(a.bodyStats.fontKo);
  const [bodyFontEn] = top(a.bodyStats.fontEn);
  const [lineSpacing] = top(a.bodyStats.lineSpacing);
  const [bodyAlign] = top(a.bodyStats.alignment);
  const [letterSp] = top(a.bodyStats.letterSpacing);
  const [spBefore] = top(a.bodyStats.spBefore);
  const [spAfter] = top(a.bodyStats.spAfter);

  // gap method detection
  let gapMethod = 'unknown';
  if (spBefore === 0 && spAfter === 0) gapMethod = 'blank_paragraph';
  else if (spBefore > 0 || spAfter > 0) gapMethod = 'paragraph_spacing';

  // cell
  const [cellFontPt] = top(a.cellStats.fontSize);
  const [cellFontKo] = top(a.cellStats.fontKo);
  const [cellLineSpacing] = top(a.cellStats.lineSpacing);
  const [cellLetterSp] = top(a.cellStats.letterSpacing);

  // table header
  const ts = a.tableStats;
  const [headerFill] = top(ts.headerFill);
  const [cellMargin] = top(ts.cellMargins);

  // headings
  const headings = [];
  for (const hp of HEADING_PATTERNS) {
    const hd = a.headingData[hp.pattern];
    if (hd.count === 0) continue;
    const [sizePt100] = top(hd.sizes);
    const [align] = top(hd.align);
    const entry = {
      pattern: hp.pattern,
      label: hp.label,
      count: hd.count,
      sizePt: sizePt100 ? sizePt100 / 100 : null,
      bold: hd.bold > hd.nonBold,
      boldRatio: hd.total > 0 ? `${hd.bold}/${hd.total}` : '0/0',
      underline: hd.underline > 0,
      underlineRatio: hd.total > 0 ? `${hd.underline}/${hd.total}` : '0/0',
      align,
    };
    if (hp.pattern === 'ka.') {
      entry.blankBefore = a.sectionHeadBlank.withBlank > 0;
      entry.blankBeforeRatio = `${a.sectionHeadBlank.withBlank}/${a.sectionHeadBlank.withBlank + a.sectionHeadBlank.without}`;
    }
    headings.push(entry);

    // confidence check
    if (hd.bold > 0 && hd.nonBold > 0) {
      confirms.push(`헤딩 "${hp.label}": 볼드 ${hd.bold}개, 비볼드 ${hd.nonBold}개 — 혼재. 의도 확인 필요`);
    }
  }

  // body bullets
  const bulletLevels = topN(a.bodyStats.bulletLevels, 5).map(([k, v]) => {
    const [ml, ind] = k.split(',').map(Number);
    return { marginLeft: ml, indent: ind, count: v };
  });

  // cell bullets
  const cb = a.cellBulletStats;
  let cellBullet = null;
  if (cb.count > 0) {
    const [cbMl] = top(cb.marginLeft);
    const [cbInd] = top(cb.indent);
    const [cbAlign] = top(cb.align);
    cellBullet = {
      detected: cb.count,
      marginLeft: cbMl,
      indent: cbInd,
      indentMm: cbInd !== null ? +(-cbInd / HWPUNIT_PER_MM).toFixed(2) : null,
      align: cbAlign,
    };
  } else {
    warnings.push('셀 불렛 0개 감지 — 레퍼런스에 셀 불렛이 없으면 본문 폰트 기준으로 계산 필요 (캡처 실측 권장)');
  }

  // image rules (derived)
  const image = {
    maxWidthMm: bodyWidth,
    maxHeightMm: 120,
    aspectTolerance: 0.02,
  };

  // build confirms list
  if (headings.length === 0) confirms.push('헤딩 패턴이 하나도 감지되지 않음 — 문서에 정형 헤딩이 없거나 패턴이 다름');
  if (gapMethod === 'unknown') confirms.push('문단 간격 방식 판단 불가');
  confirms.push('헤딩 계층 감지 결과 — "이게 헤딩인가 굵은 본문인가" 확인 필요');

  const preset = {
    _meta: {
      source: basename(file),
      extractedAt: new Date().toISOString(),
      tool: 'format-learn.js',
      note: 'auto-extracted — needsConfirm 항목은 사용자 확인 후 확정',
    },

    page: {
      size: pageSize,
      orientation,
      widthMm: p.width || null,
      heightMm: p.height || null,
      topMm: p.top || null,
      bottomMm: p.bottom || null,
      leftMm: p.left || null,
      rightMm: p.right || null,
      headerMm: p.header || null,
      footerMm: p.footer || null,
    },
    bodyWidthMm: bodyWidth,

    body: {
      fontPt: bodyFontPt ? bodyFontPt / 100 : null,
      fontPtRaw: bodyFontPt,
      fontKo: bodyFontKo,
      fontEn: bodyFontEn,
      lineSpacing,
      alignment: bodyAlign,
      letterSpacing: letterSp,
      spacingBefore: spBefore,
      spacingAfter: spAfter,
      gapMethod,
    },

    headings,

    table: {
      headerFillColor: headerFill,
      headerBold: ts.headerCells > 0 ? ts.headerBold / ts.headerCells >= 0.5 : null,
      headerBoldRatio: ts.headerCells > 0 ? `${ts.headerBold}/${ts.headerCells}` : null,
      headerAlign: ts.headerCells > 0 && ts.headerCenter / ts.headerCells >= 0.5 ? 'center' : null,
      headerAlignRatio: ts.headerCells > 0 ? `${ts.headerCenter}/${ts.headerCells}` : null,
      cellFontPt: cellFontPt ? cellFontPt / 100 : null,
      cellFontPtRaw: cellFontPt,
      cellFontKo: cellFontKo,
      cellLineSpacing: cellLineSpacing,
      cellLetterSpacing: cellLetterSp,
      cellMarginHwpunit: cellMargin,
      cellMarginMm: cellMargin ? +(cellMargin / HWPUNIT_PER_MM).toFixed(2) : null,
      headerCellCount: ts.headerCells,
      conformOk: 0.95,
      conformWarn: 0.85,
    },

    cellBullet: cellBullet || {
      detected: 0,
      note: '레퍼런스에 셀 불렛 없음 — 수동 설정 필요',
    },

    bodyBulletLevels: bulletLevels,

    image,

    needsConfirm: confirms,
    warnings,
  };

  return preset;
}

// ── CLI ──
const args = process.argv.slice(2);
const file = args.find((x) => !x.startsWith('--'));
if (!file) {
  console.error('usage: node format-learn.js <reference.hwp> [--json] [--out preset.json]');
  process.exit(2);
}

const result = analyze(resolve(file));
const preset = buildPreset(file, result);

const outIdx = args.indexOf('--out');
if (outIdx >= 0 && args[outIdx + 1]) {
  writeFileSync(args[outIdx + 1], JSON.stringify(preset, null, 2) + '\n');
  console.error(`프리셋 저장: ${args[outIdx + 1]}`);
}

if (args.includes('--json')) {
  console.log(JSON.stringify(preset, null, 2));
} else {
  // human-readable report
  console.log(`\n══════════════════════════════════════════════════`);
  console.log(`  서식 프리셋 추출 — ${preset._meta.source}`);
  console.log(`══════════════════════════════════════════════════\n`);

  // 1. page
  const pg = preset.page;
  console.log(`[1] 쪽 설정`);
  console.log(`    용지: ${pg.size.toUpperCase()} ${pg.widthMm}×${pg.heightMm}mm ${pg.orientation}`);
  console.log(`    여백: 위${pg.topMm} 아래${pg.bottomMm} 왼${pg.leftMm} 오${pg.rightMm} 머리${pg.headerMm} 꼬리${pg.footerMm}`);
  console.log(`    본문 폭: ${preset.bodyWidthMm}mm`);

  // 2. body
  const b = preset.body;
  console.log(`\n[2] 본문`);
  console.log(`    글꼴: ${b.fontKo} / ${b.fontEn}  ${b.fontPt}pt`);
  console.log(`    줄간격: ${b.lineSpacing}%  정렬: ${b.alignment}  자간: ${b.letterSpacing}%`);
  console.log(`    문단간격: 위${b.spacingBefore} 아래${b.spacingAfter}  방식: ${b.gapMethod}`);

  // 3. headings
  console.log(`\n[3] 헤딩 계층`);
  if (preset.headings.length === 0) {
    console.log(`    (감지된 헤딩 없음)`);
  } else {
    for (const h of preset.headings) {
      let line = `    ${h.label.padEnd(16)} ${String(h.sizePt || '?').padStart(4)}pt  `;
      line += h.bold ? '볼드' : '일반';
      line += `(${h.boldRatio})`;
      if (h.underline) line += ` 밑줄(${h.underlineRatio})`;
      line += `  ${h.align}`;
      if (h.blankBefore !== undefined) line += `  앞빈문단: ${h.blankBeforeRatio}`;
      line += `  (${h.count}건)`;
      console.log(line);
    }
  }

  // 4. table
  const t = preset.table;
  console.log(`\n[4] 표`);
  console.log(`    헤더행: 채우기${t.headerFillColor || '없음'} 볼드${t.headerBoldRatio || '-'} 가운데${t.headerAlignRatio || '-'}  (${t.headerCellCount}셀)`);
  console.log(`    셀 글꼴: ${t.cellFontKo} ${t.cellFontPt}pt  줄간격${t.cellLineSpacing}%  자간${t.cellLetterSpacing}%`);
  console.log(`    셀 여백: ${t.cellMarginHwpunit} HWPUNIT (${t.cellMarginMm}mm)`);

  // 5. cell bullet
  console.log(`\n[5] 셀 불렛`);
  const cb = preset.cellBullet;
  if (cb.detected > 0) {
    console.log(`    ${cb.detected}개 감지  왼여백${cb.marginLeft} 내어쓰기${cb.indent}(${cb.indentMm}mm)  정렬:${cb.align}`);
  } else {
    console.log(`    감지 0개 — ${cb.note || '수동 설정 필요'}`);
  }

  // 6. body bullets
  console.log(`\n[6] 본문 글머리표`);
  if (preset.bodyBulletLevels.length === 0) {
    console.log(`    (글머리표 없음)`);
  } else {
    for (const bl of preset.bodyBulletLevels) {
      console.log(`    왼여백${String(bl.marginLeft).padStart(5)} 내어쓰기${String(bl.indent).padStart(6)}  (${bl.count}건)`);
    }
  }

  // 7. image
  console.log(`\n[7] 이미지 (산출값)`);
  console.log(`    최대 가로: ${preset.image.maxWidthMm}mm  최대 세로: ${preset.image.maxHeightMm}mm  비율오차 한도: ${preset.image.aspectTolerance * 100}%`);

  // confirms & warnings
  if (preset.needsConfirm.length > 0) {
    console.log(`\n⚠️  사용자 확인 필요 (${preset.needsConfirm.length}건):`);
    for (const c of preset.needsConfirm) console.log(`    → ${c}`);
  }
  if (preset.warnings.length > 0) {
    console.log(`\n⚠️  경고 (${preset.warnings.length}건):`);
    for (const w of preset.warnings) console.log(`    → ${w}`);
  }

  console.log('');
}
