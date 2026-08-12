"""[W10] 연락처 해석 계약 — **엉뚱한 사람에게 메일이 가지 않는가**.

이 모듈이 틀리면 되돌릴 수 없다. 심사역이 카드에 뜬 주소를 To 에 붙여넣는 순간
콜드메일이 나가고, 그 대상이 창업자가 아니라 웹에이전시·기자·제3자일 수 있다.

## 왜 이 파일이 생겼나

2026-08-11 적대 리뷰가 이렇게 시작했다:

    🔴 TypeError: 'Contact' object is not subscriptable
       → 계약을 통과한 **첫 후보**에서 무조건. 즉 연락처 기능이 붙은 meeting-mail 은
         **한 번도 성공한 적이 없다.**

테스트가 0본이었고 `--synthetic` 은 이 경로를 안 탔다. 기능이 완전히 죽어 있는데
파이프라인은 "정상"으로 보였다 — 잡이 실패하면 attempt 를 새로 열어 claude 를 더 태우고
초안만 0건이 되기 때문이다. **돌려본 적 없는 코드는 안 돌아간다**가 이 파일의 존재 이유다.

    K1  후보(dict)와 연락처(Contact)를 섞지 않는다 — 실행되는 경로로 확인
    K2  역할 주소는 개인이 아니다(구분자를 바꿔도 뚫리지 않는다)
    K3  mailto 를 그대로 믿지 않는다(다중 수신자·인코딩)
    K4  후보가 여럿이면 **고르지 않는다** — 사람에게 넘긴다
    K5  homepage 는 외부인이 심는 값이다 — 사설·루프백·비 http 로 요청하지 않는다
    K6  스크립트·주석의 문자열은 연락처가 아니다
    K7  연락처는 승인 카드까지 **도달한다**(payload 에 실려야 존재한다)
    K8  카드에 나가는 외부 문자열은 슬랙 제어문자를 못 만든다
    K9  크롤은 적대적인 서버를 견딘다 — **실제 HTTP 로 태운다**
    K10 홈페이지 탐색이 루트 기준으로 돌고, 도메인이 다른 주소는 안 쓴다
    K11 **본문 경로에도** 도메인 대조가 걸린다 (2차 리뷰 H1)
    K12 공유 호스팅에서는 도메인 일치를 채택 근거로 쓰지 않는다 (2차 리뷰 H2)
    K13 리다이렉트를 홉마다 재검증하며 따라간다 (2차 리뷰 H3)
    K14 **s_draft 를 실제로 실행한다** — grep 이 아니라 (테스트 리뷰 HIGH-1)

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_contact.py
외부 네트워크 0 — K9 만 **루프백 로컬 서버**를 띄운다. 스트리밍 상한·데드라인·
리다이렉트 비추종은 문자열 주입으로는 못 본다(그 코드가 아예 안 돌기 때문이다).
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "kv-core"))
sys.path.insert(0, str(_REPO / "skills/net-sourcing/project"))

from core import contact as C  # noqa: E402
from kv_core import approvals_tick as T  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# ══════════════════════════════════════════════════════════════════════
print("=== K1: 후보(dict)와 연락처(Contact)를 섞지 않는다 ===\n")
# 리뷰가 잡은 원본 결함의 형태를 그대로 재현한다 — resolve 결과를 rec 에 싣고 **그 뒤에도**
# 후보 dict 를 계속 쓸 수 있어야 한다. 이름을 덮으면 여기서 TypeError 로 죽는다.
cand = {"page_id": "p-abc", "name": "티로", "raw_text": "person-ad2ad12c6fa7@example.com 로 연락주세요"}
rec: dict = {}
ct = C.resolve(cand, crawl=False)
rec["contact_channel"] = ct.channel
rec["founder_email"] = ct.email or ""
try:
    rec["page_id"] = cand["page_id"]
    rec["name"] = cand["name"]
    ok = True
except TypeError as e:
    ok = False
    print(f"      🔴 {e!r}")
check("K1 resolve 뒤에도 후보 dict 를 그대로 쓸 수 있다", ok)
check("K1 해석 결과가 rec 에 실린다",
      rec.get("contact_channel") == "email" and rec.get("founder_email") == "person-ad2ad12c6fa7@example.com",
      str(rec))

_job = (_REPO / "skills/net-sourcing/project/jobs/meeting_mail_job.py").read_text(encoding="utf-8")
# (구) 여기 `"c = contact.resolve(" not in _job` grep 단언이 있었다. 테스트 리뷰가 변이로
# 무력화를 실증했다 — 이름만 바꾸거나 재바인딩을 한 줄 미루면 통과한다. K14 가 대신한다.

print("\n=== K2: 역할 주소는 개인이 아니다 ===\n")
role = ["person-d03d4368c236@example.com", "person-640222a0dd49@example.com", "person-22c445ba383f@example.com", "person-ecac354a18d5@example.com",
        "person-4aa14dd3f875@example.com", "person-c7d91129e839@example.com", "person-39a158b75318@example.com", "person-e6301c019205@example.com"]
person = ["person-ad2ad12c6fa7@example.com", "person-a7d2476c0d0b@example.com", "person-1cf36d4722de@example.com"]
check("K2 역할 주소 8종이 전부 걸린다", all(not C.from_text(a) for a in role),
      str([a for a in role if C.from_text(a)]))
check("K2 개인 주소는 통과한다", all(C.from_text(a) for a in person),
      str([a for a in person if not C.from_text(a)]))

print("\n=== K3: mailto 를 그대로 믿지 않는다 ===\n")
html_multi = '<a href="mailto:person-8158f8c630cb@example.com,person-87b09eb0e9c9@example.com">문의</a>'
check("K3 다중 수신자 mailto 를 통째로 채택하지 않는다",
      "person-8158f8c630cb@example.com,person-87b09eb0e9c9@example.com" not in C._clean(C._mailto(html_multi)),
      str(C._clean(C._mailto(html_multi))))
check("K3 퍼센트 인코딩을 푼다",
      C._clean(C._mailto('<a href="mailto:%person-6a6a22f29044@example.com">x</a>')) == ["person-ad2ad12c6fa7@example.com"])

print("\n=== K4: 후보가 여럿이면 고르지 않는다 ===\n")
amb = C.resolve({"raw_text": "person-ad2ad12c6fa7@example.com 또는 person-f090869fb9f0@example.com"}, crawl=False)
check("K4 후보 2건이면 ambiguous — 조용히 최선치를 고르지 않는다",
      amb.channel == "ambiguous" and not amb.sendable, f"{amb.channel} / {amb.source}")
one = C.resolve({"raw_text": "person-ad2ad12c6fa7@example.com"}, crawl=False)
check("K4 후보 1건이면 email", one.channel == "email" and one.sendable)
dm = C.resolve({"linkedin_url": "https://linkedin.com/in/x"}, crawl=False)
check("K4 이메일이 없으면 DM — 실패가 아니라 정상 결과다",
      dm.channel == "linkedin_dm" and not dm.sendable)

print("\n=== K5: homepage 는 외부인이 심는 값이다 ===\n")
hostile = ["http://127.0.0.1:8899/internal", "http://user:pass@127.0.0.1/",
           "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd",
           "//evil.com/x", "http://[::1]/", "http://10.0.0.5/"]
bad = [u for u in hostile if C._public_host(u)]
check("K5 사설·루프백·링크로컬·비 http 는 요청 대상이 아니다", not bad, str(bad))
check("K5 소셜 링크는 크롤하지 않는다(우리가 이미 가진 정보다)",
      C._public_host("https://kr.linkedin.com/in/x") is None)
# 🔴 **양성 대조가 없으면 이 함수가 통째로 죽어도 초록이다.**
# 테스트 리뷰가 `_public_host` 를 항상 None 으로 바꿔도 4본 전부 통과함을 실증했다 —
# 거부만 시험하면 "전부 거부"가 정답이 되기 때문이다. 그러면 홈페이지 경로가 조용히
# 죽고 증상은 "이메일을 못 찾았다 → DM"이라 **정상 결과와 구분되지 않는다**.
import socket as _sock  # noqa: E402
_orig_gai = _sock.getaddrinfo
_sock.getaddrinfo = lambda h, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
try:
    check("K5 정상 공개 호스트는 **통과시킨다**(거부만 시험하면 함수가 죽어도 초록이다)",
          C._public_host("https://acme.io/") == "acme.io",
          str(C._public_host("https://acme.io/")))
finally:
    _sock.getaddrinfo = _orig_gai
# [2차 리뷰 MED-4] 소스 grep 단언은 형태만 맞으면 통과한다 — 실증된 우회가 있었다
# (`allow_redirects=bool(1),  # 정책: allow_redirects=False`). K9/K13 이 행위로 잡으므로 삭제.

print("\n=== K6: 스크립트·주석의 문자열은 연락처가 아니다 ===\n")
noise_html = ('<script>Sentry.init({dsn:"https://person-da3b4eebfc61@example.com/1"});</script>'
              '<style>a{}</style><!-- person-b73be1a2cd3c@example.com -->'
              '<p>대표 이메일: person-ad2ad12c6fa7@example.com</p>')
got = C._clean(C._EMAIL.findall(C._NOISE.sub(" ", noise_html)))
check("K6 Sentry DSN·주석 주소가 후보에서 빠진다", got == ["person-ad2ad12c6fa7@example.com"], str(got))
check("K6 사이트 도메인과 다른 주소는 채택하지 않는다",
      C._reg_domain("webagency.co.kr") != C._reg_domain("www.acme.io"),
      "제작 에이전시 주소를 창업자로 오인하는 축")

print("\n=== K7: 연락처가 승인 카드까지 도달한다 ===\n")
# payload 에 안 실리면 카드의 연락처 블록은 **도달 불가능한 코드**다(리뷰 M2).
_mm = _job[_job.index("def _enqueue("):_job.index("def _enqueue(") + 1400]
check("K7 승인 payload 에 연락처 4개 키가 실린다",
      all(k in _mm for k in ("contact_channel", "founder_email",
                             "contact_handle", "contact_source")),
      "drafts.jsonl 에만 있고 큐로 안 가면 없는 것과 같다")

card_dm = T._card({"kind": "mail", "key": "k1", "task": "meeting-mail",
                   "payload": {"to": "deals@example.vc", "subject": "s", "body": "b",
                               "contact_channel": "threads_dm", "contact_handle": "@founder"}})
check("K7 DM 으로 가야 하는 건은 카드에서 그렇게 보인다", "DM 으로 보내야 함" in card_dm)
card_amb = T._card({"kind": "mail", "key": "k2", "task": "meeting-mail",
                    "payload": {"to": "x", "subject": "s", "body": "b",
                                "contact_channel": "ambiguous",
                                "contact_source": "후보 2건: person-aa8a6b122601@example.com, person-da9a33894db9@example.com"}})
check("K7 모호한 건은 사람이 고르라고 카드에 뜬다", "사람이 고른다" in card_amb)

print("\n=== K8: 카드로 나가는 외부 문자열은 슬랙 제어문자를 못 만든다 ===\n")
card_inj = T._card({"kind": "mail", "key": "k3", "task": "meeting-mail",
                    "payload": {"to": "x", "subject": "s", "body": "b",
                                "contact_channel": "linkedin_dm",
                                "contact_handle": "<!channel> <http://evil.io|여기클릭>"}})
check("K8 `<!channel>` 이 그대로 렌더되지 않는다", "<!channel>" not in card_inj,
      card_inj.splitlines()[3] if len(card_inj.splitlines()) > 3 else card_inj)
card_pii = T._card({"kind": "mail", "key": "k4", "task": "meeting-mail",
                    "payload": {"to": "x", "subject": "s", "body": "b",
                                "contact_channel": "email", "founder_email": "person-ad2ad12c6fa7@example.com"}})
check("K8 제3자 이메일은 마스킹해 남긴다(슬랙 이력에 영구 잔존)",
      "person-ad2ad12c6fa7@example.com" not in card_pii and "acme.io" in card_pii,
      [ln for ln in card_pii.splitlines() if "📧" in ln])

print("\n=== K9: 크롤은 적대적인 서버를 견딘다 (실제 HTTP) ===\n")
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class _Hostile(BaseHTTPRequestHandler):
    """리뷰 프로브가 쓴 적대 서버 3종을 그대로 재현한다."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/huge":                       # 80MB — 전량 수신하면 RSS 폭발
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            chunk = b"<p>x</p>" * 1024
            try:
                for _ in range(10_000):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass                                   # 클라가 상한에서 끊었다 = 정상
        elif self.path == "/binary":                   # HTML 이 아니다
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(b"\x00\person-3353e7038435@example.com")
        elif self.path == "/redir":                    # 겉보기 외부 → 내부로 우회
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/internal")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>대표 person-ad2ad12c6fa7@example.com</p>".encode())


