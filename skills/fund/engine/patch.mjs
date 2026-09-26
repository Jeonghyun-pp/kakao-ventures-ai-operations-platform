#!/usr/bin/env node
// patch.mjs — 서식 hwp 를 바이트로 다루는 세 가지 일. 전부 2026-08~09 두 번의 실전(KIF·LP 성장펀드)에서 손으로 하던 것을 옮겼다.
//
//   truncate <hwp>                       서식 파일의 본문을 비운다(구역 설정 + 첫 문단만 남김). DocInfo(글꼴·테두리·문단모양)는 그대로 → 서식만 물려받은 빈 문서
//   learn    <hwp>                       서식 파일의 표에서 셀 테두리(BorderFill) 역할별 id 를 배운다 → JSON
//   patch    <hwp> <minPara> <profile>   minPara 뒤에 붙인 새 문단·표를 고친다: ① CharShape 글꼴 슬롯 7개를 한글 글꼴 기준으로 통일
//                                        ② 새 표 셀의 BorderFill 을 profile.bf 역할표로 ③ 불렛 문단의 LINE_SEG 캐시 삭제(내어쓰기가 살아나게)
//                                        ④ 새 문단만 쓰는 ParaShape 의 상속된 indent 를 0 으로. CFB 는 한 번만 쓴다(두 번 쓰면 깨졌다 — 9/10 실측)
//
// 왜 바이트인가: WASM(create.js) 은 새 BorderFill 을 DocInfo 에 남기지 못하고, append_paragraph 는 margin/indent 를 무시하고 마지막 문단의
// ParaShape 를 복제하며, apply_paragraph_style 은 ParaShape 만 바꾸고 줄 캐시(PARA_LINE_SEG)는 두어 한글이 옛 배치를 믿는다. 셋 다 9/9~9/10 실측.
import fs from "node:fs";
import zlib from "node:zlib";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CFB = createRequire(import.meta.url)(path.join(HERE, "hwp", "vendor", "cfb", "cfb.js"));
const T = { ID_MAPPINGS: 17, FACE_NAME: 19, BORDER_FILL: 20, CHAR_SHAPE: 21, PARA_SHAPE: 25, PARA_HEADER: 66, PARA_TEXT: 67, LINE_SEG: 69, CTRL_HEADER: 71, LIST_HEADER: 72 };

function records(buf) {
  const out = []; let p = 0;
  while (p + 4 <= buf.length) {
    const h = buf.readUInt32LE(p), tag = h & 0x3ff, level = (h >> 10) & 0x3ff;
    let size = (h >> 20) & 0xfff, hs = 4;
    if (size === 0xfff) { size = buf.readUInt32LE(p + 4); hs = 8; }
    out.push({ tag, level, size, hs, headOff: p, dataOff: p + hs });
    p += hs + size;
  }
  return out;
}

function open(fp) {
  const cfb = CFB.read(fs.readFileSync(fp), { type: "buffer" });
  const sec = CFB.find(cfb, "/BodyText/Section0"), di = CFB.find(cfb, "/DocInfo");
  if (!sec || !di) throw new Error("Section0/DocInfo 없음 — HWP 5.0 이 아니다");
  return { cfb, sec, di, secRaw: zlib.inflateRawSync(sec.content), diRaw: zlib.inflateRawSync(di.content) };
}

// 한글이 쓰는 압축 꼬리(CRC32 + 원길이, 각 4B LE). rhwp 는 이걸 안 붙이고, audit_structure 가 잡는다. 원본 서식 파일엔 있다(실측)
function deflate(buf) {
  const t = Buffer.alloc(8); t.writeUInt32LE(zlib.crc32(buf) >>> 0, 0); t.writeUInt32LE(buf.length >>> 0, 4);
  return Buffer.concat([zlib.deflateRawSync(buf), t]);
}

function save(fp, d, secBuf, diBuf) {
  if (secBuf) d.sec.content = deflate(secBuf);
  if (diBuf) d.di.content = deflate(diBuf);
  fs.writeFileSync(fp, CFB.write(d.cfb, { type: "buffer" }));
}

