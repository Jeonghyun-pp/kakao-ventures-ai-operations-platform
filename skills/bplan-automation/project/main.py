import os
import re
import sys
import time
import json
import base64
import threading
import traceback
from pathlib import Path
import requests
import gspread
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape as _html_escape
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent


def resolve_path(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return BASE_DIR / path

_REQUIRED_VARS = ["SPREADSHEET_URL", "NOTION_TOKEN", "NOTION_DATABASE_ID"]
_missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
if _missing:
    raise EnvironmentError(f"필수 환경변수가 설정되지 않았습니다: {', '.join(_missing)}")

# ==========================================

GOOGLE_CREDENTIALS_FILE = resolve_path(os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL", "")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "비플랜DB2")
WORKSHEET_SETTING_NAME = os.getenv("WORKSHEET_SETTING_NAME", "Setting")

GMAIL_CREDENTIALS_FILE = resolve_path(os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json"))
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
TOKEN_FILE = BASE_DIR / "token.json"

print(f"[BOOT] BASE_DIR={BASE_DIR} cwd={os.getcwd()} "
      f"GMAIL_CREDENTIALS_FILE={GMAIL_CREDENTIALS_FILE} exists={GMAIL_CREDENTIALS_FILE.exists()} "
      f"TOKEN_FILE={TOKEN_FILE} exists={TOKEN_FILE.exists()}", flush=True)

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

# ====== 담당자 기본값 (다음 인턴은 이 두 줄만 본인 것으로 바꾸면 됩니다) ======
# DEFAULT_GMAIL_LABEL : Gmail 라벨 이름 (예: "2. Sofia")
# DEFAULT_NOTION_TAG  : Notion '담당자' 태그 (예: "소피아 Sofia")
# 팝업창에 이 값이 미리 채워지며, 필요하면 팝업에서 그때그때 직접 고쳐 쓸 수도 있습니다.
DEFAULT_GMAIL_LABEL = "3. Evan"
DEFAULT_NOTION_TAG = "에반 Evan"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# ==========================================
# Connect Gmail API
_gmail_lock = threading.Lock()


def get_gmail_service():
    with _gmail_lock:
        try:
            creds = None
            if TOKEN_FILE.exists():
                creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), GMAIL_SCOPES)
            print(f"[GMAIL] token_exists={TOKEN_FILE.exists()} creds={creds is not None} "
                  f"valid={getattr(creds,'valid',None)} expired={getattr(creds,'expired',None)} "
                  f"has_refresh={bool(getattr(creds,'refresh_token',None))}", flush=True)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    print("[GMAIL] -> refresh path", flush=True)
                    creds.refresh(Request())
                else:
                    print(f"[GMAIL] -> OAuth flow path (cred_file={GMAIL_CREDENTIALS_FILE} exists={GMAIL_CREDENTIALS_FILE.exists()})", flush=True)
                    if not GMAIL_CREDENTIALS_FILE.exists():
                        raise FileNotFoundError(
                            f"gmail_credentials.json 미존재 (절대경로): {GMAIL_CREDENTIALS_FILE} "
                            f"(BASE_DIR={BASE_DIR}, cwd={os.getcwd()})"
                        )
                    flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDENTIALS_FILE), GMAIL_SCOPES)
                    creds = flow.run_local_server(port=0)
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
            return build('gmail', 'v1', credentials=creds)
        except Exception:
            print("[GMAIL][EXCEPTION]\n" + traceback.format_exc(), flush=True)
            raise

# Get Label Name
def get_label_ids(gmail_service):
    #Matching Label ID and Label name
    results = gmail_service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])
    #Save as dictionary
    return {label['name']: label['id'] for label in labels}

def get_valid_notion_assignees():
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
    response = requests.get(url, headers=NOTION_HEADERS)
    
    if response.status_code == 200:
        db_info = response.json()
        # get all tags in '담당자' options in the bplan 3.0 db
        options = db_info.get("properties", {}).get("담당자", {}).get("select", {}).get("options", [])
        # making tags to list
        return [opt["name"] for opt in options]
    else:
        return []

