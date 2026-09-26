#!/usr/bin/env python3
"""HWP 5.0 구조 정합성 검사 — 한컴이 거부하는 종류의 불일치를 찾는다.
서식이 아니라 '레코드가 서로 모순되는가'만 본다."""
import sys, struct, zlib, olefile
from collections import Counter

T_IDMAP, T_BORDERFILL, T_CHARSHAPE, T_PARASHAPE = 0x11, 0x14, 0x15, 0x19
T_PARA_HEADER, T_PARA_TEXT, T_PARA_CHAR_SHAPE, T_PARA_LINE_SEG = 66, 67, 68, 69
T_CTRL_HEADER, T_LIST_HEADER, T_TABLE = 71, 72, 77
CTRL8 = set(list(range(1, 9)) + [9, 11, 12] + list(range(14, 24)))  # 8 WCHAR 차지


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


def check_cfb(path, bad):
    """CFB 컨테이너 정합성 — 레코드가 완벽해도 여기서 깨지면 한컴이 거부한다."""
    b = open(path, 'rb').read()
    ssz = 1 << struct.unpack_from('<H', b, 0x1E)[0]
    nsec = (len(b) - 512) // ssz
    nfat = struct.unpack_from('<I', b, 0x2C)[0]
    hdr = [struct.unpack_from('<i', b, 0x4C + 4 * i)[0] for i in range(109)]
    difat = hdr[:nfat]
    for s in difat:
        if not (0 <= s < nsec):
            bad('difat', f'DIFAT 항목 {s} 가 파일 범위(0~{nsec}) 밖')
            return
    fat = []
    for s in difat:
        fat += list(struct.unpack_from(f'<{ssz // 4}i', b, (s + 1) * ssz))
    # FAT 섹터는 FAT 안에서 자기 자신을 -3(FATSECT)로 표시해야 한다.
    # expandFatCapacity 가 표시를 엉뚱한 곳에 쓰면 여기서 잡힌다(실제로 파일이 깨졌다).
    for s in difat:
        if s < len(fat) and fat[s] != -3:
            bad('fatsect', f'FAT 섹터 {s} 가 FAT 안에서 -3(FATSECT) 이 아님 (값 {fat[s]})')
    for i, v in enumerate(hdr[nfat:], start=nfat):
        if v != -1:
            bad('difat', f'여분 DIFAT 슬롯 {i} 가 비어 있지 않음 (값 {v})')
            break
    # 체인 중복 사용(교차 링크) 검사
    seen = {}
    def chain(start, name):
        c, g = start, 0
        while c >= 0 and g < len(fat) + 5:
            if c >= nsec:
                bad('chain', f'{name}: 섹터 {c} 가 파일 범위 밖'); return
            if c in seen:
                bad('crosslink', f'섹터 {c} 를 {seen[c]} 와 {name} 가 함께 사용')
                return
            seen[c] = name
            c = fat[c] if c < len(fat) else -2
            g += 1
    ds = struct.unpack_from('<i', b, 0x30)[0]
    chain(ds, 'directory')


