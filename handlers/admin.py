#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хендлеры: установка и просмотр комиссий (только владелец бота).
"""

from aiogram import F, types
from aiogram.filters import Command

from config import OWNER_ID, router
from db import storage
from keyboards import network_kb, token_kb
from states import user_states


@router.message(Command("setfee"))
async def cmd_setfee(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Только для владельца.")
        return

    user_states[str(OWNER_ID)] = {"step": "setfee_network", "data": {}}
    await message.answer("⚙️ <b>Установка комиссии</b>\n\nВыберите сеть:", reply_markup=network_kb())


@router.callback_query(F.data.startswith("net:"))
async def on_setfee_network(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌")
        return

    user_id = str(OWNER_ID)
    if user_id not in user_states or user_states[user_id]["step"] != "setfee_network":
        await callback.answer("Сессия устарела.")
        return

    network = callback.data.split(":")[1]
    user_states[user_id]["step"] = "setfee_token"
    user_states[user_id]["data"]["network"] = network

    await callback.message.edit_text(f"⚙️ Комиссия\n🌐 Сеть: <b>{network.upper()}</b>\n\nВыберите токен:", reply_markup=token_kb(network))
    await callback.answer()


@router.callback_query(F.data.startswith("tok:"))
async def on_setfee_token(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌")
        return

    user_id = str(OWNER_ID)
    if user_id not in user_states or user_states[user_id]["step"] != "setfee_token":
        await callback.answer("Сессия устарела.")
        return

    parts = callback.data.split(":")
    network, token = parts[1], parts[2]
    user_states[user_id]["step"] = "setfee_amount"
    user_states[user_id]["data"]["token"] = token

    current_fee = storage.get_fee(network, token)
    await callback.message.edit_text(
        f"⚙️ Комиссия\n"
        f"🌐 Сеть: <b>{network.upper()}</b>\n"
        f"🪙 Токен: <b>{token}</b>\n"
        f"📋 Текущая: <b>{current_fee:.6f}</b> {token}\n\n"
        f"Введите новую комиссию (число {token}):"
    )
    await callback.answer()


@router.message(F.text.regexp(r"^\d+(\.\d+)?$"))
async def on_setfee_amount(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    user_id = str(OWNER_ID)
    if user_id not in user_states or user_states[user_id]["step"] != "setfee_amount":
        return

    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число.")
        return

    data = user_states[user_id]["data"]
    network = data["network"]
    token = data["token"]

    storage.set_fee(network, token, amount)
    del user_states[user_id]

    await message.answer(f"✅ Комиссия установлена:\n🌐 {network.upper()} | {token}\n💰 {amount:.6f} {token}")


@router.message(Command("fees"))
async def cmd_fees(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Только для владельца.")
        return

    fees = storage.get_all_fees()
    if not fees:
        await message.answer("📋 Комиссии не установлены (все 0).")
        return

    text = "📋 <b>Комиссии:</b>\n\n"
    for f in fees:
        text += f"🌐 {f['network'].upper()} | {f['token']}: <b>{f['fee']:.6f}</b>\n"

    await message.answer(text)
