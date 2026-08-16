#!/bin/bash
# ~/kv-data 스냅샷 백업 — **유일본을 유일본으로 두지 않는다.**
#
# ## 왜 이 파일이 생겼나 (2026-08-16)
#
# `~/kv-data`(981MB · 8,806파일)는 이 시스템의 **모든 상태**다 — 이벤트 장부, 커서,
# 워터마크, 승인 큐, 수집 원본 전량. 코드는 git 에 있고 시크릿은 회전 가능하지만
# 데이터는 **둘 다 아니다.** 날아가면 복구 수단이 없다.
#
# 그런데 문서 4곳(README · kv_core/__init__ · ADR 0001 · W1 교훈)이 *"rsync 단위"* 라고
# 선언하고, `W1-공통-런타임.md` 표는 **"데이터 | 영구 손실 | 없음 | 정기 rsync"** 라고
# 적어 뒀는데 — crontab 은 비어 있고 launchd 에도 없고 디스크 어디에도 사본이 없었다.
# **선언만 있고 발동하지 않는 가드.** 이 리포가 반복해 당한 그 형태이고, 이번엔 대상이
# 데이터 전량이었다.
#
# ## 방식 — `--link-dest` 하드링크 스냅샷
#
# 어제 스냅샷과 **안 바뀐 파일은 하드링크**로 잇는다. 그래서 7일치를 두어도 디스크는
# `전체 1벌 + 변경분`만 먹는다(증가 속도 실측 ~30MB/일 → 7일치 ≈ 1.2GB).
# 각 스냅샷은 **완전한 디렉토리**다 — 복구가 "그날 폴더를 통째로 되돌리기"로 끝난다.
# 증분 체인을 되감을 필요가 없다(체인 복구는 한 고리만 깨져도 전부 못 쓴다).
#
# ## 무엇을 막고 무엇을 못 막나
#
#   막는다     실수 삭제 · 잘못된 압축/마이그레이션 · 파일 파손 (7일 안이면 되돌린다)
#   못 막는다  🔴 **디스크·맥 자체의 고장** — 같은 디스크에 있기 때문이다.
#              오프사이트 1벌이 반드시 있어야 하고, 그건 이 스크립트 밖의 일이다
#              (2026-08-16 현재 이 맥에는 클라우드 드라이브가 하나도 마운트돼 있지 않다).
#
# 설치: cp config/launchd/com.kv.backup.plist ~/Library/LaunchAgents/
#      launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kv.backup.plist
set -euo pipefail

SRC="${KV_DATA:-$HOME/kv-data}"
DST="${KV_BACKUP_DIR:-$HOME/kv-data-backup}"
KEEP="${KV_BACKUP_KEEP:-7}"
TODAY="$(date +%Y-%m-%d)"

# 🔴 원본이 없으면 **아무것도 하지 않는다.** rsync 는 빈 원본을 성실하게 반영해서
#    "정상적으로 백업된 빈 디렉토리"를 만든다 — 그게 오늘 스냅샷이 되고, 7일 뒤엔
#    멀쩡한 스냅샷이 전부 밀려나간다. 백업이 데이터를 지우는 경로다.
if [ ! -d "$SRC" ]; then
  echo "🔴 원본 없음: $SRC — 중단한다(빈 백업으로 멀쩡한 스냅샷을 밀어내지 않는다)" >&2
  exit 1
fi
SRC_FILES=$(find "$SRC" -type f | wc -l | tr -d ' ')
if [ "$SRC_FILES" -lt 100 ]; then
  echo "🔴 원본 파일이 $SRC_FILES 개뿐이다(기대: 수천) — 중단한다." >&2
  echo "   마운트 실패·경로 오타일 가능성이 높다. 확인 후 KV_BACKUP_FORCE=1 로 강행하라." >&2
  [ "${KV_BACKUP_FORCE:-}" = "1" ] || exit 1
fi

mkdir -p "$DST"
# 가장 최근 스냅샷을 하드링크 기준으로 삼는다(오늘 것 제외 — 재실행 시 자기 자신 참조 방지)
LINK_DEST=""
PREV="$(ls -1 "$DST" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | grep -v "^$TODAY$" | tail -1 || true)"
[ -n "$PREV" ] && LINK_DEST="--link-dest=$DST/$PREV"

echo "· 원본 $SRC ($SRC_FILES 파일)"
echo "· 대상 $DST/$TODAY${PREV:+  (하드링크 기준: $PREV)}"

# --delete: 원본에서 지운 파일이 오늘 스냅샷에도 없어야 한다(스냅샷은 그날의 정확한 상)
# 어제 스냅샷은 그대로 남으므로 실수 삭제는 여전히 되돌릴 수 있다.
rsync -a --delete $LINK_DEST "$SRC/" "$DST/$TODAY/"

DST_FILES=$(find "$DST/$TODAY" -type f | wc -l | tr -d ' ')
if [ "$DST_FILES" -lt "$SRC_FILES" ]; then
  echo "🔴 백업 파일 수가 원본보다 적다: $DST_FILES < $SRC_FILES" >&2
  exit 1
fi

# 보관 기간 초과분 삭제 — 하드링크라 오래된 스냅샷을 지워도 최신본은 멀쩡하다.
# ⚠️ `head -n -N`(뒤에서 N개 빼기)은 **GNU 전용**이다. macOS BSD head 는
#    `illegal line count -- -7` 로 죽는다 — 1회차에 실제로 여기서 터졌다.
SNAPS=$(ls -1 "$DST" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort || true)
NSNAP=$(printf '%s\n' "$SNAPS" | grep -c . || true)
if [ "$NSNAP" -gt "$KEEP" ]; then
  printf '%s\n' "$SNAPS" | head -n "$((NSNAP - KEEP))" | while read -r old; do
    [ -n "$old" ] || continue
    echo "· 보관기간 초과 삭제: $old"
    rm -rf "${DST:?}/${old:?}"
  done
  SNAPS=$(ls -1 "$DST" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort)
  NSNAP=$(printf '%s\n' "$SNAPS" | grep -c .)
fi

# 성공 표식 — 사람도 기계도 "언제 마지막으로 됐나"를 한 줄로 안다.
# 🔴 표식은 **백업 대상 밖**($DST)에 둔다. $SRC 안에 두면 자기 자신을 백업하는 꼴이라
#    "백업이 죽은 날"에도 어제 스냅샷 안의 옛 표식이 남아 판정이 흐려진다.
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ) files=$DST_FILES snapshots=$NSNAP" > "$DST/.last-success"

echo "✅ $TODAY · $DST_FILES 파일 · 스냅샷 ${NSNAP}벌 · 총 $(du -sh "$DST" | cut -f1)"
