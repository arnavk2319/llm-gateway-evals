from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .router import ParentRef


class Lineage:
    """Maps a call id to the (model, family) that served it.

    Needed for constraints like reviewer independence. In-process LRU first,
    then the event store (so lineage survives across serverless instances).
    """

    def __init__(self, sink: Any = None, maxsize: int = 10_000) -> None:
        self._sink = sink
        self._max = maxsize
        self._d: OrderedDict[str, ParentRef] = OrderedDict()

    async def put(self, span_id: str, model_id: str, family: str) -> None:
        self._d[span_id] = ParentRef(model_id, family)
        self._d.move_to_end(span_id)
        while len(self._d) > self._max:
            self._d.popitem(last=False)

    async def get(self, span_id: str) -> ParentRef | None:
        ref = self._d.get(span_id)
        if ref is not None:
            return ref
        if self._sink is None:
            return None
        try:
            row = await self._sink.get_span_model(span_id)
        except Exception:
            return None
        return ParentRef(*row) if row else None
