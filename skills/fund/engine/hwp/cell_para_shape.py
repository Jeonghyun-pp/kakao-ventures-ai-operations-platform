#!/usr/bin/env python3
"""표 셀 문단이 쓰는 ParaShape id 와 그 기하를 찍는다.

`clone_para_shape` 는 `source_id` 를 요구하는데 정작 그 번호를 **어디서 읽는지** 어느 문서에도
없었다 — `extract_text.js --inspect` 는 셀 텍스트만 주고 문단모양 번호를 주지 않는다.
그래서 매번 바이너리 파서를 새로 짜는 우회로가 반복됐다. 이 스크립트가 그 한 단계를 없앤다.

    python3 cell_para_shape.py 파일.hwp              # 지배 PS 요약
    python3 cell_para_shape.py 파일.hwp --all        # 셀 문단 전부
    python3 cell_para_shape.py 파일.hwp --bullet     # 불렛(•) 문단만

정렬 코드: 0=양쪽 1=좌 2=우 3=가운데 4=배분 5=나눔
"""
import argparse, os, struct, sys, zlib
from collections import Counter

try:
    import olefile
except ImportError:
    sys.exit('olefile 이 필요하다:  pip install olefile')

ALIGN = {0: '양쪽', 1: '좌', 2: '우', 3: '가운데', 4: '배분', 5: '나눔'}
EXT_CTRL = set(range(1, 9)) | {11, 12} | set(range(14, 24))   # 각 8 WCHAR 를 차지


def para_text(words):
    """PARA_TEXT 워드열에서 진짜 글자만 뽑는다.

    확장 컨트롤은 8 WCHAR 를 차지하고 그 안에 `secd`·`cold` 같은 ASCII 태그가 들어 있다.
    `c > 31` 로만 거르면 태그가 본문 글자로 섞인다(실측: 첫 문단이 `捤獥汤捯…` 로 읽혔다)."""
    out, i = [], 0
    while i < len(words):
        c = words[i]
        if c in EXT_CTRL:
            i += 8
            continue
        if c > 31:
            out.append(chr(c))
        i += 1
    return ''.join(out)


def walk(b):
    p = 0
    while p + 4 <= len(b):
        h = struct.unpack_from('<I', b, p)[0]
        tag, lvl, sz = h & 0x3ff, (h >> 10) & 0x3ff, (h >> 20) & 0xfff
        p += 4
        if sz == 0xfff:
            sz = struct.unpack_from('<I', b, p)[0]; p += 4
        yield tag, lvl, p, sz
        p += sz


def collect(path):
    o = olefile.OleFileIO(path)
    try:
        di = zlib.decompress(o.openstream('DocInfo').read(), -15)
        secs = sorted(n for n in ('/'.join(e) for e in o.listdir())
                      if n.startswith('BodyText/Section'))
        bts = [zlib.decompress(o.openstream(n).read(), -15) for n in secs]
    finally:
        o.close()

    shapes = []
    for tag, lvl, off, sz in walk(di):
        if tag == 25:                                  # PARA_SHAPE
            a = struct.unpack_from('<I', di, off)[0]
            shapes.append({
                'align': (a >> 2) & 7,
                'margin_left': struct.unpack_from('<i', di, off + 4)[0],
                'margin_right': struct.unpack_from('<i', di, off + 8)[0],
                'indent': struct.unpack_from('<i', di, off + 12)[0],
            })

    rows, cur = [], None
    for si, bt in enumerate(bts):
        for tag, lvl, off, sz in walk(bt):
            if tag == 66:                              # PARA_HEADER
                cur = {'sec': si, 'lvl': lvl,
                       'ps': struct.unpack_from('<H', bt, off + 8)[0], 'w': []}
            elif tag == 67 and cur is not None:        # PARA_TEXT
                cur['w'] += [bt[off + k] | (bt[off + k + 1] << 8) for k in range(0, sz, 2)]
                if cur['lvl'] >= 1:                    # 셀 문단만 (0 = 본문)
                    cur['text'] = para_text(cur['w'])
                    rows.append(cur)
                cur = None
    return shapes, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('hwp')
    ap.add_argument('--all', action='store_true', help='셀 문단을 전부 나열')
    ap.add_argument('--bullet', action='store_true', help='불렛(•) 문단만')
    a = ap.parse_args()

    shapes, rows = collect(a.hwp)
    if a.bullet:
        rows = [r for r in rows if '•' in r.get('text', '')]
    if not rows:
        print('해당하는 셀 문단이 없다.')
        return 1

    def fmt(i):
        if i >= len(shapes):
            return f'PS{i} (정의 없음 — DocInfo 손상 의심)'
        d = shapes[i]
        return (f'PS{i:<3} 정렬={ALIGN.get(d["align"], d["align"]):4s} '
                f'왼여백={d["margin_left"]:<6} 내어쓰기={d["indent"]:<7} '
                f'오른여백={d["margin_right"]}')

    print(f'{os.path.basename(a.hwp)} — 셀 문단 {len(rows)}개\n')
    if a.all or a.bullet:
        for r in rows:
            print(f'  [sec{r["sec"]}] {fmt(r["ps"])}  {r["text"][:44]!r}')
        print()

    print('지배 ParaShape (많이 쓰는 순):')
    for ps, n in Counter(r['ps'] for r in rows).most_common(10):
        print(f'  {n:>5}문단  {fmt(ps)}')
    print('\n→ clone_para_shape 의 source_id 로 위 번호를 쓴다. '
          '응답의 new_id 는 절대 하드코딩하지 말고 읽어라.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