_srv = HTTPServer(("127.0.0.1", 0), _Hostile)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_base = f"http://127.0.0.1:{_srv.server_port}"
# [2차 리뷰 H3] `_fetch` 는 이제 **홉마다** `_public_host` 로 재검증한다(리다이렉트 추종을
# 되살리면서 SSRF 를 막는 방식). 그래서 루프백 서버로 본문 처리를 시험하려면 그 검사만
# 비켜야 한다 — 검사 자체는 바로 아래 "resolve 는 루프백을 크롤하지 않는다"가 지킨다.
_saved_host = C._public_host
_ok_hostport = f"127.0.0.1:{_srv.server_port}"
# **테스트 서버 포트만** 허용한다. 다른 곳(리다이렉트 목적지)은 진짜 검사를 그대로 탄다 —
# 그래야 "홉마다 재검증한다"가 실물로 시험된다(전부 허용하면 그 검사가 검증 밖으로 나간다).
C._public_host = lambda u: "127.0.0.1" if _ok_hostport in str(u) else _saved_host(u)
try:
    body = C._fetch(f"{_base}/huge")
    check("K9 거대 응답을 상한에서 끊는다(전량 수신 후 자르지 않는다)",
          len(body) <= C._MAX_BYTES + 8192, f"수신 {len(body):,}자 · 상한 {C._MAX_BYTES:,}")
    check("K9 HTML 이 아니면 읽지 않는다", C._fetch(f"{_base}/binary") == "",
          "바이너리에서 이메일 정규식을 돌리지 않는다")
    check("K9 리다이렉트가 내부를 가리키면 그 홉에서 멈춘다(추종은 하되 재검증한다)",
          C._fetch(f"{_base}/redir") == "",
          "302 → http://127.0.0.1/internal 은 홉 검증에 걸린다")
    check("K9 정상 페이지는 읽는다(K9 가 전부 막는 게 아님을 보인다)",
          "person-ad2ad12c6fa7@example.com" in C._fetch(f"{_base}/ok"))
