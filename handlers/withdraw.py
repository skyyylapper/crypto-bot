#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хендлеры вывода средств: выбор сети/токена/суммы/адреса и подтверждение (inline-кнопки).
"""

import json

from aiogram import F, types
from aiogram.filters import Command

from config import logger, router
from db import storage
from keyboards import confirm_kb, network_kb, token_kb, main_menu, hide_menu
from states import user_states
from withdraw_service import withdraw_bep20, withdraw_evm


@router.message(Command("withdraw"))
async def cmd_withdraw(message: types.Message):
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id not in users:
        await message.answer("❌ Сначала создайте кошелёк через меню.",
                             reply_markup=main_menu(message.from_user.id))
        return

    user_states[user_id] = {"step": "select_network", "data": {}}
    await message.answer("💸 <b>Вывод средств</b>\n\nВыберите сеть:",
                         reply_markup=network_kb())
    # Скрываем Reply-клавиатуру, так как пользователь будет работать с inline-кнопками
    # Можно также отправить hide_menu(), но она будет перекрыта inline-клавиатурой.
    # Оставим как есть, но если хотите, можно отправить hide_menu() отдельно:
    # await message.answer("Для отмены нажмите кнопку 'Отмена' внутри диалога.",
    #                      reply_markup=hide_menu())


@router.callback_query(F.data.startswith("net:"))
async def on_network_selected(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    network = callback.data.split(":")[1]

    if user_id not in user_states or user_states[user_id]["step"] != "select_network":
        await callback.answer("Сессия устарела. Начните заново: /withdraw")
        return

    user_states[user_id]["step"] = "select_token"
    user_states[user_id]["data"]["network"] = network
    await callback.message.edit_text(f"💸 Вывод\n🌐 Сеть: <b>{network.upper()}</b>\n\nВыберите токен:",
                                     reply_markup=token_kb(network))
    await callback.answer()


@router.callback_query(F.data.startswith("tok:"))
async def on_token_selected(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    parts = callback.data.split(":")
    network, token = parts[1], parts[2]

    if user_id not in user_states or user_states[user_id]["step"] != "select_token":
        await callback.answer("Сессия устарела. Начните заново: /withdraw")
        return

    balance = storage.get_balance(user_id, network, token)
    if balance <= 0:
        await callback.answer("❌ Баланс пуст!")
        return

    fee = storage.get_fee(network, token)
    available = balance - fee
    if available <= 0:
        await callback.answer(f"❌ Недостаточно средств (комиссия {fee} {token})")
        return

    user_states[user_id]["step"] = "enter_amount"
    user_states[user_id]["data"]["token"] = token
    user_states[user_id]["data"]["balance"] = balance
    user_states[user_id]["data"]["fee"] = fee
    user_states[user_id]["data"]["available"] = available

    await callback.message.edit_text(
        f"💸 Вывод\n"
        f"🌐 Сеть: <b>{network.upper()}</b>\n"
        f"🪙 Токен: <b>{token}</b>\n"
        f"💰 Баланс: <b>{balance:.6f}</b> {token}\n"
        f"📋 Комиссия: <b>{fee:.6f}</b> {token}\n"
        f"✅ Доступно: <b>{available:.6f}</b> {token}\n\n"
        f"Введите сумму для вывода (макс {available:.6f}):"
    )
    # Также можно отправить hide_menu(), чтобы скрыть Reply-клавиатуру
    await callback.message.answer("Введите сумму числом:", reply_markup=hide_menu())
    await callback.answer()


@router.message(F.text.regexp(r"^\d+(\.\d+)?$"))
async def on_amount_entered(message: types.Message):
    user_id = str(message.from_user.id)

    if user_id not in user_states or user_states[user_id]["step"] != "enter_amount":
        return

    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число.", reply_markup=hide_menu())
        return

    data = user_states[user_id]["data"]
    available = data["available"]
    network = data["network"]
    token = data["token"]
    fee = data["fee"]

    if amount <= 0 or amount > available:
        await message.answer(f"❌ Некорректная сумма. Доступно: {available:.6f} {token}",
                             reply_markup=hide_menu())
        return

    user_states[user_id]["step"] = "enter_address"
    user_states[user_id]["data"]["amount"] = amount

    await message.answer(
        f"💸 Вывод\n"
        f"🌐 Сеть: <b>{network.upper()}</b>\n"
        f"🪙 Токен: <b>{token}</b>\n"
        f"💰 Сумма: <b>{amount:.6f}</b> {token}\n"
        f"📋 Комиссия: <b>{fee:.6f}</b> {token}\n"
        f"📤 К получению: <b>{amount:.6f}</b> {token}\n\n"
        f"Введите адрес для вывода:",
        reply_markup=hide_menu()
    )


async def handle_enter_address(message: types.Message):
    user_id = str(message.from_user.id)

    if user_id not in user_states or user_states[user_id]["step"] != "enter_address":
        return

    address = message.text.strip()
    data = user_states[user_id]["data"]
    network = data["network"]
    token = data["token"]
    amount = data["amount"]
    fee = data["fee"]

    if network == "trc20" and not address.startswith("T"):
        await message.answer("❌ TRON адрес должен начинаться с T", reply_markup=hide_menu())
        return
    elif network != "trc20" and not address.startswith("0x"):
        await message.answer("❌ EVM адрес должен начинаться с 0x", reply_markup=hide_menu())
        return

    user_states[user_id]["step"] = "confirm"
    user_states[user_id]["data"]["address"] = address

    confirm_data = json.dumps({"network": network, "token": token, "amount": amount, "address": address, "fee": fee})
    await message.answer(
        f"💸 <b>Подтвердите вывод:</b>\n\n"
        f"🌐 Сеть: <b>{network.upper()}</b>\n"
        f"🪙 Токен: <b>{token}</b>\n"
        f"💰 Сумма: <b>{amount:.6f}</b> {token}\n"
        f"📋 Комиссия: <b>{fee:.6f}</b> {token}\n"
        f"📍 Адрес: <code>{address}</code>\n\n"
        f"❗ Проверьте адрес! Операция необратима.",
        reply_markup=confirm_kb("withdraw", confirm_data)
    )
    # Скрываем Reply-клавиатуру (она не нужна, т.к. есть inline-кнопки)
    await message.answer("Подтвердите или отмените операцию.", reply_markup=hide_menu())


@router.callback_query(F.data.startswith("confirm:withdraw:"))
async def on_withdraw_confirmed(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)

    if user_id not in user_states or user_states[user_id]["step"] != "confirm":
        await callback.answer("Сессия устарела.")
        return

    data = json.loads(callback.data.split(":", 2)[2])
    network = data["network"]
    token = data["token"]
    amount = data["amount"]
    address = data["address"]
    fee = data["fee"]

    if network == "bep20":
        success, result = await withdraw_bep20(address, token, amount)
    elif network == "evm":
        success, result = await withdraw_evm(address, token, amount)
    else:
        await callback.answer("❌ TRON вывод пока не реализован")
        return

    if success:
        storage.add_withdrawal(user_id, network, token, amount, fee, address, result)
        await callback.message.edit_text(
            f"✅ <b>Вывод отправлен!</b>\n\n"
            f"🌐 {network.upper()} | {token}\n"
            f"💰 {amount:.6f} {token}\n"
            f"📍 <code>{address}</code>\n"
            f"🔗 Хеш: <code>{result}</code>"
        )
        logger.info("Вывод %s %s на %s", amount, token, address)
    else:
        await callback.message.edit_text(f"❌ <b>Ошибка вывода:</b>\n{result}")

    del user_states[user_id]
    await callback.answer()
    # Возвращаем главное меню
    await callback.message.answer("Главное меню:", reply_markup=main_menu(int(user_id)))


@router.callback_query(F.data == "cancel")
async def on_cancel(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id in user_states:
        del user_states[user_id]
    await callback.message.edit_text("❌ Операция отменена.")
    await callback.answer()
    # Возвращаем главное меню
    await callback.message.answer("Главное меню:", reply_markup=main_menu(int(user_id)))


@router.callback_query(F.data == "back:net")
async def on_back_network(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id in user_states:
        user_states[user_id]["step"] = "select_network"
        user_states[user_id]["data"] = {}
    await callback.message.edit_text("💸 <b>Вывод средств</b>\n\nВыберите сеть:",
                                     reply_markup=network_kb())
    await callback.answer()