// 표마다 셀(LIST_HEADER, level 2)의 (col,row,colSpan,rowSpan,bf) — 실전 파일은 본문이 Section0 하나였다. 여러 구역이면 첫 구역만 본다
function tables(secRaw, recs) {
  const out = []; let para = 0, cur = null;
  for (const r of recs) {
    if (r.tag === T.PARA_HEADER && r.level === 0) { para++; if (cur) { out.push(cur); cur = null; } }
    if (r.tag === T.CTRL_HEADER && r.level === 1) { if (cur) out.push(cur); cur = { para, cells: [] }; }
    if (r.tag === T.LIST_HEADER && r.level === 2 && cur) {
      const b = secRaw.subarray(r.dataOff, r.dataOff + r.size);
      cur.cells.push({ col: b.readUInt16LE(8), row: b.readUInt16LE(10), colSpan: b.readUInt16LE(12), rowSpan: b.readUInt16LE(14), bf: b.readUInt16LE(32), off: r.dataOff + 32 });
    }
  }
  if (cur) out.push(cur);
  for (const t of out) {
    t.maxCol = Math.max(0, ...t.cells.map(c => c.col + c.colSpan - 1));
    t.maxRow = Math.max(0, ...t.cells.map(c => c.row + c.rowSpan - 1));
  }
  return out.filter(t => t.cells.length);
}

// 머리행이 몇 줄인가 — 첫 행부터 채움색이 이어지는 만큼(두 줄짜리 머리행). 첫 행은 무조건 머리행
function headerRows(t, filled) {
  let n = 1;
  while (n <= t.maxRow && t.cells.some(c => c.row === n) && t.cells.filter(c => c.row === n).every(c => filled.has(c.bf))) n++;
  return n;
}

// 셀의 역할 — 머리행 / 머리행 바로 아래 줄(겹선이 여기 붙는다) / 본문 / 마지막 행, 각각 좌·중·우
function role(c, t, hdr) {
  const side = c.col === 0 ? "left" : (c.col + c.colSpan - 1 >= t.maxCol ? "right" : "mid");
  const bottom = c.row + c.rowSpan - 1 >= t.maxRow;
  const band = c.row < hdr ? "hdr" : bottom ? "bot" : c.row === hdr ? "row1" : "body";
  return `${band}_${side}`;
}

// 채움색이 있는 BorderFill id — 머리행 판별. 레코드 배치(HWP 5.0): 속성2 + 테두리 4×6 + 대각선 6 → 32: 채움종류(bit0 단색) → 36: 배경색(BGR)
function filledIds(diRaw) {
  const out = new Set(); let idx = 0;
  for (const r of records(diRaw)) {
    if (r.tag !== T.BORDER_FILL) continue;
    idx++;
    if (r.size < 40) continue;
    const fill = diRaw.readUInt32LE(r.dataOff + 32), rgb = diRaw.readUInt32LE(r.dataOff + 36) & 0xffffff;
    if ((fill & 1) && rgb !== 0xffffff) out.add(idx);
  }
  return out;
}

