# kv-automation

카카오벤처스 인턴 업무 자동화 — 스킬과 공통 기반.

## 구조

```
skills/       업무별 스킬 6종 (~/.claude/skills/ 에서 심볼릭 링크)
kv-core/      공통 기반 — secrets · notion · notify · gate · job · health  (W1)
.github/      상시 실행이 필요한 잡 (앱스토어 일일 스냅샷)                 (W2)
docs/         ADR · 런북 · 인시던트
```

## 원칙

- **자격증명과 실데이터는 커밋하지 않는다.** `.env` · `token.json` · `data/` 는 전부 제외.
  `data/` 에는 외부 창업자 개인정보가 실재한다.
- **경로는 `$HOME` 또는 환경변수 기준.** 절대경로 하드코딩 금지 — 머신 이전이 불가능해진다.
- **시크릿 저장소는 단일 `.env` (권한 600).** macOS Keychain은 머신 종속이라 쓰지 않는다.

## 설치

```bash
git clone <repo> ~/kv-automation
cd ~/kv-automation
# 각 스킬을 Claude Code에 연결
for s in skills/*/; do ln -sfn "$PWD/$s" ~/.claude/skills/$(basename "$s"); done
# 자격증명 채우기
cp skills/net-sourcing/project/.env.example skills/net-sourcing/project/.env
```
