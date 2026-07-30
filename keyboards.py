#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клавиатуры бота: inline и reply.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from config import OWNER_ID


# =============================================================================
# Inline-клавиатуры (существующие)
# =============================================================================

def network_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="BEP20 (BSC)", callback_data="net:bep20"),
         InlineKeyboardButton(text="TRC20 (TRON)", callback_data="net:trc20")],
        [InlineKeyboardButton(text="EVM (ETH)", callback_data="net:evm")]
    ])


def token_kb(network: str):
    if network == "bep20":
        tokens = ["BNB", "USDT", "USDC"]
    elif network == "trc20":
        tokens = ["TRX", "USDT", "USDC"]
    else:
        tokens = ["ETH", "USDT", "USDC"]
    buttons = [[InlineKeyboardButton(text=t, callback_data=f"tok:{network}:{t}")] for t in tokens]
    buttons.append([InlineKeyboardButton(text="◀ Назад", callback_data="back:net")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_kb(action: str, data: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{action}:{data}"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])


# =============================================================================
# Reply-клавиатуры (новые)
# =============================================================================

def main_menu(user_id: int = None) -> ReplyKeyboardMarkup:
    """
    Главное меню с кнопками.
    Если user_id совпадает с OWNER_ID, добавляются админ-кнопки.
    """
    buttons = [
        [KeyboardButton(text="👛 Создать кошелёк")],
        [KeyboardButton(text="📥 Импорт кошелька")],
        [KeyboardButton(text="👀 Мой кошелёк")],
        [KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="💸 Вывод")],
        [KeyboardButton(text="📜 История")],
        [KeyboardButton(text="🆘 Помощь")],
    ]
    # Если пользователь — владелец, добавляем админские кнопки
    if user_id == OWNER_ID:
        buttons.append([KeyboardButton(text="⚙️ Установить комиссию"),
                        KeyboardButton(text="📋 Комиссии")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def hide_menu() -> ReplyKeyboardMarkup:
    """
    Клавиатура только с кнопкой 'Отмена' – используется во время диалогов.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )
