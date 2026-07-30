#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inline-клавиатуры бота.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
