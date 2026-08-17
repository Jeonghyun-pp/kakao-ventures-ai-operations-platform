"""kvlib — 원시 타입과 경로. 통로가 아니다.

여기 있는 것은 **어느 스킬에서도 같은 것**뿐이다:

    fetched      통로의 반환 타입 (0 과 미상을 가른다)
    ledger       장부 — 멱등·재개·보고가 전부 여기서 나온다
    constraints  [0] 외부 제약의 기계 판본
    paths        데이터 루트 기준 경로

통로(노션·iTunes·슬랙·claude·Apify)는 `kv_transport/` 에 있다.
두 번째 스킬(링크드인)을 만들면서 실물 둘을 놓고 겹치는 데까지 뽑았다 —
미리 뺐으면 옛 시스템처럼 선을 감으로 긋게 되고, 그러면 두 겹이 된다.
"""
from .fetched import Fetched, Unknown  # noqa: F401
from . import constraints, ledger, paths  # noqa: F401