finally:
    C._public_host = _saved_host
    _srv.shutdown()
check("K9 그럼에도 resolve 는 루프백을 크롤 대상으로 삼지 않는다",
      C.resolve({"homepage": f"{_base}/ok"}, crawl=True).channel == "none",
      "SSRF 차단이 살아 있다 — 위 스텁을 되돌린 뒤 확인한다")

print("\n=== K10: 홈페이지 탐색은 루트 기준 · 도메인이 다르면 안 쓴다 ===\n")
_seen: list[str] = []


def _stub_fetch(url, *, deadline=None):
    _seen.append(url)
    if url.endswith("/contact"):
        return ('<footer><a href="mailto:person-094ed0b9b649@example.com">제작</a>'
                '<p>대표 person-ad2ad12c6fa7@example.com</p></footer>')
    return "<p>회사 소개</p>"


_orig_fetch, _orig_host = C._fetch, C._public_host
C._fetch, C._public_host = _stub_fetch, (lambda u: "acme.io")
try:
    emails, tried = C.from_site("https://acme.io/blog")
    check("K10 `/contact` 를 **루트 기준**으로 두드린다(`/blog/contact` 아님)",
          "https://acme.io/contact" in _seen, str(_seen))
    check("K10 사이트 도메인과 같은 주소만 채택한다", emails == ["person-ad2ad12c6fa7@example.com"], str(emails))
    check("K10 제작 에이전시 주소는 버린다(비가역 오발송 축)",
          "person-094ed0b9b649@example.com" not in emails)
    check("K10 무엇을 두드렸는지 남긴다(사람이 검증할 수 있게)", len(tried) >= 2, str(tried))
