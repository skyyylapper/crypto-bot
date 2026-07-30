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
from states import user_states

from .withdraw import handle_enter_address
from .wallet import handle_import_seed

# Шаг диалога -> обработчик
_STEP_HANDLERS = {
    "enter_address": handle_enter_address,
    "import_seed": handle_import_seed,
}


@router.message(F.text)
async def on_free_text(message: types.Message):
    user_id = str(message.from_user.id)
    state = user_states.get(user_id)
    if not state:
        return

    handler = _STEP_HANDLERS.get(state["step"])
    if handler is None:
        return

    await handler(message)
