"""Выгрузка каталога FazerCard: показывает ID (category_id/card_id/game_id/...)
нужные для создания номиналов в админке.

Запуск в контейнере:
    docker compose exec bot python -m scripts.fzr_catalog
    docker compose exec bot python -m scripts.fzr_catalog giftcards
    docker compose exec bot python -m scripts.fzr_catalog gamekeys
    docker compose exec bot python -m scripts.fzr_catalog raw giftcards   # сырой JSON
"""

from __future__ import annotations

import json
import sys

from fazercard import FazerCardClient


def _rows(data):
    """Достать список позиций из ответа (разные обёртки)."""
    if isinstance(data, dict):
        for key in ("items", "data", "results", "giftcards", "gamekeys", "topups", "categories"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    if isinstance(data, list):
        return data
    return []


def _get(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def dump(kind: str, data) -> None:
    rows = _rows(data)
    print(f"\n=== {kind}: {len(rows)} позиций ===")
    for it in rows:
        if not isinstance(it, dict):
            print(" ", it)
            continue
        cid = _get(it, "category_id", "categoryId", "id", "game_id", "gameId")
        name = _get(it, "name", "title", "label")
        price = _get(it, "price", "cost", "amount")
        print(f"\n• {name}   [id={cid}]" + (f"   price={price}" if price else ""))
        # Вложенные карты/ключи/офферы.
        for sub_key in ("cards", "keys", "offers", "variations", "items", "denominations"):
            subs = it.get(sub_key)
            if isinstance(subs, list) and subs:
                print(f"    {sub_key}:")
                for s in subs:
                    if not isinstance(s, dict):
                        print("     -", s)
                        continue
                    sid = _get(s, "card_id", "key_id", "offer_id", "id")
                    sname = _get(s, "name", "title", "label", "value")
                    sprice = _get(s, "price", "cost", "amount")
                    line = f"     - {sname}   [id={sid}]"
                    if sprice:
                        line += f"   price={sprice}"
                    print(line)


def main() -> None:
    args = [a.lower() for a in sys.argv[1:]]
    raw = "raw" in args
    args = [a for a in args if a != "raw"]
    kinds = args or ["giftcards", "gamekeys", "topups"]

    fzr = FazerCardClient.from_env()
    try:
        bal = fzr.balance()
        print("Баланс FazerCard:", bal.get("balance"), bal.get("currency"))
    except Exception as e:  # noqa: BLE001
        print("Не удалось получить баланс (проверь FZR_API_KEY):", e)

    getters = {
        "giftcards": fzr.list_giftcards,
        "gamekeys": fzr.list_gamekeys,
        "topups": fzr.list_topups,
    }
    for kind in kinds:
        getter = getters.get(kind)
        if not getter:
            print("Неизвестный раздел:", kind)
            continue
        try:
            data = getter(include_ui=True)
        except Exception as e:  # noqa: BLE001
            print(f"\n{kind}: ошибка запроса: {e}")
            continue
        if raw:
            print(f"\n=== RAW {kind} ===")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:6000])
        else:
            dump(kind, data)


if __name__ == "__main__":
    main()
