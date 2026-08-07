# 런북 02 — 노션 429 / 네트워크 장애

> **2026-08-06 실발생**: meeting-mail 이 `ConnectionError(ProtocolError)` 로 실패 →
> 다음 발화에서 attempt-suffix(-r2)가 자동 복구. 설계대로 동작한 사례.

## 증상
- `pipeline_error` 에 `ConnectionError` / `429` / `Timeout`
- 잡 상태 failed, 워터마크 `last_success` 미전진(=유실 없음)

## 즉시 확인
```bash
grep -o '"pipeline_error".*' ~/kv-data/net-sourcing/jobs/<잡>/events.jsonl | head -1
python -m kv_core.orrery --no-net | grep -A5 "실패 잡"     # 미해결/해결됨 구분
curl -s -o /dev/null -w "%{http_code}" https://api.notion.com/v1/users/me -H "Authorization: Bearer $NOTION_TOKEN" -H "Notion-Version: 2022-06-28"
```

## 원인 후보
1. **일시 망 단절**(맥 sleep 진입·wifi 전환) — 재시도 105초를 넘기면 실패로 확정
2. 노션 rate limit(429) — `core/notion.py` 가 Retry-After 존중 4회 재시도
3. 토큰 만료·권한 회수 → 401/403 (재시도 무의미)

## 복구
- **보통 아무것도 안 해도 된다** — attempt-suffix 가 같은 날 재시도하고, 안 되면 다음 날 새 잡.
- 급하면: `launchctl kickstart gui/$(id -u)/com.kv.scheduler`
- 401/403 이면 노션 통합 공유 상태 확인 후 `$KV_CONFIG/.env` 의 `NOTION_TOKEN` 갱신

## 재발 방지
- 재시도 소진(105초)을 넘기는 장애는 "네트워크가 오래 끊긴 것"이라 코드로 못 막는다 — 재시도가 답.
- 429 가 잦으면 페이지 크기·호출 빈도 조정(현재 3 req/s 제한 준수 중)

## MTTR (예상)
**0분(자동 복구)** — 사람 개입 불필요. 개입 시 2분.
