"""Тесты кастом-эмодзи и цветов на кнопках."""

from decimal import Decimal

from bot import keyboards as kb
from bot.keyboards import EMOJI


def _flat(markup):
    return [btn for row in markup.inline_keyboard for btn in row]


def test_main_menu_one_row_with_icons_and_colors():
    markup = kb.main_menu_kb()
    # Каталог и Мой профиль — в одном ряду
    assert len(markup.inline_keyboard) == 1
    catalog, profile = markup.inline_keyboard[0]
    assert catalog.icon_custom_emoji_id == EMOJI["catalog"]
    assert catalog.style == "primary"
    assert profile.icon_custom_emoji_id == EMOJI["profile"]
    assert profile.style == "success"


def test_profile_icons_and_orders_blue():
    btns = {b.callback_data: b for b in _flat(kb.profile_kb())}
    assert btns["balance"].icon_custom_emoji_id == EMOJI["balance"]
    assert btns["balance"].style == "danger"
    assert btns["myorders"].icon_custom_emoji_id == EMOJI["orders"]
    assert btns["myorders"].style == "primary"  # «Мои заказы» — синий
    assert btns["mytopups"].icon_custom_emoji_id == EMOJI["topups"]
    assert btns["menu"].icon_custom_emoji_id == EMOJI["back"]


def test_topup_button_icon():
    btns = {b.callback_data: b for b in _flat(kb.balance_kb())}
    assert btns["topup"].icon_custom_emoji_id == EMOJI["topup"]
    assert btns["topup"].style == "success"


def test_back_buttons_use_back_emoji():
    for markup in (kb.back_profile_kb(), kb.topup_cancel_kb()):
        btn = _flat(markup)[0]
        assert btn.icon_custom_emoji_id == EMOJI["back"]
