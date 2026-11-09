"""Read explicit account interaction flags; missing fields remain unknown."""

from collections.abc import Mapping

from . import codes, endpoints


def following_attribute(value):
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return True if value in (2, 6) else False if value in (0, 4) else None


def favorite_state(transport, dyn_id):
    answer = codes.ensure_ok(
        transport.fetch(
            endpoints.OPUS_DETAIL,
            query={"id": dyn_id, "features": "htmlNewStyle,ugcDelete,editable,opusPrivateVisible"},
            referer=endpoints.dyn_referer(dyn_id),
        ),
        what="读取收藏状态",
    )
    item = (answer.get("data") or {}).get("item") or {}
    modules = item.get("modules") or {}
    holders = modules if isinstance(modules, list) else [modules]
    for holder in holders:
        if not isinstance(holder, Mapping):
            continue
        stat = holder.get("module_stat") or {}
        favorite = stat.get("favorite") or {}
        value = favorite.get("status") if isinstance(favorite, Mapping) else None
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
    return None