# notion page creation and add to db
def create_notion_page(data, notion_assignee):
    url = "https://api.notion.com/v1/pages"

    def format_text(text):
        content = str(text).strip() if text else "None"
        return [{"text": {"content": content[:2000]}}] #when content is over 2000, it occurs error. So just truncate it.

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "기업명": {"title": format_text(data['company_name'])},
            "이메일": {"rich_text": format_text(data['email'])},
            "대표명": {"rich_text": format_text(data['ceo_name'])},
            "연락처": {"rich_text": format_text(data['ceo_contact'])},
            "서비스정의": {"rich_text": format_text(data['abstraction'])},
            "문제정의": {"rich_text": format_text(data['problem'])},
            "솔루션": {"rich_text": format_text(data['solution'])},
            "비즈니스모델": {"rich_text": format_text(data['bm'])},
            "경쟁사": {"rich_text": format_text(data['peer_group'])},
            "팀이력": {"rich_text": format_text(data['team'])},
            "경쟁력": {"rich_text": format_text(data['best_reason'])},
            "추가내용": {"rich_text": format_text(data['free'])},
            "To do" : {"select": {"name": "Newly Added"}}, # defualt 'To do' selection in notin is "Newly Added"
            "담당자": {"select": {"name": notion_assignee}} # when create_notion_page called, toggle the notion_assignee variable.
        }
    }
    
    # if link exists, put url in the payload content
    link_url = str(data['link']).strip() if data['link'] else ""
    if link_url:
        payload["properties"]["사업계획서"] = {"url": link_url}
    
    #create page to the notion db of the current notion database id.
    response = requests.post(url, headers=NOTION_HEADERS, data=json.dumps(payload))
    return response.status_code == 200, response.text

