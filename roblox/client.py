"""Клиент Roblox для проверки аккаунта игрока и его гейм-пассов.

Используется при активации кода (Вариант Б): по нику игрока собираем отчёт
для админа — какие пассы on-sale, их цена, Place ID, и сверяем с ожидаемой
ценой пасса (за вычетом комиссии Roblox 30%).

Только публичные эндпоинты Roblox, без авторизации. Единственная зависимость —
``requests``. Все функции синхронные — вызывать через ``asyncio.to_thread``.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

_UA = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 15


def parse_robux_amount(product: Optional[str]) -> Optional[int]:
    """Вытащить количество Robux из названия товара (первое число)."""
    m = re.search(r"\d+", product or "")
    return int(m.group()) if m else None


def expected_gamepass_price(robux_amount: Optional[int]) -> Optional[int]:
    """Минимальная цена пасса, чтобы продавец получил robux_amount Robux.

    Roblox берёт 30% комиссии (продавец получает floor(price*0.7)).
    Нужна минимальная P: floor(P*0.7) >= R → ceil(10R/7) = (10R+6)//7.
    Таблица: 100->143, 400->572, 700->1000, 1000->1429.
    """
    if not robux_amount:
        return None
    return (10 * robux_amount + 6) // 7


def get_user(username: str) -> Optional[Dict[str, Any]]:
    """Ник Roblox -> {id, name, displayName} или None."""
    r = requests.post(
        "https://users.roblox.com/v1/usernames/users",
        json={"usernames": [username], "excludeBannedUsers": False},
        headers=_UA, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


def get_games(user_id: int) -> List[Dict[str, Any]]:
    """Опубликованные игры (Experiences) пользователя."""
    games: List[Dict[str, Any]] = []
    cur = None
    while True:
        url = f"https://games.roblox.com/v2/users/{user_id}/games?sortOrder=Asc&limit=50"
        if cur:
            url += f"&cursor={cur}"
        r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        games.extend(payload.get("data", []))
        cur = payload.get("nextPageCursor")
        if not cur:
            break
    return games


def get_universe_passes(universe_id: int) -> Optional[List[Dict[str, Any]]]:
    """Пассы игры: [{id, name, price}] (price может быть None). None -> ошибка."""
    try:
        result: List[Dict[str, Any]] = []
        cur = None
        while True:
            url = f"https://games.roblox.com/v1/games/{universe_id}/game-passes?limit=100&sortOrder=Asc"
            if cur:
                url += f"&cursor={cur}"
            r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            for gp in payload.get("data", []):
                result.append({
                    "id": gp.get("id"),
                    "name": gp.get("name", "Без названия"),
                    "price": gp.get("price"),
                })
            cur = payload.get("nextPageCursor")
            if not cur:
                break
        return result
    except Exception:  # noqa: BLE001
        return None


def get_gamepass_info(gamepass_id: int) -> Tuple[Optional[int], Optional[bool]]:
    """(price, is_for_sale) из product-info пасса. (None, None) при ошибке."""
    try:
        r = requests.get(
            f"https://apis.roblox.com/game-passes/v1/game-passes/{gamepass_id}/product-info",
            headers=_UA, timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return None, None
        info = r.json()
        price = info.get("PriceInRobux")
        if price is None:
            price = (info.get("PriceInformation") or {}).get("DefaultPriceInRobux")
        return price, info.get("IsForSale")
    except Exception:  # noqa: BLE001
        return None, None


def get_inventory_places(user_id: int) -> Optional[List[Dict[str, Any]]]:
    """Place-ассеты из инвентаря (тип 9). None -> инвентарь скрыт (403)."""
    places: List[Dict[str, Any]] = []
    cur = None
    while True:
        url = f"https://inventory.roblox.com/v2/users/{user_id}/inventory/9?limit=100&sortOrder=Asc"
        if cur:
            url += f"&cursor={cur}"
        r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
        if r.status_code == 403:
            return None
        r.raise_for_status()
        payload = r.json()
        places.extend(payload.get("data", []))
        cur = payload.get("nextPageCursor")
        if not cur:
            break
    return places


def build_gamepass_report(
    nickname: str,
    expected_price: Optional[int] = None,
    robux_amount: Optional[int] = None,
) -> Tuple[str, Optional[int], Optional[int]]:
    """(текст отчёта для админа, user_id | None, фактическая цена | None)."""
    nick = (nickname or "").lstrip("@").strip()
    try:
        user = get_user(nick)
    except Exception as e:  # noqa: BLE001
        return f"⚠️ Не удалось найти пользователя {nick}: {e}", None, None
    if not user:
        return f"⚠️ Пользователь {nick} не найден в Roblox.", None, None

    user_id = user["id"]
    header = f"🔗 Профиль: https://www.roblox.com/users/{user_id}/profile\n"
    if expected_price is not None:
        header += f"🎯 Ожидаемая цена пасса: {expected_price} R$ (за {robux_amount} Robux)\n"

    try:
        games = get_games(user_id)
    except Exception:  # noqa: BLE001
        games = None
    if games is None:
        return header + "⚠️ Не удалось получить данные Roblox (проверьте вручную).", user_id, None
    if not games:
        return (
            header + "📭 У пользователя нет игр/Place.\n➡️ Пасс не создан или Place не существует.",
            user_id, None,
        )

    raw_by_id: Dict[int, Dict[str, Any]] = {}
    place_map: Dict[int, Any] = {}
    places = set()
    for g in games:
        universe_id = g.get("id")
        place_id = (g.get("rootPlace") or {}).get("id")
        if place_id:
            places.add(place_id)
        if not universe_id:
            continue
        for gp in (get_universe_passes(universe_id) or []):
            gid = gp.get("id")
            if gid is None:
                continue
            place_map[gid] = place_id
            raw_by_id[gid] = {"id": gid, "name": gp.get("name", "Без названия"), "price": gp.get("price")}

    single_place = next(iter(places)) if len(places) == 1 else None

    for gid, p in raw_by_id.items():
        if p.get("price") is None:
            info_price, is_sale = get_gamepass_info(gid)
            p["price"] = info_price if is_sale else None

    passes = []
    for gid, p in raw_by_id.items():
        if p.get("price") is None:
            continue
        passes.append({
            "id": gid, "name": p.get("name", "Без названия"),
            "price": p["price"], "place_id": place_map.get(gid) or single_place,
        })

    manual_hint = f"\n\n📍 Place ID: {single_place or '—'}"
    if single_place:
        manual_hint += f"\n🌐 https://www.roblox.com/games/{single_place}"
    manual_hint += "\n⚠️ Если игра приватная — пассы через API не видны, проверьте вручную по профилю."

    if not raw_by_id:
        return header + "📭 Гейм-пассы не найдены автоматически." + manual_hint, user_id, None
    if not passes:
        return header + "📭 Активных (on-sale) пассов не найдено (все Offsale)." + manual_hint, user_id, None

    total = len(passes)

    def pass_line(p: Dict[str, Any]) -> str:
        parts = [f"• {p.get('name', 'Без названия')} — {p.get('price')} R$"]
        if p.get("place_id"):
            parts.append(f"  🆔 Place ID: {p['place_id']}")
            parts.append(f"  🌐 https://www.roblox.com/games/{p['place_id']}")
        parts.append(f"  🔗 https://www.roblox.com/game-pass/{p['id']}")
        return "\n".join(parts)

    if expected_price is None:
        lines = [header + f"🎟 Активных (on-sale) пассов ({total}):"]
        for p in passes[:15]:
            lines.append(pass_line(p))
        if total > 15:
            lines.append(f"… и ещё {total - 15}")
        detected = passes[0].get("price") if passes else None
        return "\n".join(lines), user_id, detected

    exact = [p for p in passes if p.get("price") == expected_price]
    others = sorted(passes, key=lambda p: abs(p.get("price") - expected_price))
    others = [p for p in others if p.get("price") != expected_price]
    detected_price = others[0].get("price") if others else None

    lines = [header]
    if exact:
        lines.append(f"✅ НАЙДЕН пасс с нужной ценой {expected_price} R$ — {len(exact)} шт.:")
        for p in exact[:8]:
            lines.append(pass_line(p))
        if len(exact) > 8:
            lines.append(f"… и ещё {len(exact) - 8} с такой же ценой")
    else:
        near = [p for p in others if abs(p["price"] - expected_price) <= 1]
        if near:
            lines.append(f"⚠️ Точного пасса на {expected_price} R$ нет, но есть очень близкие:")
            for p in near[:5]:
                lines.append(pass_line(p))
        else:
            lines.append(f"❌ Пасса с нужной ценой {expected_price} R$ НЕ найдено.")
        if others:
            lines.append(f"\nБлижайшие по цене (из {total} on-sale):")
            for p in others[:6]:
                lines.append(pass_line(p))

    lines.append(f"\n📊 Активных (on-sale) пассов: {total}")
    return "\n".join(lines), user_id, detected_price
