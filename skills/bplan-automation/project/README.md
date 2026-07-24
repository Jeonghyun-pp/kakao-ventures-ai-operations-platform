# Bplan 자동화 봇

카카오벤처스 사업계획서(Bplan) 접수 처리를 자동화하는 터미널 도구입니다.

- **동기화**: 구글시트의 신규 접수건을 Notion DB에 등록하고, 해당 Gmail을 담당자 라벨로 이동
- **드랍메일**: Notion에서 `To Drop` 처리된 기업에 드랍 메일 초안(답장)을 Gmail에 자동 생성

현재 담당자는 **Sofia** 기준으로 설정되어 있습니다.

---

## 실행 방법 (터미널)

이 폴더에서 아래 명령으로 실행합니다. 실행하면 Gmail 라벨(동기화는 Notion 태그도)을 물어보며,
그냥 Enter를 누르면 기본값(`2. Sofia` / `소피아 Sofia`)으로 진행합니다.

```
# Mac
.venv/bin/python main.py drop    # 드랍메일 초안 생성
.venv/bin/python main.py sync    # 구글시트 신규 접수건 동기화

# Windows
.venv\Scripts\python main.py drop
.venv\Scripts\python main.py sync
```

### 더블클릭으로 드랍메일만 실행 (선택)

| OS | 실행 파일 |
|----|-----------|
| Mac | `run.command` 더블클릭 → `drop` 실행 |
| Windows | `run.bat` 더블클릭 → `drop` 실행 |

- 최초 1회는 가상환경 설치 때문에 1~2분 걸립니다.
- 실행되면 터미널 창이 뜨고, Gmail 라벨을 입력(또는 Enter)하면 초안 생성이 진행됩니다.
- 작업이 끝나면 결과가 출력되고 종료됩니다. (동기화는 더블클릭이 아닌 터미널 `sync` 명령으로 실행)

> Mac에서 "확인되지 않은 개발자" 경고로 `run.command`가 안 열리면,
> 파일 우클릭 → **열기** → **열기** 를 한 번 눌러주세요. (최초 1회)

---

## 다음 인턴이 인계받을 때 (담당자 이름 바꾸기)

`main.py` 상단의 **이 두 줄만** 본인 것으로 바꾸면 됩니다.

```python
DEFAULT_GMAIL_LABEL = "2. Sofia"      # 본인 Gmail 라벨 이름
DEFAULT_NOTION_TAG  = "소피아 Sofia"   # 본인 Notion '담당자' 태그
```

- 바꾸면 동기화/드랍메일 팝업에 이 값이 **기본으로 미리 채워집니다.**
- 굳이 코드를 안 바꿔도, 팝업창에서 그때그때 직접 고쳐 입력해도 됩니다.
- Gmail 라벨 이름과 Notion 담당자 태그는 **정확히 일치**해야 합니다 (각각 Gmail/Notion에 실제 존재하는 이름).

---

## 사전 준비물 (이미 세팅되어 있다면 건너뛰기)

같은 폴더에 아래 파일들이 있어야 합니다.

- `.env` — Google/Notion 토큰 및 설정값 (Slack 토큰은 더 이상 필요 없습니다)
- `credentials.json` — 구글 시트 서비스 계정 키
- `gmail_credentials.json` — Gmail OAuth 클라이언트
- `token.json` — Gmail 로그인 토큰 (최초 실행 시 브라우저 인증으로 자동 생성)

> 이 파일들은 **비밀 자격증명**입니다. 외부에 공유하거나 깃에 올리지 마세요.

---

## 테스트

```
# Mac
.venv/bin/python -m unittest test_draft_mail.py
# Windows
.venv\Scripts\python -m unittest test_draft_mail.py
```