# ==========================================
def run_sync(gmail_label, notion_tag):
    """동기화 워크플로우: 구글시트 신규 접수건 → Notion 등록 + Gmail 라벨 이동."""

    def log(text):
        print(text, flush=True)

    log("🔄 동기화 작업을 시작합니다...")

    # [1. 유효성 검사 (Fail-Fast)]
    try:
        gmail_service = get_gmail_service()
        label_map = get_label_ids(gmail_service)
        valid_mailboxes = list(label_map.keys())
    except Exception as e:
        log(f"Gmail API 연결 실패. 관리자에게 문의하세요 : {e}")
        log("프로그램을 종료합니다.")
        return

    try:
        valid_names = get_valid_notion_assignees()
    except Exception as e:
        log(f"노션 DB 연동 실패. 관리자에게 문의하세요: {e}")
        log("프로그램을 종료합니다.")
        return
    if not valid_names:
        log("노션 DB 연동 실패. 관리자에게 문의하세요.")
        log("프로그램을 종료합니다.")
        return

    if gmail_label not in valid_mailboxes:
        log(f"지메일에 '{gmail_label}' 라벨이 없습니다. 다시 실행해주세요.")
        log("정확한 데이터를 입력해주세요.")
        log("프로그램을 종료합니다.")
        return
    if notion_tag not in valid_names:
        log(f"노션에 '{notion_tag}' 태그가 없습니다. 다시 실행해주세요.")
        log("정확한 데이터를 입력해주세요.")
        log("프로그램을 종료합니다.")
        return

    try:
        gc = gspread.service_account(filename=str(GOOGLE_CREDENTIALS_FILE))
        sh = gc.open_by_url(SPREADSHEET_URL)
        worksheet_data = sh.worksheet(WORKSHEET_NAME) 
        worksheet_settings = sh.worksheet(WORKSHEET_SETTING_NAME) 
    except Exception as e:
        log(f"구글 시트 연결 실패. 관리자에게 문의하세요 : {e}")
        log("프로그램을 종료합니다.")
        return

    try:
        saved_count_str = worksheet_settings.acell('A1').value
    except Exception as e:
        log(f"[오류] 'Setting' 탭의 A1 셀을 읽어오지 못했습니다. 관리자에게 문의하세요: {e}")
        log("프로그램을 종료합니다.")
        return
    if not saved_count_str:
        log("[오류] 'Setting' 탭의 A1 셀이 비어 있습니다. 관리자에게 문의하세요.")
        log("프로그램을 종료합니다.")
        return
    try:
        saved_count = int(saved_count_str)
    except ValueError:
        log(f"[오류] 'Setting' 탭의 A1 셀 값이 잘못되었습니다: '{saved_count_str}'. 정수여야 합니다.")
        log("프로그램을 종료합니다.")
        return

    try:
        start_row = saved_count + 2
        results = worksheet_data.batch_get(['A1:ZZ1', f'A{start_row}:ZZ'])
        headers = results[0][0] if results[0] else []
        new_rows = results[1] if len(results) > 1 else []
        new_records = [dict(zip(headers, row + [''] * (len(headers) - len(row)))) for row in new_rows]
    except Exception as e:
        log(f"[오류] 구글 시트 데이터를 읽어오지 못했습니다. 관리자에게 문의하세요: {e}")
        log("프로그램을 종료합니다.")
        return

    if not new_records:
        log("✅ 새롭게 추가된 접수건이 없습니다. 프로그램을 종료합니다.")
        return

    log(f"총 {len(new_records)}개의 새로운 데이터를 발견했습니다! 전송 시작...")

    current_count = saved_count

    # [3. 반복 작업 시작]
    for row in new_records:
        data = {
            'email' : row.get('이메일 주소', 'None'),
            'ceo_name' : row.get('대표님의 성함을 알려주세요.(Please tell us your name.)', 'None'),
            'ceo_contact' : row.get('대표님의 연락처를 알려주세요. (Please share relevant contact information for us to reach back.)', 'None'),
            'abstraction' : row.get('1. 팀의 Service/Product를 한 문장으로 정의해 주세요. (Please define your company\'s service/product in one sentence.)', 'None'),
            'problem' : row.get('2. 문제 정의 : 누구의 & 어떤 구체적 고충/불편을 해결하려고 하시나요? (What are the pain points/problems you are trying to solve?)', 'None'),
            'solution' : row.get('3. 솔루션 : 그 고충/불편을 어떤 방법으로 해결하려고 하시나요?(How do you propose to solve said pain points/ problems?)', 'None'),
            'bm' : row.get('4. 핵심 비즈니스 모델(e.g. 거래 수수료, 구독 모델)은 무엇인가요?(What is the core business model?)', 'None'),
            'peer_group' : row.get('5. 핵심 경쟁사들은 어떤 곳이 있나요? 핵심 경쟁사 대비 우리 서비스와 기술의 경쟁력은 어떤 것이 있을까요?(Who do you perceive as your key competitors? What are your company\'s key competitive advantages?) ', 'None'),
            'team' : row.get('6. 대표님과 핵심 구성원(c-level) 의 주요 이력을 알려주세요.(Please share your team\'s key background including but not exclusively academic and professional.)', 'None'),
            'best_reason' : row.get('7. 우리 팀이 이 서비스 & 기술을 가장 잘할 수 있는 이유를 알려주세요. (Why is your team best suited to succeeding in said particular domain?', 'None'),
            'free' : row.get('(선택)추가로 공유해 주고 싶으신 내용이 있으시면 자유롭게 말씀해 주세요. (Please share any and all information that your team may wish to share with us)', 'None'),
            'link' : row.get('팀의 사업계획서를 첨부해주세요.(Please attach a copy of your business plan or IR deck.)', 'None'),
            'company_name' : row.get('기업명을 알려주세요.(Please tell us your company name.)', 'None'),
        }
        
        # ==========================================
        # find the mail from zerobot and move to each mailbox.
        # ==========================================
        query = f'in:inbox subject:"[카카오벤처스] {data["company_name"]} 로부터 접수된 사업계획서"'

        # search mail
        try:
            results = gmail_service.users().messages().list(userId='me', q=query).execute()
        except Exception as e:
            log(f"[{data['company_name']}] Gmail 검색 오류. 관리자에게 문의하세요: {e}")
            log("프로그램을 종료합니다.")
            return
        messages = results.get('messages', [])

        if not messages:
            log(f"[{data['company_name']}] Gmail에서 해당 이메일을 찾지 못했습니다. 관리자에게 문의하세요.")
            log("프로그램을 종료합니다.")
            return
        
        # send to notion db
        try:
            success, msg = create_notion_page(data, notion_tag)
        except Exception as e:
            log(f"[{data['company_name']}] 노션 등록 중 통신 오류. 관리자에게 문의하세요: {e}")
            return

        if success:
            log(f"✅ [{data['company_name']}] 노션 등록 성공! (할당: {notion_tag})")

            msg_id = messages[0]['id']
            target_label_id = label_map.get(gmail_label)
        
            try:
                # move to each person's mailbox
                gmail_service.users().messages().modify(
                    userId='me',
                    id=msg_id,
                    body={
                        'addLabelIds': [target_label_id],
                        'removeLabelIds': ['INBOX']
                    }
                ).execute()
                log(f"이메일을 '{gmail_label}' 라벨로 성공적으로 이동했습니다.")
            except Exception as e:
                log(f"노션은 생성되었으나, 지메일 이동 중 통신 에러가 발생했습니다. 다음 접수 건으로 진행됩니다.: {e}")

            # update count and cell A1 when we success create and send to notion.
            current_count += 1
            try:
                worksheet_settings.update_acell('A1', current_count)
            except Exception as e:
                log(f"[{data['company_name']}] 카운터 업데이트 실패. 관리자에게 문의하세요: {e}")
                return

        else: # when we can't create the page.
            log(f"[{data['company_name']}] 노션 등록 실패. 관리자에게 문의하세요 : {msg}")
            return
        time.sleep(1.01) #sleep for avoid api ban.

    log("🎉 *모든 동기화 작업이 완료되었습니다!*")

