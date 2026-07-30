#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Уведомление владельца бота.
"""

from aiogram.enums import ParseMode

from config import bot, logger, OWNER_ID


async def notify_owner(text: str) -> None:
    try:
        await bot.send_message(OWNER_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("Не удалось отправить уведомление: %s", e)
