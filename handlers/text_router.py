#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый диспетчер "свободного" текста (без команд и без чисто цифровых значений).

Важно: это единственный catch-all-обработчик F.text во всём боте. Несколько
таких обработчиков в разных модулях конфликтовали бы между собой — в aiogram
после срабатывания фильтра первого зарегистрированного хендлера сообщение
считается обработанным и до остальных не доходит. Поэтому вместо отдельных
@router.message(F.text) в handlers/wallet.py и handlers/withdraw.py там
объявлены обычные async-функции (handle_import_seed, handle_enter_address),
а маршрутизация по шагу диалога (user_states[user_id]["step"]) происходит
здесь, в одном месте.

Этот модуль импортируется последним в handlers/__init__.py, чтобы более
специфичные фильтры (команды, callback_query, регэксп на числа) успели
сработать раньше.
"""

from aiogram import F, types

from config import router
from keyboards import main_menu, hide_menu
from states import user_states

from .withdraw import handle_enter_address
from .wallet import handle_import_seed
from .wallet import cmd_create_wallet, cmd_my_wallet, cmd_balance, cmd_history, cmd_help
from .withdraw import cmd_withdraw
from .admin import cmd_setfee, cmd_fees


# Шаг диалога -> обработчик
_STEP_HANDLERS = {
    "enter_address": handle_enter_address,
    "import_seed": handle_import_seed,
}


# =============================================================================
# Обработчики кнопок меню (они имеют более высокий приоритет, чем catch-all)
# =============================================================================

@router.message(F.text == "👛 Создать кошелёк")
async def menu_create_wallet(message: types.Message):
    await cmd_create_wallet(message)


@router.message(F.text == "📥 Импорт кошелька")
async def menu_import_wallet(message: types.Message):
    # Передаём управление команде импорта (она сама покажет hide_menu)
    await cmd_import_wallet(message)  # нужно импортировать cmd_import_wallet
    # Чтобы избежать циклического импорта, импортируем внутри функции
    from .wallet import cmd_import_wallet
    await cmd_import_wallet(message)


@router.message(F.text == "👀 Мой кошелёк")
async def menu_my_wallet(message: types.Message):
    await cmd_my_wallet(message)


@router.message(F.text == "💰 Баланс")
async def menu_balance(message: types.Message):
    await cmd_balance(message)


@router.message(F.text == "💸 Вывод")
async def menu_withdraw(message: types.Message):
    await cmd_withdraw(message)


@router.message(F.text == "📜 История")
async def menu_history(message: types.Message):
    await cmd_history(message)


@router.message(F.text == "🆘 Помощь")
async def menu_help(message: types.Message):
    await cmd_help(message)


# Админские кнопки (проверяем владельца внутри команд)
@router.message(F.text == "⚙️ Установить комиссию")
async def menu_setfee(message: types.Message):
    await cmd_setfee(message)


@router.message(F.text == "📋 Комиссии")
async def menu_fees(message: types.Message):
    await cmd_fees(message)


@router.message(F.text == "❌ Отмена")
async def menu_cancel(message: types.Message):
    user_id = str(message.from_user.id)
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("❌ Операция отменена.", reply_markup=main_menu(message.from_user.id))


# =============================================================================
# Catch-all для текстовых сообщений (ввод в диалогах)
# =============================================================================

@router.message(F.text)
async def on_free_text(message: types.Message):
    user_id = str(message.from_user.id)
    state = user_states.get(user_id)
    if not state:
        # Если нет состояния, но пришёл текст — вероятно, пользователь что-то ввел вне диалога
        await message.answer("Используйте кнопки меню.", reply_markup=main_menu(message.from_user.id))
        return

    handler = _STEP_HANDLERS.get(state["step"])
    if handler is None:
        # Если шаг не распознан, сбрасываем состояние и показываем меню
        del user_states[user_id]
        await message.answer("Неизвестное состояние. Начните заново.", reply_markup=main_menu(message.from_user.id))
        return

    await handler(message)