def check(path):
    o = olefile.OleFileIO(path)
    di = zlib.decompress(o.openstream('DocInfo').read(), -15)
    secs = ['/'.join(e) for e in o.listdir() if e[0] == 'BodyText']
    bodies = [(s, zlib.decompress(o.openstream(s).read(), -15)) for s in sorted(secs)]
    prob = Counter()
    detail = []
    wprob = Counter()
    wdetail = []

    def bad(k, msg):
        prob[k] += 1
        if len(detail) < 40:
            detail.append(f'  [{k}] {msg}')

    # ⚠️ Warning — 한컴 저장본에도 나타나는 값이라 파일을 거부하지는 않는다.
    # 실측: 한컴이 만든 제출본에서 세로 병합 127개 중 52개가 높이 불일치였고 정상으로 열린다.
    def warn(k, msg):
        wprob[k] += 1
        if len(wdetail) < 20:
            wdetail.append(f'  [{k}] {msg}')

    check_cfb(path, bad)

    # ── 압축 스트림 꼬리 (CRC32 + 원본 길이) ──
    # 한컴은 레코드 스트림(DocInfo·BodyText·Scripts)을 raw deflate 로 압축한 뒤
    # gzip 식 8바이트 꼬리를 붙이고 읽을 때 검증한다. 없으면 레코드가 완벽해도
    # "문서를 열 수 없습니다"가 뜬다. BinData(그림)에는 꼬리가 없는 게 정상.
    for e in sorted(o.listdir()):
        n = '/'.join(e)
        if not (n == 'DocInfo' or n.startswith('BodyText/') or n.startswith('Scripts/')):
            continue
        raw = o.openstream(n).read()
        d = zlib.decompressobj(-15)
        try:
            out = d.decompress(raw) + d.flush()
        except Exception as ex:
            bad('deflate', f'{n} 압축 해제 실패: {ex}')
            continue
        tail = d.unused_data
        if len(tail) != 8:
            bad('trailer', f'{n} 압축 꼬리 {len(tail)}B (CRC32+길이 8B 여야 함)')
            continue
        crc, size = struct.unpack('<II', tail)
        if crc != (zlib.crc32(out) & 0xFFFFFFFF):
            bad('trailer', f'{n} 꼬리 CRC32 {crc:#010x} ≠ 실제 {zlib.crc32(out) & 0xFFFFFFFF:#010x}')
        if size != len(out):
            bad('trailer', f'{n} 꼬리 길이 {size} ≠ 실제 {len(out)}')
    o.close()

    # ── DocInfo: ID_MAPPINGS 선언 수 vs 실제 레코드 수 ──
    counts = Counter()
    idmap = None
    for tag, lvl, off, sz in walk(di):
        counts[tag] += 1
        if tag == T_IDMAP and idmap is None:
            idmap = [struct.unpack_from('<i', di, off + i * 4)[0] for i in range(sz // 4)]
    n_cs, n_ps, n_bf = counts[T_CHARSHAPE], counts[T_PARASHAPE], counts[T_BORDERFILL]
    if idmap:
        if idmap[9] != n_cs:  bad('idmap', f'글자모양 선언 {idmap[9]} ≠ 실제 {n_cs}')
        if idmap[13] != n_ps: bad('idmap', f'문단모양 선언 {idmap[13]} ≠ 실제 {n_ps}')
        if idmap[8] != n_bf:  bad('idmap', f'테두리배경 선언 {idmap[8]} ≠ 실제 {n_bf}')

    for sname, bt in bodies:
        recs = list(walk(bt))
        # 문단 단위로 자식 레코드를 모은다
        paras = []
        cur = None
        for i, (tag, lvl, off, sz) in enumerate(recs):
            if tag == T_PARA_HEADER:
                cur = dict(i=i, lvl=lvl, off=off, sz=sz, text=[], cs=None, ls=None, ctrl=0, segstarts=[],
                           nch=struct.unpack_from('<I', bt, off)[0],
                           mask=struct.unpack_from('<I', bt, off + 4)[0],
                           ps=struct.unpack_from('<H', bt, off + 8)[0],
                           ncs=struct.unpack_from('<H', bt, off + 12)[0],
                           nls=struct.unpack_from('<H', bt, off + 16)[0])
                paras.append(cur)
            elif cur is not None and lvl == cur['lvl'] + 1:
                if tag == T_PARA_TEXT:
                    cur['text'] += [bt[off + k] | (bt[off + k + 1] << 8) for k in range(0, sz, 2)]
                elif tag == T_PARA_CHAR_SHAPE:
                    cur['cs'] = sz
                elif tag == T_PARA_LINE_SEG:
                    cur['ls'] = sz
                    cur['segstarts'] = [struct.unpack_from('<I', bt, off + k * 36)[0] for k in range(sz // 36)]
                elif tag == T_CTRL_HEADER:
                    cur['ctrl'] += 1

        for p in paras:
            n = p['nch'] & 0x7FFFFFFF
            actual = len(p['text']) + (1 if not p['text'] else 0)
            if p['text'] and n != len(p['text']):
                bad('nchars', f'{sname} 문단{p["i"]} 글자수 {n} ≠ 실제 {len(p["text"])}')
            # 컨트롤 문자(1-8,11,12,14-23 등)는 8 WCHAR 을 차지한다. 뒤따르는 7 WCHAR 은
            # 컨트롤의 페이로드라 0 같은 값이 들어 있어, 그대로 세면 비트 0 이 잘못 켜진다.
            want = 0
            k = 0
            w = p['text']
            while k < len(w):
                c = w[k]
                if c in CTRL8:
                    want |= (1 << c); k += 8; continue
                if c < 32 and c != 13:
                    want |= (1 << c)
                k += 1
            # 최상위 비트를 '플래그'로 보고 벗기지 마라 — 그건 컨트롤 문자 31(0x1F)의 비트다.
            # 한쪽만 벗기면 0x80000200 과 0x80000200 을 다르다고 보고한다(실측: 한컴 저장본 15건 오탐).
            if p['mask'] != want:
                bad('mask', f'{sname} 문단{p["i"]} 마스크 {p["mask"]:#x} ≠ 실제 {want:#x}')
            if p['cs'] is not None and p['ncs'] != p['cs'] // 8:
                bad('ncharshape', f'{sname} 문단{p["i"]} nCS {p["ncs"]} ≠ 레코드 {p["cs"]//8}')
            if p['ls'] is not None and p['nls'] != p['ls'] // 36:
                bad('nlineseg', f'{sname} 문단{p["i"]} nLS {p["nls"]} ≠ 레코드 {p["ls"]//36}')
            # 줄 정보는 "이 줄이 몇 번째 글자에서 시작하는가" 캐시다. 문단이 짧아졌는데
            # 이 값을 안 고치면 문단 밖을 가리키게 되고, 레코드가 전부 멀쩡해도 한컴이
            # 파일을 거부한다. replace_text 가 실제로 이 상태를 만들어 냈다.
            # 경계값 주의: 시작 위치가 글자수와 '같은' 것(한 칸 뒤 sentinel)은 한컴도 쓴다 —
            # 개체를 담은 문단이나 빈 문단에서 실제로 나온다. 진짜 결함은 '넘어선' 경우다
            # (실측 사례: 글자수 21 인데 55 번째 글자에서 시작 → 파일이 안 열렸다).
            if n > 0:
                for k, st in enumerate(p['segstarts']):
                    if st > n:
                        bad('linesegrange', f'{sname} 문단{p["i"]} 글자수 {n} 인데 {k}번째 줄이 {st}번째 글자에서 시작')
                        break
                if any(p['segstarts'][k] > p['segstarts'][k + 1] for k in range(len(p['segstarts']) - 1)):
                    bad('linesegorder', f'{sname} 문단{p["i"]} 줄 시작 위치가 오름차순이 아님 {p["segstarts"][:6]}')
            if p['ps'] >= n_ps:
                bad('psref', f'{sname} 문단{p["i"]} ParaShape {p["ps"]} ≥ {n_ps}')

        # ── 표: TABLE 레코드의 행/열 수 vs 실제 셀 ──
        i = 0
        while i < len(recs):
            tag, lvl, off, sz = recs[i]
            if tag == T_TABLE:
                nrow = struct.unpack_from('<H', bt, off + 4)[0]
                ncol = struct.unpack_from('<H', bt, off + 6)[0]
                # 행별 셀 수 배열 (offset 4+2+2+2+2+2+2+ ... 구현별 상이) → 셀 순회로 검증
                # 셀 LIST_HEADER 는 TABLE 레코드와 '같은' 레벨의 형제다(둘 다 CTRL_HEADER 의 자식).
                cells = []
                j = i + 1
                while j < len(recs):
                    t2, l2, o2, s2 = recs[j]
                    if l2 < lvl:
                        break
                    if t2 == T_LIST_HEADER and l2 == lvl:
                        cells.append(dict(col=struct.unpack_from('<H', bt, o2 + 8)[0],
                                          row=struct.unpack_from('<H', bt, o2 + 10)[0],
                                          cs=struct.unpack_from('<H', bt, o2 + 12)[0],
                                          rs=struct.unpack_from('<H', bt, o2 + 14)[0],
                                          w=struct.unpack_from('<I', bt, o2 + 16)[0],
                                          h=struct.unpack_from('<I', bt, o2 + 20)[0],
                                          nparas=struct.unpack_from('<H', bt, o2)[0],
                                          off=o2, lvl=l2))
                    j += 1
                covered = sum(c['cs'] * c['rs'] for c in cells)
                if covered != nrow * ncol:
                    bad('tablegrid', f'{sname} 표@{off} 격자 {nrow}×{ncol}={nrow*ncol} ≠ 셀 덮개 {covered} (셀 {len(cells)}개)')
                seen = set()
                for c in cells:
                    for r in range(c['row'], c['row'] + c['rs']):
                        for k in range(c['col'], c['col'] + c['cs']):
                            if (r, k) in seen:
                                bad('tableoverlap', f'{sname} 표@{off} 셀 겹침 ({r},{k})')
                            seen.add((r, k))
                if len(seen) != nrow * ncol:
                    bad('tablehole', f'{sname} 표@{off} 채워진 칸 {len(seen)} ≠ {nrow*ncol}')
                # 행별 셀 개수 배열 (TABLE +18, UINT16 × nRows)
                rowsz = [struct.unpack_from('<H', bt, off + 18 + 2 * k)[0] for k in range(nrow)]
                actual = [sum(1 for c in cells if c['row'] == k) for k in range(nrow)]
                if rowsz != actual:
                    bad('rowsize', f'{sname} 표@{off} 행별 셀수 선언 {rowsz} ≠ 실제 {actual}')
                # 병합 셀의 크기 vs 덮은 격자. merge 가 span 만 바꾸고 width/height 를
                # 안 고치면 여기서 잡힌다 — 한컴이 파일을 거부하는 원인이 된 실제 결함.
                rowh, colw = {}, {}
                for c in cells:
                    if c['rs'] == 1: rowh.setdefault(c['row'], set()).add(c['h'])
                    if c['cs'] == 1: colw.setdefault(c['col'], set()).add(c['w'])
                for c in cells:
                    if c['rs'] > 1:
                        want = sum(min(rowh.get(r, {0})) for r in range(c['row'], c['row'] + c['rs']))
                        if want and abs(c['h'] - want) > 2:
                            warn('mergeheight', f'{sname} 표@{off} 병합셀({c["row"]},{c["col"]}) 높이 {c["h"]} ≠ 덮은 행 합 {want}')
                    if c['cs'] > 1:
                        want = sum(min(colw.get(k, {0})) for k in range(c['col'], c['col'] + c['cs']))
                        if want and abs(c['w'] - want) > 2:
                            warn('mergewidth', f'{sname} 표@{off} 병합셀({c["row"]},{c["col"]}) 너비 {c["w"]} ≠ 덮은 열 합 {want}')
                i = j
                continue
            i += 1

        # ── 셀 LIST_HEADER 의 문단 수 vs 실제 ──
        for idx, (tag, lvl, off, sz) in enumerate(recs):
            if tag != T_LIST_HEADER:
                continue
            declared = struct.unpack_from('<H', bt, off)[0]
            cnt = 0
            lastflags = 0
            j = idx + 1
            while j < len(recs):
                t2, l2, o2, s2 = recs[j]
                if (t2 == T_LIST_HEADER and l2 == lvl) or l2 < lvl:
                    break
                if t2 == T_PARA_HEADER and l2 == lvl:
                    cnt += 1
                    if struct.unpack_from('<I', bt, o2)[0] >> 31:
                        lastflags += 1
                j += 1
            if declared != cnt:
                bad('cellparas', f'{sname} 셀@{off} 문단수 선언 {declared} ≠ 실제 {cnt}')
            if cnt and lastflags != 1:
                bad('celllast', f'{sname} 셀@{off} 마지막문단 플래그 {lastflags}개 (1이어야 함)')

        top = [p for p in paras if p['lvl'] == 0]
        nlast = sum(1 for p in top if p['nch'] >> 31)
        if nlast != 1:
            bad('seclast', f'{sname} 최상위 마지막문단 플래그 {nlast}개 (1이어야 함)')

    return prob, detail, wprob, wdetail


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        # --help 를 파일명으로 받아 트레이스백을 뱉던 것을 막는다
        print(__doc__ or 'usage: audit_structure.py <file.hwp> [more.hwp ...]')
        sys.exit(0 if len(sys.argv) > 1 else 2)
    for f in sys.argv[1:]:
        prob, detail, wprob, wdetail = check(f)
        name = f.split('/')[-1]
        if not prob and not wprob:
            print(f'✅ {name}: 이상 없음')
        elif not prob:
            print(f'✅ {name}: Error 없음 · Warning {dict(wprob)}')
            for d in wdetail[:4]:
                print(d)
        else:
            print(f'❌ {name}: {dict(prob)}' + (f' · Warning {dict(wprob)}' if wprob else ''))
            for d in detail[:12]:
                print(d)