finally:
    C._fetch, C._public_host = _orig_fetch, _orig_host

print("\n=== K11~K13: 2차 리뷰 — 대조를 한 곳에만 걸었던 것 ===\n")
# K11 본문 경로. 여기가 이메일의 **주 공급원**인데 검사가 없었다.
r = C.resolve({"raw_text": "제작 문의: person-094ed0b9b649@example.com",
               "homepage": "https://acme.io", "linkedin_url": "https://linkedin.com/in/x"},
              crawl=False)
check("K11 회사 도메인과 다른 원문 주소는 채택하지 않는다",
      r.channel == "ambiguous" and not r.email, f"{r.channel} / {r.email}")
check("K11 그래도 DM 대상은 남긴다(가진 것을 빼앗지 않는다)", bool(r.handle), str(r.handle))
ok1 = C.resolve({"raw_text": "연락: person-ad2ad12c6fa7@example.com", "homepage": "https://acme.io"}, crawl=False)
check("K11 같은 도메인이면 채택하고 verified 로 표시한다",
      ok1.channel == "email" and ok1.verified, f"{ok1.channel} verified={ok1.verified}")
nov = C.resolve({"raw_text": "연락: person-ad2ad12c6fa7@example.com"}, crawl=False)
check("K11 대조할 홈페이지가 없으면 채택하되 **미검증**으로 표시한다",
      nov.channel == "email" and not nov.verified and "미검증" in nov.source, nov.source)