_DROP_MAIL_TEMPLATE = """\
안녕하세요 {ceo_name} 대표님,
카카오벤처스입니다.

먼저 카카오벤처스에 관심을 가지고 연락 주셔서 감사드립니다.

보내주신 이메일을 바탕으로 내부 검토 과정을 거쳤으나, 아쉽게도 본 투자 건에 대한 추가 검토 진행은 힘들 것으로 결론나게 되었습니다. 가능하면 대표님 한 분 한 분을 찾아 뵙고 미팅을 가지고 싶지만, 저희 역시 여러 한계와 어려움 속에서 일 하다 보니 안타깝게도 만나 뵙지 못하는 분들이 많아 죄송스런 마음입니다.

투자 결정에 있어서 다양한 요소가 작용하는 만큼 추가 검토가 어려운 이유를 꼭 집어서 말씀드리기 어려운 점 또한 양해 부탁드립니다. 저희 또한 다양한 투자사 중 하나에 불과하기 때문에 판단이 틀릴 때가 있음을 고려하셔서 처음 가지셨던 믿음과 의지로 사업을 해 나가시길 바랍니다. 멀리서나마 사업 번창하시고, 건승하시기를 바라겠습니다.

감사합니다.
카카오벤처스 드림"""

_DROP_MAIL_TEMPLATE_EN = """\
Hello {ceo_name},
Thank you for reaching out to Kakao Ventures and sending your deck.

We wanted to let you know that we've finished performing an internal review of your business proposal, but unfortunately, decided not to advance it further. The reasons why we cannot proceed further are subjective, and considering that we are simply one investor among many, there are many times in which we make mistakes. As such, we hope that you continue persevering to see the realization of your business, and if possible, please let us know how it goes. We would love to learn from our mistakes.

We wish you success in all your future endeavors.

Best regards,
Kakao Ventures"""


#when korean exists, return true.
def is_korean(text):
    return bool(re.search(r'[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]', text or ""))


KOREAN_EMAIL_DOMAINS = {
    "naver.com", "hanmail.net", "daum.net", "kakao.com", "nate.com",
    "korea.com", "empas.com", "paran.com", "dreamwiz.com",
}


def email_domain(email):
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip(" <>,;")


def is_korean_email_domain(email):
    domain = email_domain(email)
    if not domain:
        return False
    return domain.endswith(".kr") or domain in KOREAN_EMAIL_DOMAINS


