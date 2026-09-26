# kv-automation — 이제 투심보고서 엔진만 남았다 (2026-09-26)

현행 자동화는 전부 `~/kv` 다(메일·링크드인·비플랜·앱스토어·투심 요청 레인·살림). 이 리포에 남은 것:

- `skills/investment-memo/` — 투심보고서 생성 엔진(MCP 서버 + 섹션 프롬프트 + 표·IRR·문체 코드). `~/kv/skills/memo` 가 `claude -p --mcp-config` 로 무인 실행한다. 대화형(`/investment-memo`)으로도 그대로 쓴다.
- `requirements.txt` · `.venv` — 그 엔진의 파이썬(`mcp<2` · pdfplumber · python-docx …). `~/kv/constraints.yaml memo.skill_python` 이 여기를 가리킨다.

지운 것(태그 `legacy-final-2026-09-26` 에 전부 있다): kv-core(스케줄러·승인 레인·오러리 브리핑), net-sourcing(스레드·링크드인·앱스토어 옛 소싱 — 스레드는 하지 않기로 함, 나머지는 kv 로 이관), ai-consumer-pulse(2026-08-04 정지 후 폐기), appstore-rank, config/scripts/docs.
미커밋이던 net-sourcing 작업은 브랜치 `wip/net-sourcing-2026-09` 에 있다. 데이터는 `~/kv-data/_archive/legacy-2026-09-26/` 로 옮겼다(유료 Apify 원본 포함, 영구 보존).
