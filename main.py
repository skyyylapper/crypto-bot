#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот: крипто-биржа.
- Пользователи получают адреса для пополнения
- Пополнения автоматически собираются на COLLECT_ADDRESS
- Вывод с WITHDRAW_ADDRESS через inline-кнопки
- Комиссия фиксированная в токенах, устанавливается владельцем через /setfee
- Сид-фраза видна пользователю при создании и по /show_seed
- SQLite хранилище

Точка входа: регистрирует хендлеры (импортом пакета handlers) и запускает
фоновые мониторы депозитов + long-polling бота.
"""

import asyncio

from config import bot, dp, logger, OWNER_ID
from monitors import monitor_bep20, monitor_evm, monitor_trc20

# Импорт пакета handlers регистрирует все @router-хендлеры (see handlers/__init__.py)
import handlers  # noqa: F401
# Импортируем text_router отдельно после полной загрузки остальных хендлеров,
# чтобы избежать циклической зависимости.
import handlers.text_router  # noqa: F401


async def main():
    logger.info("Бот запущен. OWNER_ID=%s", OWNER_ID)
    asyncio.create_task(monitor_evm())
    asyncio.create_task(monitor_bep20())
    asyncio.create_task(monitor_trc20())
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
