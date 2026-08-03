"""kv-core — KV 인턴업무 자동화 공통 런타임.

기본구조 1단계(공통 런타임). 각 스킬은 **도메인 로직만** 갖고, 아래 횡단 관심사는 여기서 상속한다.

    paths     실데이터 루트($KV_DATA) 기준 경로. 코드에 경로 리터럴을 두지 않기 위한 단일 진입점
    secrets   자격증명 단일 저장소($KV_CONFIG). .env·토큰 파일의 유일한 출처
    notion    노션 API를 **안전하게 부르는 법** — 인증·재시도·UTF-16 절단·스키마 조회
    notify    Slack 단일 경로
    gate      공유 자원 어드미션 컨트롤 (W2)
    job       잡 러너·상태 프로토콜·다단계 파이프라인·자원 락 (W3·W4)
    cadence   주기 어휘 정본 — interval_elapsed (W4, net-sourcing에서 승격)
    registry  선언적 task 미러 — config/tasks.yaml, 노션 대조 reconcile (W4)
    scheduler 상태기반 due 판정·catch-up·워터마크의 단일 진입점 (W4)
    approval  승인 브릿지 — 판단·발송 초안을 심사역에게 라우팅 (W4 인터페이스 / W8 본체)
    gmail     Gmail 초안 생성 — **발송 경로 없음** (W4)
    health    심박·헬스 판정            (골격 — 본체는 W6)

W4에서 컨트롤 플레인이 완성됐다: registry(무슨 일)·scheduler(언제)·job(어떻게 안전하게)·
approval(사람 승인). 스킬은 여전히 도메인 로직만 갖는다.

## 경계 — 무엇이 여기 있고 무엇이 스킬에 남나

여기 있는 것은 "외부 시스템을 안전하게 부르는 법"까지다.
"우리 DB를 다루는 법"(DB 라우팅·착지 키·도메인 프로퍼티 매핑)은 각 스킬에 남는다.
DB마다 스키마가 다르므로 그 앞 단계까지만 공통화한다.

## 세 개의 루트

    ~/kv-automation/    코드    git 관리
    ~/kv-data/          데이터   rsync 단위. git 명령이 닿지 않는다
    ~/kv-config/        시크릿   별도 취급. 데이터와 백업 정책이 다르다(회전 가능 vs 불가)

셋을 분리하는 이유는 `git clean -xdf`가 `.gitignore`된 파일도 지우기 때문이다(`-x`가 그 뜻).
**`.gitignore`는 선언이고 위치는 구조다.**
"""

__version__ = "0.1.0"