#\uC811\uC218 \uBA54\uC77C\uC740 \uC591\uC2DD \uC790\uCCB4\uAC00 \uD55C\uAE00\uC774\uBBC0\uB85C, \uC9C0\uC6D0\uC790\uAC00 \uC9C1\uC811 \uC4F4 \uB2F5\uBCC0\uB9CC \uB0A8\uAE30\uACE0 \uC591\uC2DD \uBB38\uAD6C\uB97C \uAC77\uC5B4\uB0B8\uB2E4.
_INTAKE_BOILERPLATE = [
    r"\uC544\uB798\uC640 \uAC19\uC740 \uB0B4\uC6A9\uC73C\uB85C \uC124\uBB38\uC9C0\uB97C \uD1B5\uD574 \uC0AC\uC5C5\uACC4\uD68D\uC11C\uAC00 \uC811\uC218\uB418\uC5C8\uC2B5\uB2C8\uB2E4\.?",
    r"Reply\uB97C \uD560 \uACBD\uC6B0 \uC544\uB798 \uC774\uBA54\uC77C\uB85C \uBA54\uC77C\uC774 \uBC1C\uC1A1\uB429\uB2C8\uB2E4\.?",
    r"\uB300\uD45C\uB2D8 \uC131\uD568\s*:", r"\uAE30\uC5C5\uBA85\s*:", r"\uC804\uD654\uBC88\uD638\s*:", r"\uC774\uBA54\uC77C\s*\uC8FC\uC18C\s*:", r"IR\s*\uB371\s*:",
    r"\d+\s*\.\s*\uD55C\uC904\uC18C\uAC1C", r"\d+\s*\.\s*\uD300\s*\uC774\uB825", r"\d+\s*\.\s*\uBB38\uC81C\s*\uC815\uC758",
    r"\d+\s*\.\s*\uC194\uB8E8\uC158\s*\uC124\uBA85", r"\d+\s*\.\s*BM", r"\d+\s*\.\s*\uACBD\uC7C1\uC0AC\uC640\s*\uC6B0\uB9AC\uC758\s*\uACBD\uC7C1\uB825",
    r"\d+\s*\.\s*\uC6B0\uB9AC\uD300\uC774\s*\uCD5C\uACE0\uC778\s*\uC774\uC720", r"\d+\s*\.\s*\uCD94\uAC00\uC0AC\uD56D",
    r"https?://\S+", r"[\w.+-]+@[\w-]+\.[\w.-]+", r"-{3,}",
]

KOREAN_RATIO_THRESHOLD = 0.15
MIN_SIGNAL_CHARS = 20


def applicant_text(body):
    #strip the Korean intake form scaffolding so only the applicant's own answers remain.
    text = body or ""
    for pattern in _INTAKE_BOILERPLATE:
        text = re.sub(pattern, " ", text)
    return text


def korean_ratio(text):
    korean = len(re.findall(r'[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]', text or ""))
    latin = len(re.findall(r'[A-Za-z]', text or ""))
    total = korean + latin
    if total == 0:
        return 0.0, 0
    return korean / total, total


def decide_language(page, email_body=""):
    #decide ko/en from the applicant's own words in the intake mail, then fall back to
    #Notion metadata (service_def / company / ceo / email domain).
    #returns (lang, reason) - reason is logged so the choice is auditable.
    ratio, signal = korean_ratio(applicant_text(email_body))
    if signal >= MIN_SIGNAL_CHARS:
        pct = round(ratio * 100)
        if ratio >= KOREAN_RATIO_THRESHOLD:
            return "ko", f"\uBCF8\uBB38 \uD55C\uAE00 {pct}%"
        return "en", f"\uBCF8\uBB38 \uD55C\uAE00 {pct}%"

    for field, label in (("service_def", "\uC11C\uBE44\uC2A4\uC815\uC758"), ("company_name", "\uAE30\uC5C5\uBA85"), ("ceo_name", "\uB300\uD45C\uBA85")):
        if is_korean(page.get(field, "")):
            return "ko", f"{label} \uD55C\uAE00"

    if is_korean_email_domain(page.get("email", "")):
        return "ko", f"\uC774\uBA54\uC77C \uB3C4\uBA54\uC778 {email_domain(page.get('email', ''))}"

    if page.get("service_def") or page.get("company_name"):
        return "en", "\uD55C\uAE00 \uC2E0\uD638 \uC5C6\uC74C"

    return "ko", "\uC815\uBCF4 \uC5C6\uC74C \u2192 \uAE30\uBCF8\uAC12"


