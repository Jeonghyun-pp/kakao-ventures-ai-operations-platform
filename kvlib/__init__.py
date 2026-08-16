"""kvlib — 원시 타입과 경로. 통로가 아니다.

여기 있는 것은 **어느 스킬에서도 같은 것**뿐이다:

    fetched      통로의 반환 타입 (0 과 미상을 가른다)
    constraints  [0] 외부 제약의 기계 판본
    paths        데이터 루트 기준 경로

통로(노션·iTunes·슬랙·claude·Apify)는 아직 스킬 안에 있다.
**두 번째 스킬을 만들 때** 실물 둘을 놓고 겹치는 데까지만 밖으로 뺀다 —
미리 빼면 옛 시스템처럼 선을 감으로 긋게 되고, 그러면 두 겹이 된다.
"""
from .fetched import Fetched, Unknown  # noqa: F401
from . import constraints, paths  # noqa: F401
