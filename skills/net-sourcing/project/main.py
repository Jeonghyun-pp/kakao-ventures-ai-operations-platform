#!/usr/bin/env python3
"""net-sourcing CLI 디스패처.

서브커맨드 = 데이터 흐름 단계:
    collect  → 채널 어댑터로 raw 수집 (AI 없음)           → data/raw/
    land     → 수집 raw를 그물망 소싱 DB에 Channel 태그로 전량 착지(채널별 정리)
    run      → collect → land 일괄 ('스킬 한 번 실행' = 3채널 수집+착지)
    screen   → [판단 에이전트/추후] 중복제거 + AI 판단       → data/candidates_*.json
    sync     → [판단 에이전트/추후] 판단 통과분을 극초기 DB에 적재
    report   → 수요일 미팅용 마크다운 다이제스트
    schedule → 매주 화 08:00 launchd 등록/해제/상태

메일 발송·확정은 하지 않는다(소싱까지만).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent
CONFIG_PATH = PROJ / "config.yaml"
KEYWORDS_PATH = PROJ / "keywords.yaml"


def load_env() -> None:
    """자격증명 정본은 `$KV_CONFIG/.env` 하나뿐이다.

    🔴 [2026-07-27] 예전엔 스킬마다 `project/.env` 를 따로 뒀다. 같은 NOTION_TOKEN 이 3벌,
       APIFY_TOKEN 이 2벌 있었고 — 값이 우연히 같았을 뿐 **한쪽만 갱신되면 조용히 갈라진다.**
       스킬별 `.env` 를 다시 만들지 마라.
    """
    from kv_core import secrets
    secrets.load()


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_keywords() -> dict:
    with open(KEYWORDS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cmd_collect(cfg: dict, channel: str, force_budget: bool = False) -> bool:
    """채널 어댑터를 돌려 raw 후보를 data/raw/ 에 저장. 반환: 정상이면 True.

    ★ [2026-07-20] 반환값이 생긴 이유 = **크론이 조용히 실패하지 않게.**
      수집이 '돌긴 했는데 전멸'인 경우를 종료코드로 올려보낸다. traceback으로 터뜨리지 않는 건
      로그에서 원인이 묻히기 때문이다 — 사람이 볼 건 스택이 아니라 '무엇이 몇 % 실패했나'다.
    """
    from core import pipeline
    # 🔴 월 예산 가드 우회는 **사람이 CLI로 지정할 때만** 켜진다. cfg에 실어 보내는 이유는
    #   pipeline.collect 시그니처를 안 건드리려는 것뿐이고, config.yaml에는 이 키가 없다
    #   (있으면 크론이 영구 우회하게 된다 — 그건 가드를 없애는 것과 같다).
    cfg = {**cfg, "_force_budget": bool(force_budget)}
    try:
        pipeline.collect(cfg, load_keywords(), channel)
    except pipeline.CollectDegraded as e:
        print(f"\n🔴 {e}")
        print("   → 수집된 건은 저장됐다. 위 실패 원인을 확인하라 "
              "(네트워크·쿼터·액터 스키마 변경 순으로 의심).")
        return False
    return True


def cmd_land(cfg: dict, channel: str, dry_run: bool = False) -> None:
    """수집 raw를 그물망 소싱 DB에 Channel 태그로 전량 착지(채널별 정리)."""
    from core import pipeline
    pipeline.land(cfg, channel, dry_run=dry_run)


def cmd_screen(cfg: dict) -> None:
    """data/raw/ 최신본 → 중복제거 + AI 판단 → data/candidates_*.json"""
    from core import pipeline
    pipeline.screen(cfg)


def cmd_sync(cfg: dict) -> None:
    """판단 통과분을 극초기 DB에 신규 로우(미검토)로 적재."""
    from core import pipeline
    pipeline.sync(cfg)


def cmd_report(cfg: dict) -> None:
    """수요일 미팅용 마크다운 다이제스트를 stdout으로."""
    from core import report
    print(report.build(cfg))


def cmd_run(cfg: dict, channel: str, force_budget: bool = False) -> bool:
    """'스킬 한 번 실행' = 3채널 수집 → 그물망 소싱 DB 채널별 착지 (스코프#1).

    판단(screen)·극초기 DB 승격(sync)은 별도 '판단 에이전트' 단계 — 추후 run에 합류.
    """
    # 수집이 전멸했어도 **착지는 한다** — 이미 받아온 건을 버릴 이유가 없다.
    # 다만 종료코드는 비-0으로 올려보낸다(크론이 알아야 한다).
    ok = cmd_collect(cfg, channel, force_budget=force_budget)
    cmd_land(cfg, channel)
    # 🔴 [2026-08-13 W12 · 리뷰 A-F10] 여기 `return` **뒤에** 세 줄이 있었다:
    #        if cfg.get("schedule", {}).get("notify"): notify.summary(cfg)
    #    도달 불가 코드다. `config.yaml` 의 `schedule.notify` 를 켠 사람은 켜졌다고 믿는데
    #    `run` 경로의 요약 알림만 영영 안 나갔다 — ADR 0002 의 "발동하지 않는 가드는
    #    없는 가드보다 나쁘다"의 알림판이다.
    #    되살리지 않고 **지운다.** 알림 정책은 정본 R3 가 이미 세 곳으로 모았고(alerts),
    #    여기서 한 벌 더 내보내면 그 통합이 도로 깨진다.
    return ok


def cmd_schedule(cfg: dict, action: str) -> None:
    from core import scheduler
    scheduler.dispatch(cfg, action)


CHANNELS = ["appstore", "threads",
            "linkedin", "linkedin_harvestapi", "linkedin_listly",  # linkedin=2어댑터 별칭
            "naver", "disquiet", "velog",
            "x", "hackernews", "producthunt", "all"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="net-sourcing")
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("collect")
    sc.add_argument("--channel", default="all", choices=CHANNELS)
    sc.add_argument("--force-budget", action="store_true",
                    help="월 예산(monthly_budget_usd) 초과여도 진행 — **사람 전용**. 크론은 쓰지 마라")
    sl = sub.add_parser("land")
    sl.add_argument("--channel", default="all", choices=CHANNELS)
    sl.add_argument("--dry-run", action="store_true",
                    help="쓰지 않고 '신규 N행 / 갱신 M행 / 블록 P편'만 출력")
    sub.add_parser("screen")
    sub.add_parser("sync")
    sub.add_parser("report")
    sr = sub.add_parser("run")
    sr.add_argument("--channel", default="all", choices=CHANNELS)
    sr.add_argument("--force-budget", action="store_true",
                    help="월 예산 초과여도 진행 — **사람 전용**. 크론은 쓰지 마라")
    ss = sub.add_parser("schedule")
    ss.add_argument("action", choices=["install", "uninstall", "status"])

    # [반자동 judge] 크론이 LLM을 부르지 않는다 — 주 1회 Claude Code 세션이 판단한다.
    je = sub.add_parser("judge-export", help="판정이 빈 행 → data/judge_queue_*.json")
    # 🔴 기본 0 = 무제한. 예전 기본값 400이 07-16 실행을 정확히 400에서 끊어 **링크드인을
    #    큐에 아예 못 넣었다**(앱스토어 92 + 스레드 308에서 소진). 스모크용으로만 쓴다.
    je.add_argument("--limit", type=int, default=0, help="0=무제한 (스모크용으로만 지정)")
    je.add_argument("--channel", default="", help="Threads | LinkedIn | App Store (빈값=전부)")
    je.add_argument("--include-base", action="store_true",
                    help="레거시 base DB도 포함 (⚠️ 스레드·링크드인 원본이 채널 DB와 중복)")
    ja = sub.add_parser("judge-apply", help="판단 결과(verdicts json) → 노션 Tier/AI Reason")
    ja.add_argument("--file", required=True)

    # [팔로업 ②] 알려진 인물 bio 재구매·diff — **수동 전용**($0.005/인, 상한 recheck.max_people).
    rk = sub.add_parser("recheck", help="알려진 스레드 인물 bio 재확인 — 변화만 재검토 ✅ ($0.005/인)")
    rk.add_argument("--dry-run", action="store_true", help="대상·비용만 ($0)")
    rk.add_argument("--limit", type=int, default=0, help="앞 N명만 (스모크용)")
    # [2026-07-20] 링크드인 액티브 레인 — 스레드 recheck의 링크드인판. **수동 전용**.
    rl = sub.add_parser("recheck-linkedin",
                        help="링크드인 프로필 재확인 — 변화/이탈임박만 재검토 ✅ ($0.004/인)")
    rl.add_argument("--dry-run", action="store_true", help="대상·비용만 ($0)")
    rl.add_argument("--limit", type=int, default=0, help="앞 N명만 (스모크용)")

    args = p.parse_args(argv)
    load_env()
    cfg = load_config()

    try:
        return _dispatch(cfg, args)
    except RuntimeError as e:          # 전제 미충족(예산 초과·토큰 없음 등) — 트레이스백 대신 안내
        print(f"\n🔴 {e}")
        return 1


def _dispatch(cfg: dict, args) -> int:
    if args.cmd == "collect":
        if not cmd_collect(cfg, args.channel,
                           force_budget=getattr(args, "force_budget", False)):
            return 1                              # 🔴 크론은 종료코드로만 건강을 안다
    elif args.cmd == "land":
        cmd_land(cfg, args.channel, dry_run=args.dry_run)
    elif args.cmd == "judge-export":
        from core import judge_cli
        return judge_cli.export(cfg, limit=args.limit, channel=args.channel,
                                include_base=args.include_base)
    elif args.cmd == "judge-apply":
        from core import judge_cli
        return judge_cli.apply(cfg, args.file)
    elif args.cmd == "recheck":
        from tools import recheck
        return recheck.run(dry_run=args.dry_run, limit=args.limit)
    elif args.cmd == "recheck-linkedin":
        from tools import recheck_linkedin
        return recheck_linkedin.run(dry_run=args.dry_run, limit=args.limit)
    elif args.cmd == "screen":
        cmd_screen(cfg)
    elif args.cmd == "sync":
        cmd_sync(cfg)
    elif args.cmd == "report":
        cmd_report(cfg)
    elif args.cmd == "run":
        if not cmd_run(cfg, args.channel,
                       force_budget=getattr(args, "force_budget", False)):
            return 1
    elif args.cmd == "schedule":
        cmd_schedule(cfg, args.action)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(PROJ))
    raise SystemExit(main())