# ==========================================
def query_to_drop_pages():
    #return page list of satisfying To do = 'To Drop' and check box = false int Notion DB
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "To do", "select": {"equals": "To Drop"}},
                {"property": "드랍메일", "checkbox": {"equals": False}}
            ]
        }
    }
    pages = []
    has_more = True
    start_cursor = None

    while has_more:
        if start_cursor:
            payload["start_cursor"] = start_cursor
        try:
            response = requests.post(url, headers=NOTION_HEADERS, json=payload)
        except Exception as e:
            raise RuntimeError(f"Notion API 통신 오류: {e}")
        if response.status_code != 200:
            raise RuntimeError(f"Notion query 실패 status={response.status_code} body={response.text[:300]}")
        data = response.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            
            company_name = props.get("기업명", {}).get("title", [{}])
            company_name = company_name[0].get("text", {}).get("content", "") if company_name else ""
            
            ceo_name_list = props.get("대표명", {}).get("rich_text", [{}])
            ceo_name = ceo_name_list[0].get("text", {}).get("content", "") if ceo_name_list else ""
            
            email_list = props.get("이메일", {}).get("rich_text", [{}])
            email_addr = email_list[0].get("text", {}).get("content", "") if email_list else ""
            
            service_def_list = props.get("서비스정의", {}).get("rich_text", [{}])
            service_def = service_def_list[0].get("text", {}).get("content", "") if service_def_list else ""
            
            pages.append({
                "page_id": page["id"],
                "company_name": company_name,
                "ceo_name": ceo_name,
                "email": email_addr,
                "service_def": service_def,
            })
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
        if has_more and not start_cursor:
            raise RuntimeError("Notion API 비정상 응답: has_more=True이나 next_cursor가 없습니다.")

    return pages


def _decode_part(part):
    data = part.get('body', {}).get('data')
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
    except Exception:
        return ""


def extract_body_text(payload):
    #walk the MIME tree and return the message text (prefer text/plain, fall back to stripped html).
    plain, html = [], []

    def walk(part):
        mime_type = part.get('mimeType', '')
        if mime_type == 'text/plain':
            plain.append(_decode_part(part))
        elif mime_type == 'text/html':
            html.append(_decode_part(part))
        for sub in part.get('parts', []) or []:
            walk(sub)

    walk(payload or {})
    if any(plain):
        return "\n".join(plain)
    return re.sub(r'<[^>]+>', ' ', "\n".join(html))


def find_original_message(gmail_service, company_name, label):
    #return thread_id, message_id, subject, body of email. if not exists, return (None, None, None, "")
    query = f'subject:"[카카오벤처스] {company_name} 로부터 접수된 사업계획서"'
    query = f'label:"{label}" {query}'
    results = gmail_service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])
    if not messages:
        return None, None, None, ""
    msg = gmail_service.users().messages().get(userId='me', id=messages[0]['id'], format='full').execute()
    payload = msg.get('payload', {})
    thread_id = msg.get('threadId')
    message_id = next((h['value'] for h in payload.get('headers', [])
                       if h['name'] == 'Message-ID'), None)
    subject = next((h['value'] for h in payload.get('headers', [])
                    if h['name'] == 'Subject'), '')
    return thread_id, message_id, subject, extract_body_text(payload)


def _template_to_gmail_html(text):
    """템플릿 텍스트를 Gmail 수동 reply와 동일한 <div> 블록 구조 HTML로 변환."""
    lines = text.split("\n")
    if not lines:
        return "<div><br></div>"
    parts = []
    for line in lines:
        if line == "":
            parts.append("<div><br></div>")
        else:
            parts.append(f"<div>{_html_escape(line)}</div>")
    return "".join(parts)


def _build_drop_mail_mime(to_email, subject, ceo_name, lang, in_reply_to):
    """드랍메일 초안용 multipart/alternative MIME 메시지 생성.

    Gmail 초안 에디터가 긴 text/plain 라인을 중간에서 wrap하며 문단 빈 줄을
    접는 현상을 피하기 위해, Gmail 수동 작성 시의 구조를 모방한
    text/html (div 블록) 파트를 함께 포함한다.
    """
    template = _DROP_MAIL_TEMPLATE if lang == "ko" else _DROP_MAIL_TEMPLATE_EN
    plain_body = template.format(ceo_name=ceo_name)
    html_body = _template_to_gmail_html(plain_body)

    mime = MIMEMultipart('alternative')
    mime['To'] = to_email
    mime['Subject'] = subject if subject.startswith("Re: ") else f"Re: {subject}"
    if in_reply_to:
        mime['In-Reply-To'] = in_reply_to
        mime['References'] = in_reply_to

    mime.attach(MIMEText(plain_body, 'plain', 'utf-8'))
    mime.attach(MIMEText(html_body, 'html', 'utf-8'))
    return mime


