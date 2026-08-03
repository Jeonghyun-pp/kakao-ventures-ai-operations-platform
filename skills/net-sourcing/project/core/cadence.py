"""(승격됨) 정본은 `kv_core.cadence`. — W4에서 kv-core로 이동.

이 파일은 하위호환 **재노출 shim**이다. 로직은 kv-core에 한 벌만 있다(정본 한 곳 원칙).
기존 `from core import cadence as cad; cad.interval_elapsed(...)` 호출을 깨지 않기 위해 남긴다.
새 코드는 `from kv_core import cadence`를 직접 import 하라.
"""
from kv_core.cadence import DAYS, gap_days, interval_elapsed  # noqa: F401
