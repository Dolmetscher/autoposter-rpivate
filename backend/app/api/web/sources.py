from fastapi import APIRouter, Depends

from ...constants import PLANNED_SOURCES, SOURCES, get_source
from ...deps import get_current_user
from ...integrations.registry import get_adapter
from ...models import User
from ...schemas import source_out

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
async def list_sources(user: User = Depends(get_current_user)):
    out = []
    for sid in sorted(list(SOURCES.keys()) + list(PLANNED_SOURCES.keys())):
        info = get_source(sid)
        d = source_out(info)
        if info.adapter:
            adapter = get_adapter(sid)
            d["connect_fields"] = adapter.connect_requires()
        out.append(d)
    return {"list": out}