# K12 공유 호스팅 — 대조가 가장 필요한 표본에서 무너지던 지점
check("K12 공유 호스팅은 도메인 일치를 근거로 쓰지 않는다",
      C._same_org("person-07216f15a293@example.com", "https://acme.github.io") is None
      and C._same_org("person-282a619106c6@example.com", "https://blog.naver.com/acme") is None)
check("K12 자체 도메인은 정상 대조된다",
      C._same_org("person-85199c8ed3d4@example.com", "https://acme.io") is True
      and C._same_org("person-689a9aaeef35@example.com", "https://acme.io") is False)
sh = C.resolve({"raw_text": "연락: person-07216f15a293@example.com",
                "homepage": "https://acme.github.io"}, crawl=False)
check("K12 공유 호스팅에서 온 주소는 verified 가 아니다", not sh.verified, sh.source)

# K13 리다이렉트 — 실제 HTTP 로 확인한다
class _Redir(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/start":
            self.send_response(301)
            self.send_header("Location", "/final")     # 상대 Location
            self.end_headers()
        elif self.path == "/final":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>대표 person-ad2ad12c6fa7@example.com</p>".encode())
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")      # 무한 루프
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


_s2 = HTTPServer(("127.0.0.1", 0), _Redir)
threading.Thread(target=_s2.serve_forever, daemon=True).start()
_b2 = f"http://127.0.0.1:{_s2.server_port}"
_orig_host = C._public_host
C._public_host = lambda u: "127.0.0.1"                 # 홉 검증은 통과시키고 추종만 시험
try:
    check("K13 301 을 따라가 최종 페이지를 읽는다",
          "person-ad2ad12c6fa7@example.com" in C._fetch(f"{_b2}/start"),
          "http:// 홈페이지 2,778건(30%)이 이것 때문에 죽고 있었다")
    check("K13 리다이렉트 루프에서 멈춘다(홉 상한)", C._fetch(f"{_b2}/loop") == "")
finally:
    C._public_host = _orig_host
    _s2.shutdown()
check("K13 홉마다 호스트를 재검증한다(최초 URL 만 보지 않는다)",
      "_public_host(url)" in (_REPO / "skills/net-sourcing/project/core/contact.py")
      .read_text(encoding="utf-8").split("for _ in range(_MAX_HOPS)")[1][:200])

print("\n=== K14: s_draft 를 실제로 실행한다 ===\n")
# 🔴 테스트 리뷰 HIGH-1: 여기가 이 파일의 존재 이유다.
# H1(`TypeError: 'Contact' object is not subscriptable`)이 나흘을 살아남은 이유는
# **그 줄을 아무도 실행하지 않았기 때문**이다. 그래서 잡겠다고 이 파일을 썼는데,
# K1 이 소스 grep 이라 리뷰가 H1 을 **원형 그대로 재주입했을 때 18/18 이 초록**이었다.
# 잡겠다고 만든 테스트가 못 잡으면 그건 테스트가 아니라 통과 개수다.
sys.path.insert(0, str(_REPO / "skills/net-sourcing/project"))
from jobs import meeting_mail_job as MM  # noqa: E402

_jid = "meeting-mail-k14"
_work = MM._work(_jid)
_work.mkdir(parents=True, exist_ok=True)
(_work / "candidates.json").write_text(json.dumps([{
    "page_id": "pg-k14", "name": "루프톤", "one_liner": "AI 재고 최적화",
    "raw_text": "연락은 person-ad2ad12c6fa7@example.com 로 주세요", "homepage": "https://acme.io",
    "linkedin_url": "https://linkedin.com/in/jihoon"}], ensure_ascii=False), encoding="utf-8")

# `contact` 는 s_draft 안에서 import 되므로 모듈 속성이 아니다 — 원본 모듈을 갈아끼운다.
_orig = (MM.session.run_session, MM._cfg, C.from_site)
MM.session.run_session = lambda prompt, **kw: json.dumps([{
    "to": "x", "subject": "[카카오벤처스] 루프톤 미팅 요청의 건",
    "body": "안녕하세요, 루프톤 김철수 대표님\n\n" + "본문 " * 40}], ensure_ascii=False)
MM._cfg = lambda: {"meeting_mail": {"max_drafts_per_run": 3}}
C.from_site = lambda *a, **k: ([], [])                 # 네트워크 0
try:
    MM.s_draft("net-sourcing", _jid)
    crashed = None
except Exception as e:                                 # noqa: BLE001
    crashed = e
finally:
    MM.session.run_session, MM._cfg, C.from_site = _orig

check("K14 s_draft 가 끝까지 돈다(후보 dict 를 Contact 로 덮으면 여기서 죽는다)",
      crashed is None, repr(crashed) if crashed else "")
_dp = MM._drafts_path(_jid)
_rows = [json.loads(x) for x in _dp.read_text(encoding="utf-8").splitlines()] if _dp.exists() else []
check("K14 초안이 실제로 기록된다", len(_rows) == 1, f"{len(_rows)}건")
if _rows:
    d = _rows[0]
    check("K14 후보 식별자가 살아 있다(재바인딩되면 여기가 깨진다)",
          d.get("page_id") == "pg-k14" and d.get("name") == "루프톤", str(d.get("page_id")))
    check("K14 연락처 해석 결과가 실린다",
          d.get("contact_channel") == "email" and d.get("founder_email") == "person-ad2ad12c6fa7@example.com",
          f"{d.get('contact_channel')} / {d.get('founder_email')}")
    check("K14 도메인 검증 결과까지 실린다(자동 발송이 읽을 값)",
          d.get("contact_verified") is True, str(d.get("contact_verified")))
    check("K14 수신자는 코드가 정한다(LLM 출력 불신)",
          d.get("to") == MM.RECIPIENT, str(d.get("to")))

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 연락처 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 연락처 계약 전부 통과")
