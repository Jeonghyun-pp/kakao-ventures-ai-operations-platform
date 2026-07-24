"""Gmail 재인증 도우미.

토큰이 만료/취소되어 'Gmail API 끊김'이 발생하면 이 파일을 실행하세요.
브라우저가 열리면 봇용 Gmail 계정으로 로그인 + 권한 허용하면
token.json 이 새로 발급됩니다.

실행: python reauth.py   (또는 .venv/bin/python reauth.py)
"""
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
BASE = Path(__file__).resolve().parent
CRED = BASE / "gmail_credentials.json"
TOKEN = BASE / "token.json"

if not CRED.exists():
    raise SystemExit(f"gmail_credentials.json 가 없습니다: {CRED}")

flow = InstalledAppFlow.from_client_secrets_file(str(CRED), SCOPES)
creds = flow.run_local_server(
    port=0,
    access_type='offline',
    prompt='consent',
    authorization_prompt_message='\n>>> 브라우저가 자동으로 열립니다. 안 열리면 아래 URL을 직접 여세요:\n{url}\n',
    success_message='인증 완료! 이 창은 닫으셔도 됩니다.',
)
TOKEN.write_text(creds.to_json())
print("OK: token.json 새로 발급 완료. refresh_token 보유:", bool(creds.refresh_token))