function learn(fp) {
  const d = open(fp), recs = records(d.secRaw), filled = filledIds(d.diRaw);
  // 서식 파일에는 표 종류가 섞여 있다(안내 상자·신청서 격자·본문 표). 머리행에 채움색이 있는 표만이 본문 서식이고,
  // 그중 머리행 가운데 셀 테두리가 가장 흔한 '가족'에서 나머지 역할을 배운다
  const styled = tables(d.secRaw, recs).filter(t => t.maxRow >= 2 && t.cells.filter(c => c.row === 0).every(c => filled.has(c.bf)));
  const mid = {}; for (const t of styled) for (const c of t.cells) if (role(c, t, headerRows(t, filled)) === "hdr_mid") mid[c.bf] = (mid[c.bf] || 0) + 1;
  const best = Object.entries(mid).sort((a, b) => b[1] - a[1])[0];
  if (!best) return { tables_used: 0, bf: {}, votes: {} };
  const family = styled.filter(t => t.cells.some(c => c.bf === Number(best[0])));
  const votes = {};
  for (const t of family) {
    const hdr = headerRows(t, filled);
    for (const c of t.cells) { const k = role(c, t, hdr); (votes[k] ??= {})[c.bf] = (votes[k][c.bf] || 0) + 1; }
  }
  const bf = {};
  for (const [k, v] of Object.entries(votes)) bf[k] = Number(Object.entries(v).sort((a, b) => b[1] - a[1])[0][0]);
  for (const side of ["left", "mid", "right"]) {                                       // 못 본 역할은 본문 값으로 — 표가 두 행뿐인 서식도 있다
    bf[`body_${side}`] ??= bf.body_mid ?? bf.body_left;
    bf[`row1_${side}`] ??= bf[`body_${side}`]; bf[`bot_${side}`] ??= bf[`body_${side}`]; bf[`hdr_${side}`] ??= bf[`body_${side}`];
  }
  return { tables_used: family.length, family: `hdr_mid=${best[0]}`, bf, votes };
}

function truncate(fp) {
  const d = open(fp), recs = records(d.secRaw);
  const parts = []; let inFirst = false;
  for (const r of recs) {
    if (r.tag === T.PARA_HEADER && r.level === 0) { if (inFirst) break; inFirst = true; }
    if (inFirst && r.tag === T.CTRL_HEADER && r.level === 1) {   // 구역·단 정의(secd·cold)는 쪽 여백을 품으니 남긴다. 표·그림은 버린다
      const id = Buffer.from(d.secRaw.subarray(r.dataOff, r.dataOff + 4)).reverse().toString("latin1");
      if (id === "tbl " || id === "gso ") break;
    }
    parts.push(d.secRaw.subarray(r.headOff, r.headOff + r.hs + r.size));
  }
  const trimmed = Buffer.concat(parts);
  save(fp, d, trimmed, null);
  return { original: d.secRaw.length, trimmed: trimmed.length };
}

function faceGroups(diRaw) {
  const recs = records(diRaw);
  const idm = recs.find(r => r.tag === T.ID_MAPPINGS);
  const counts = []; for (let i = 1; i <= 7; i++) counts.push(diRaw.readInt32LE(idm.dataOff + i * 4));
  const faces = recs.filter(r => r.tag === T.FACE_NAME).map(r => { const n = diRaw.readUInt16LE(r.dataOff + 1); return diRaw.subarray(r.dataOff + 3, r.dataOff + 3 + n * 2).toString("utf16le"); });
  const starts = []; let cum = 0; for (const c of counts) { starts.push(cum); cum += c; }
  const nameToId = counts.map((c, g) => { const m = {}; for (let i = 0; i < c; i++) m[faces[starts[g] + i]] = i; return m; });
  const koName = {}; for (let i = 0; i < counts[0]; i++) koName[i] = faces[starts[0] + i];
  return { nameToId, koName };
}