def create_draft_reply(gmail_service, thread_id, message_id, original_subject, to_email, ceo_name, lang="ko"):
    """원본 스레드에 답장 형태로 Gmail 초안 생성"""
    mime = _build_drop_mail_mime(
        to_email=to_email,
        subject=original_subject,
        ceo_name=ceo_name,
        lang=lang,
        in_reply_to=message_id,
    )
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    draft_body = {'message': {'raw': raw, 'threadId': thread_id}}
    gmail_service.users().drafts().create(userId='me', body=draft_body).execute()


def mark_draft_created(page_id):
    """Notion 페이지의 드랍메일 체크박스를 true로 업데이트"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {"properties": {"드랍메일": {"checkbox": True}}}
    response = requests.patch(url, headers=NOTION_HEADERS, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"Notion 체크박스 업데이트 실패 (status: {response.status_code}): {response.text}")


# ==========================================
def run_drop(label):
    """드랍메일 워크플로우: To Drop 항목 조회 → 원본 스레드에 Gmail 초안 생성 + Notion 체크."""

    def log(text):
        print(text, flush=True)

    try:
        gmail_service = get_gmail_service()
    except Exception as e:
        log(f"Gmail API 연결 실패: {e}")
        return

    # 라벨 유효성 체크 (없는 라벨이면 어차피 0건이라 미리 안내하고 종료)
    try:
        label_map = get_label_ids(gmail_service)
    except Exception as e:
        log(f"Gmail 라벨 조회 실패. 관리자에게 문의하세요: {e}")
        return
    if label not in label_map:
        log(f"지메일에 '{label}' 라벨이 없습니다. 라벨 이름을 확인하고 다시 실행해주세요.")
        return

    try:
        pages = query_to_drop_pages()
    except Exception as e:
        log(f"Notion API 오류가 발생했습니다. 관리자에게 문의하세요: {e}")
        return
    if not pages:
        log("모든 To Drop 항목에 대한 드랍 메일이 이미 처리되었습니다.")
        return

    processed = 0
    for page in pages:
        company = page["company_name"]
        try:
            result = find_original_message(gmail_service, company, label=label)
        except Exception as e:
            log(f"[{company}] Gmail 검색 오류. 관리자에게 문의하세요: {e}")
            return
        if result[0] is None:
            continue
        thread_id, message_id, original_subject, body = result

        lang, reason = decide_language(page, email_body=body)

        try:
            create_draft_reply(gmail_service, thread_id, message_id, original_subject,
                               page["email"], page["ceo_name"], lang=lang)
        except Exception as e:
            log(f"❌ [{company}] 드랍 메일 초안 생성 실패: {e}\n관리자에게 문의하세요.")
            return

        try:
            mark_draft_created(page["page_id"])
        except Exception as e:
            log(f"❌ [{company}] Notion 체크박스 업데이트 실패 (초안은 생성됨): {e}\n노션에서 수동으로 체크해주세요.\n관리자에게 문의하세요.")
            return

        log(f"✅ [{company}] 드랍 메일 초안 생성 완료 ({lang.upper()}, {reason})")
        processed += 1

    if processed == 0:
        log(f"'{label}' 라벨에서 드랍 처리할 항목을 찾지 못했습니다. 노션과 메일 데이터가 일치하지 않습니다.")
        return
    log("🎉 드랍 메일 초안 생성 작업이 완료되었습니다!")


# ==========================================
def _prompt(label_text, default):
    """터미널 입력 프롬프트. 빈 입력(Enter)이면 기본값 사용."""
    value = input(f"{label_text} [{default}]: ").strip()
    return value or default


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None

    if cmd == "drop":
        label = _prompt("Gmail 라벨", DEFAULT_GMAIL_LABEL)
        run_drop(label)
    elif cmd == "sync":
        gmail_label = _prompt("Gmail 라벨", DEFAULT_GMAIL_LABEL)
        notion_tag = _prompt("Notion 담당자 태그", DEFAULT_NOTION_TAG)
        run_sync(gmail_label, notion_tag)
    else:
        print("사용법: python main.py [drop|sync]")
        print("  drop : To Drop 기업에 드랍메일 초안을 Gmail에 생성")
        print("  sync : 구글시트 신규 접수건을 Notion 등록 + Gmail 라벨 이동")
        sys.exit(1)


if __name__ == "__main__":
    main()