function patch(fp, minPara, profile) {
  const d = open(fp);
  const di = Buffer.from(d.diRaw), sec = Buffer.from(d.secRaw);
  const diRecs = records(di), secRecs = records(sec);
  // ① 글꼴 슬롯 — CharShape 의 영문·한자·일어·기타·기호·사용자 슬롯을 한글 슬롯과 같은 이름으로(rhwp 가 0 으로 두어 영문·숫자가 다른 글꼴로 찍혔다)
  const { nameToId, koName } = faceGroups(di);
  let fontPatched = 0, fontTotal = 0;
  for (const r of diRecs) {
    if (r.tag !== T.CHAR_SHAPE) continue;
    fontTotal++;
    const name = koName[di.readUInt16LE(r.dataOff)];
    if (!name) continue;
    let changed = false;
    for (let g = 1; g < 7; g++) {
      const want = nameToId[g][name];
      if (want !== undefined && di.readUInt16LE(r.dataOff + g * 2) !== want) { di.writeUInt16LE(want, r.dataOff + g * 2); changed = true; }
    }
    if (changed) fontPatched++;
  }
  // ② 새 표 셀 테두리 — 머리행 채움색이 이미 붙은 셀(다단 머리행)은 머리행으로 유지
  const fills = filledIds(di);
  let cellPatched = 0, tablesPatched = 0;
  for (const t of tables(sec, secRecs)) {
    if (t.para <= minPara) continue;
    tablesPatched++;
    const hdr = headerRows(t, fills);
    for (const c of t.cells) {
      const id = profile.bf[role(c, t, hdr)];
      if (id !== undefined) { sec.writeUInt16LE(id, c.off); cellPatched++; }
    }
  }
  // ③ 불렛 문단의 LINE_SEG 삭제 + lineSegsCount=0 → 한글이 내어쓰기로 재배치한다(레코드는 남기고 count 만 0 이면 거부됐다 — 9/9 실측)
  const prefixes = profile.bullet_prefixes || [];
  const targets = []; let lv0 = 0;
  for (let i = 0; i < secRecs.length; i++) {
    const r = secRecs[i];
    if (r.tag !== T.PARA_HEADER || r.level !== 0) continue;
    lv0++;
    if (lv0 <= minPara) continue;
    let txt = "", ls = null;
    for (let j = i + 1; j < secRecs.length && secRecs[j].tag !== T.PARA_HEADER; j++) {
      if (secRecs[j].tag === T.PARA_TEXT && !txt) txt = sec.subarray(secRecs[j].dataOff, secRecs[j].dataOff + secRecs[j].size).toString("utf16le").replace(/[\x00-\x1f]/g, "");
      if (secRecs[j].tag === T.LINE_SEG && !ls) ls = secRecs[j];
    }
    if (ls && prefixes.some(p => txt.startsWith(p))) targets.push({ ph: r, ls });
  }
  let secOut = sec;
  for (let k = targets.length - 1; k >= 0; k--) {
    const { ph, ls } = targets[k];
    secOut = Buffer.concat([secOut.subarray(0, ls.headOff), secOut.subarray(ls.dataOff + ls.size)]);
    if (ph.size >= 18) secOut.writeUInt16LE(0, ph.dataOff + 16);
  }
  // ④ 새 문단만 쓰는 ParaShape 의 indent — 서식 마지막 문단에서 물려받은 값(예: -6090)을 0 으로. 옛 문단과 공유하는 모양은 건드리지 않는다
  const newPs = new Set(), oldPs = new Set(); lv0 = 0; let isNew = false;
  for (const r of secRecs) {
    if (r.tag !== T.PARA_HEADER) continue;
    if (r.level === 0) { lv0++; isNew = lv0 > minPara; }
    (isNew ? newPs : oldPs).add(sec.readUInt16LE(r.dataOff + 8));
  }
  for (const id of oldPs) newPs.delete(id);
  let indentPatched = 0, psIdx = 0;
  for (const r of diRecs) {
    if (r.tag !== T.PARA_SHAPE) continue;
    if (newPs.has(psIdx) && di.readInt32LE(r.dataOff + 4) === 0 && di.readInt32LE(r.dataOff + 12) !== 0) { di.writeInt32LE(0, r.dataOff + 12); indentPatched++; }
    psIdx++;
  }
  save(fp, d, secOut, di);
  return { fonts: { total: fontTotal, patched: fontPatched }, cells: { tables: tablesPatched, patched: cellPatched }, lineseg_trimmed: targets.length, indent_patched: indentPatched };
}

const [mode, fp, a, b] = process.argv.slice(2);
try {
  let out;
  if (mode === "truncate") out = truncate(fp);
  else if (mode === "learn") out = learn(fp);
  else if (mode === "patch") out = patch(fp, parseInt(a, 10), JSON.parse(fs.readFileSync(b, "utf8")));
  else throw new Error("usage: patch.mjs truncate|learn|patch <hwp> [minPara profile.json]");
  console.log(JSON.stringify({ status: "success", ...out }));
} catch (e) {
  console.log(JSON.stringify({ status: "error", message: String(e && e.message || e) }));
  process.exit(1);
}
