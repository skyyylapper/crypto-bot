#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот для мультисетевых криптокошельков.
Поддерживает BEP20 (BSC), TRC20 (TRON) и EVM (Ethereum).
Мониторит нативные монеты (BNB, TRX, ETH) и токены USDT/USDC.
Хранилище: SQLite (файл data.db).
Адаптирован для хостинга Bothost.ru (базовая подписка).
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from bip32 import BIP32
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from eth_account import Account
from mnemonic import Mnemonic
from tronpy.keys import PrivateKey, to_base58check_address
from web3 import Web3

# =============================================================================
# 1. КОНФИГУРАЦИЯ
# =============================================================================

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
FERNET_KEY: str = os.getenv("FERNET_KEY", "").strip().strip('"').strip("'").strip()
MONITOR_INTERVAL: int = int(os.getenv("MONITOR_INTERVAL", "30"))
BSC_RPC_URL: str = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org")
TRON_RPC_URL: str = os.getenv("TRON_RPC_URL", "https://api.trongrid.io")
ETH_RPC_URL: str = os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")

# Адреса контрактов токенов
ETH_USDT: str = os.getenv("ETH_USDT", "0xdAC17F958D2ee523a2206206994597C13D831ec7")
ETH_USDC: str = os.getenv("ETH_USDC", "0xA0b86a33E6441E6C7D3D4B4B0cA0e1c2d3e4f5a6")
BSC_USDT: str = os.getenv("BSC_USDT", "0x55d398326f99059fF775485246999027B3197955")
BSC_USDC: str = os.getenv("BSC_USDC", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d")
TRON_USDT: str = os.getenv("TRON_USDT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
TRON_USDC: str = os.getenv("TRON_USDC", "TEkxiTehnzSmSe2XUrbz6D9mD7H3P1Z1Z1")

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")
if not OWNER_ID:
    raise ValueError("OWNER_ID не задан")
if not FERNET_KEY:
    raise ValueError("FERNET_KEY не задан")

# Инициализация шифрования
fernet = Fernet(FERNET_KEY.encode())

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Aiogram
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Web3 провайдеры
w3_eth = Web3(Web3.HTTPProvider(ETH_RPC_URL))
w3_bsc = Web3(Web3.HTTPProvider(BSC_RPC_URL))

# ABI для ERC20 Transfer события
ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

DB_FILE = "data.db"


# =============================================================================
# 2. SQLITE ХРАНИЛИЩЕ
# =============================================================================

class SQLiteStorage:
    """Хранилище данных в SQLite (файл data.db)."""

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Создаёт таблицы если их нет."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                mnemonic_encrypted TEXT,
                bep20_address TEXT,
                trc20_address TEXT,
                evm_address TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS network_state (
                network TEXT PRIMARY KEY,
                last_block INTEGER DEFAULT 0,
                last_tx_hash TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS processed_txs (
                tx_hash TEXT PRIMARY KEY,
                network TEXT,
                token TEXT,
                timestamp TEXT
            )
        ''')

        # Инициализация начальных значений
        for net in ["evm", "bep20", "trc20"]:
            c.execute("INSERT OR IGNORE INTO network_state (network, last_block, last_tx_hash) VALUES (?, 0, '')", (net,))

        conn.commit()
        conn.close()

    def get_users(self) -> Dict[str, Dict[str, str]]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT user_id, username, mnemonic_encrypted, bep20_address, trc20_address, evm_address FROM users")
        rows = c.fetchall()
        conn.close()
        return {str(row[0]): {
            "username": row[1] or "",
            "mnemonic_encrypted": row[2] or "",
            "bep20_address": row[3] or "",
            "trc20_address": row[4] or "",
            "evm_address": row[5] or "",
        } for row in rows}

    def add_user(self, user_id: str, username: str, mnemonic_encrypted: str,
                 bep20: str, trc20: str, evm: str) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            INSERT INTO users (user_id, username, mnemonic_encrypted, bep20_address, trc20_address, evm_address)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, mnemonic_encrypted, bep20, trc20, evm))
        conn.commit()
        conn.close()

    def get_network_state(self) -> Dict[str, Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT network, last_block, last_tx_hash FROM network_state")
        rows = c.fetchall()
        conn.close()
        return {row[0]: {
            "last_block": row[1] or 0,
            "last_tx_hash": row[2] or None,
        } for row in rows}

    def update_network_state(self, network: str, last_block: int, last_tx_hash: Optional[str]) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            UPDATE network_state SET last_block = ?, last_tx_hash = ? WHERE network = ?
        ''', (last_block, last_tx_hash or "", network))
        conn.commit()
        conn.close()

    def is_tx_processed(self, tx_hash: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT 1 FROM processed_txs WHERE tx_hash = ?", (tx_hash,))
        result = c.fetchone() is not None
        conn.close()
        return result

    def mark_tx_processed(self, tx_hash: str, network: str, token: str) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            INSERT OR IGNORE INTO processed_txs (tx_hash, network, token, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (tx_hash, network, token, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()


storage = SQLiteStorage()


# =============================================================================
# 3. ГЕНЕРАЦИЯ КОШЕЛЬКА
# =============================================================================

def generate_wallet() -> Dict[str, str]:
    mnemo = Mnemonic("english")
    mnemonic = mnemo.generate(strength=128)
    seed = mnemo.to_seed(mnemonic)
    bip32 = BIP32.from_seed(seed)

    evm_privkey = bip32.get_privkey_from_path("m/44'/60'/0'/0/0")
    evm_account = Account.from_key(evm_privkey)
    evm_address = evm_account.address

    tron_privkey = bip32.get_privkey_from_path("m/44'/195'/0'/0/0")
    tron_pk = PrivateKey(tron_privkey)
    trc20_address = tron_pk.public_key.to_base58check_address()

    return {
        "mnemonic": mnemonic,
        "evm_address": evm_address,
        "bep20_address": evm_address,
        "trc20_address": trc20_address,
    }


# =============================================================================
# 4. УВЕДОМЛЕНИЯ
# =============================================================================

async def notify_owner(text: str) -> None:
    try:
        await bot.send_message(OWNER_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("Не удалось отправить уведомление владельцу: %s", e)


# =============================================================================
# 5. МОНИТОРИНГ EVM (Ethereum)
# =============================================================================

async def monitor_evm() -> None:
    logger.info("Запущен мониторинг EVM (ETH + USDT + USDC)")
    while True:
        try:
            if not w3_eth.is_connected():
                logger.warning("EVM RPC недоступен")
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            users = storage.get_users()
            if not users:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            state = storage.get_network_state()
            evm_state = state.get("evm", {"last_block": 0, "last_tx_hash": None})
            last_block = evm_state.get("last_block", 0)
            current_block = w3_eth.eth.block_number

            if last_block == 0:
                storage.update_network_state("evm", current_block, None)
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            from_block = last_block + 1
            to_block = min(current_block, from_block + 30)

            user_addrs = {uid: info["evm_address"].lower() for uid, info in users.items()}
            addr_to_uid = {v: k for k, v in user_addrs.items()}

            # Нативный ETH
            for block_num in range(from_block, to_block + 1):
                try:
                    block = w3_eth.eth.get_block(block_num, full_transactions=True)
                except Exception as e:
                    logger.warning("EVM блок %s: %s", block_num, e)
                    continue

                for tx in block.transactions:
                    to_addr = (tx.get("to") or "").lower()
                    value = tx.get("value", 0)
                    tx_hash = tx.get("hash", b"").hex()

                    if to_addr in addr_to_uid and value > 0:
                        if storage.is_tx_processed(tx_hash):
                            continue
                        uid = addr_to_uid[to_addr]
                        username = users[uid].get("username", "")
                        eth_val = w3_eth.from_wei(value, "ether")
                        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                        msg = (
                            f"🔔 <b>Пополнение</b>\n"
                            f"🌐 Сеть: <b>EVM (Ethereum)</b>\n"
                            f"💰 Сумма: <b>{eth_val:.6f} ETH</b>\n"
                            f"🔗 Хеш: <code>{tx_hash}</code>\n"
                            f"👤 Пользователь: {user_link}"
                        )
                        await notify_owner(msg)
                        storage.mark_tx_processed(tx_hash, "evm", "ETH")
                        logger.info("EVM ETH пополнение: %s на %s", eth_val, to_addr)

            # USDT
            _monitor_evm_token_sync(
                w3_eth, "evm", "USDT", ETH_USDT, from_block, to_block, users, addr_to_uid, 6
            )
            # USDC
            _monitor_evm_token_sync(
                w3_eth, "evm", "USDC", ETH_USDC, from_block, to_block, users, addr_to_uid, 6
            )

            storage.update_network_state("evm", to_block, None)

        except Exception as e:
            logger.error("Ошибка мониторинга EVM: %s", e)

        await asyncio.sleep(MONITOR_INTERVAL)


def _monitor_evm_token_sync(
    w3: Web3, network: str, token_name: str, contract_addr: str,
    from_block: int, to_block: int, users: Dict, addr_to_uid: Dict, decimals: int
) -> None:
    if not contract_addr or contract_addr == "0x":
        return

    try:
        logs = w3.eth.get_logs({
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": Web3.to_checksum_address(contract_addr),
            "topics": [ERC20_TRANSFER_TOPIC],
        })

        for log in logs:
            tx_hash = log["transactionHash"].hex()
            if storage.is_tx_processed(tx_hash + "_" + token_name):
                continue

            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            to_addr = "0x" + topics[2].hex()[-40:]
            to_addr_lower = to_addr.lower()

            if to_addr_lower in addr_to_uid:
                data = log.get("data", "0x")
                if data == "0x":
                    continue
                amount = int(data, 16)
                if amount <= 0:
                    continue

                uid = addr_to_uid[to_addr_lower]
                username = users[uid].get("username", "")
                token_val = amount / (10 ** decimals)
                user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                msg = (
                    f"🔔 <b>Пополнение токена</b>\n"
                    f"🌐 Сеть: <b>{network.upper()}</b>\n"
                    f"🪙 Токен: <b>{token_name}</b>\n"
                    f"💰 Сумма: <b>{token_val:.6f} {token_name}</b>\n"
                    f"🔗 Хеш: <code>{tx_hash}</code>\n"
                    f"👤 Пользователь: {user_link}"
                )
                asyncio.create_task(notify_owner(msg))
                storage.mark_tx_processed(tx_hash + "_" + token_name, network, token_name)
                logger.info("%s %s пополнение: %s на %s", network, token_name, token_val, to_addr)

    except Exception as e:
        logger.error("Ошибка мониторинга %s %s: %s", network, token_name, e)


# =============================================================================
# 6. МОНИТОРИНГ BEP20 (BSC)
# =============================================================================

async def monitor_bep20() -> None:
    logger.info("Запущен мониторинг BEP20 (BNB + USDT + USDC)")
    while True:
        try:
            if not w3_bsc.is_connected():
                logger.warning("BSC RPC недоступен")
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            users = storage.get_users()
            if not users:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            state = storage.get_network_state()
            bsc_state = state.get("bep20", {"last_block": 0, "last_tx_hash": None})
            last_block = bsc_state.get("last_block", 0)
            current_block = w3_bsc.eth.block_number

            if last_block == 0:
                storage.update_network_state("bep20", current_block, None)
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            from_block = last_block + 1
            to_block = min(current_block, from_block + 30)

            user_addrs = {uid: info["bep20_address"].lower() for uid, info in users.items()}
            addr_to_uid = {v: k for k, v in user_addrs.items()}

            # Нативный BNB
            for block_num in range(from_block, to_block + 1):
                try:
                    block = w3_bsc.eth.get_block(block_num, full_transactions=True)
                except Exception as e:
                    logger.warning("BSC блок %s: %s", block_num, e)
                    continue

                for tx in block.transactions:
                    to_addr = (tx.get("to") or "").lower()
                    value = tx.get("value", 0)
                    tx_hash = tx.get("hash", b"").hex()

                    if to_addr in addr_to_uid and value > 0:
                        if storage.is_tx_processed(tx_hash):
                            continue
                        uid = addr_to_uid[to_addr]
                        username = users[uid].get("username", "")
                        bnb_val = w3_bsc.from_wei(value, "ether")
                        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                        msg = (
                            f"🔔 <b>Пополнение</b>\n"
                            f"🌐 Сеть: <b>BEP20 (BSC)</b>\n"
                            f"💰 Сумма: <b>{bnb_val:.6f} BNB</b>\n"
                            f"🔗 Хеш: <code>{tx_hash}</code>\n"
                            f"👤 Пользователь: {user_link}"
                        )
                        await notify_owner(msg)
                        storage.mark_tx_processed(tx_hash, "bep20", "BNB")
                        logger.info("BEP20 BNB пополнение: %s на %s", bnb_val, to_addr)

            # USDT BEP20
            _monitor_evm_token_sync(
                w3_bsc, "bep20", "USDT", BSC_USDT, from_block, to_block, users, addr_to_uid, 18
            )
            # USDC BEP20
            _monitor_evm_token_sync(
                w3_bsc, "bep20", "USDC", BSC_USDC, from_block, to_block, users, addr_to_uid, 18
            )

            storage.update_network_state("bep20", to_block, None)

        except Exception as e:
            logger.error("Ошибка мониторинга BEP20: %s", e)

        await asyncio.sleep(MONITOR_INTERVAL)


# =============================================================================
# 7. МОНИТОРИНГ TRC20 (TRON)
# =============================================================================

async def monitor_trc20() -> None:
    logger.info("Запущен мониторинг TRC20 (TRX + USDT + USDC)")
    while True:
        try:
            users = storage.get_users()
            if not users:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            for uid, info in users.items():
                address = info.get("trc20_address", "")
                if not address:
                    continue

                await _monitor_tron_native(address, uid, info.get("username", ""))
                await _monitor_tron_token(
                    address, uid, info.get("username", ""), TRON_USDT, "USDT", 6
                )
                await _monitor_tron_token(
                    address, uid, info.get("username", ""), TRON_USDC, "USDC", 6
                )

        except Exception as e:
            logger.error("Ошибка мониторинга TRC20: %s", e)

        await asyncio.sleep(MONITOR_INTERVAL)


async def _monitor_tron_native(address: str, uid: str, username: str) -> None:
    url = f"{TRON_RPC_URL}/v1/accounts/{address}/transactions"
    params = {"only_to": "true", "limit": "20", "order_by": "block_timestamp,desc"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=30) as resp:
            if resp.status != 200:
                return
            result = await resp.json()

    txs = result.get("data", [])
    if not txs:
        return

    for tx in reversed(txs):
        tx_id = tx.get("txID", "")
        if not tx_id or storage.is_tx_processed(tx_id):
            continue

        raw_data = tx.get("raw_data", {})
        contracts = raw_data.get("contract", [])
        if not contracts:
            continue

        contract = contracts[0]
        if contract.get("type") != "TransferContract":
            continue

        value = contract.get("parameter", {}).get("value", {})
        amount = value.get("amount", 0)
        to_address_hex = value.get("to_address", "")

        if amount <= 0 or not to_address_hex:
            continue

        try:
            to_address_b58 = to_base58check_address(to_address_hex)
        except Exception:
            continue

        if to_address_b58 != address:
            continue

        trx_value = amount / 1_000_000
        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
        msg = (
            f"🔔 <b>Пополнение</b>\n"
            f"🌐 Сеть: <b>TRC20 (TRON)</b>\n"
            f"💰 Сумма: <b>{trx_value:.6f} TRX</b>\n"
            f"🔗 Хеш: <code>{tx_id}</code>\n"
            f"👤 Пользователь: {user_link}"
        )
        await notify_owner(msg)
        storage.mark_tx_processed(tx_id, "trc20", "TRX")
        logger.info("TRC20 TRX пополнение: %s на %s", trx_value, address)


async def _monitor_tron_token(address: str, uid: str, username: str,
                               contract_addr: str, token_name: str, decimals: int) -> None:
    if not contract_addr:
        return

    url = f"{TRON_RPC_URL}/v1/accounts/{address}/transactions/trc20"
    params = {
        "contract_address": contract_addr,
        "only_to": "true",
        "limit": "20",
        "order_by": "block_timestamp,desc",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=30) as resp:
            if resp.status != 200:
                return
            result = await resp.json()

    txs = result.get("data", [])
    if not txs:
        return

    for tx in reversed(txs):
        tx_id = tx.get("transaction_id", "")
        if not tx_id or storage.is_tx_processed(tx_id + "_" + token_name):
            continue

        to_addr = tx.get("to", "")
        amount_str = tx.get("value", "0")

        if to_addr != address:
            continue

        try:
            amount = int(amount_str)
        except (ValueError, TypeError):
            continue

        if amount <= 0:
            continue

        token_val = amount / (10 ** decimals)
        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
        msg = (
            f"🔔 <b>Пополнение токена</b>\n"
            f"🌐 Сеть: <b>TRC20 (TRON)</b>\n"
            f"🪙 Токен: <b>{token_name}</b>\n"
            f"💰 Сумма: <b>{token_val:.6f} {token_name}</b>\n"
            f"🔗 Хеш: <code>{tx_id}</code>\n"
            f"👤 Пользователь: {user_link}"
        )
        await notify_owner(msg)
        storage.mark_tx_processed(tx_id + "_" + token_name, "trc20", token_name)
        logger.info("TRC20 %s пополнение: %s на %s", token_name, token_val, address)


# =============================================================================
# 8. ОБРАБОТЧИКИ КОМАНД
# =============================================================================

@router.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    await message.answer(
        "👋 Привет! Я бот для мультисетевых криптокошельков.\n\n"
        "📌 <b>Доступные команды:</b>\n"
        "/create_wallet — создать кошелёк (BEP20, TRC20, EVM)\n"
        "/my_wallet — показать адреса моего кошелька\n"
        "/help — справка"
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(
        "🤖 <b>Справка по боту</b>\n\n"
        "Бот позволяет создать единый кошелёк для трёх сетей:\n"
        "• <b>BEP20</b> — BNB Smart Chain (BNB, USDT, USDC)\n"
        "• <b>TRC20</b> — TRON (TRX, USDT, USDC)\n"
        "• <b>EVM</b> — Ethereum Mainnet (ETH, USDT, USDC)\n\n"
        "Команды:\n"
        "/create_wallet — создать кошелёк (один на пользователя)\n"
        "/my_wallet — показать ваши адреса"
    )


@router.message(Command("create_wallet"))
async def cmd_create_wallet(message: types.Message) -> None:
    user_id = str(message.from_user.id)
    username = message.from_user.username or ""
    users = storage.get_users()

    if user_id in users:
        info = users[user_id]
        await message.answer(
            "⚠️ У вас уже есть кошелёк!\n\n"
            f"<b>Ваши адреса:</b>\n"
            f"🔷 <b>BEP20 (BSC):</b> <code>{info['bep20_address']}</code>\n"
            f"🔷 <b>TRC20 (TRON):</b> <code>{info['trc20_address']}</code>\n"
            f"🔷 <b>EVM (ETH):</b> <code>{info['evm_address']}</code>"
        )
        return

    wallet = generate_wallet()
    encrypted_mnemonic = fernet.encrypt(wallet["mnemonic"].encode()).decode()

    storage.add_user(
        user_id, username, encrypted_mnemonic,
        wallet["bep20_address"], wallet["trc20_address"], wallet["evm_address"]
    )

    await message.answer(
        "✅ <b>Кошелёк успешно создан!</b>\n\n"
        "🔐 <b>Сид-фраза отправлена владельцу бота.</b>\n"
        "Сохраните её в надёжном месте!\n\n"
        f"🔷 <b>BEP20 (BSC):</b>\n<code>{wallet['bep20_address']}</code>\n\n"
        f"🔷 <b>TRC20 (TRON):</b>\n<code>{wallet['trc20_address']}</code>\n\n"
        f"🔷 <b>EVM (ETH):</b>\n<code>{wallet['evm_address']}</code>"
    )

    user_link = f'<a href="tg://user?id={user_id}">{username or user_id}</a>'
    owner_msg = (
        f"🆕 <b>Создан новый кошелёк</b>\n\n"
        f"👤 Пользователь: {user_link} (ID: <code>{user_id}</code>)\n"
        f"🔑 Сид-фраза:\n<code>{wallet['mnemonic']}</code>\n\n"
        f"🔷 BEP20: <code>{wallet['bep20_address']}</code>\n"
        f"🔷 TRC20: <code>{wallet['trc20_address']}</code>\n"
        f"🔷 EVM: <code>{wallet['evm_address']}</code>"
    )
    await notify_owner(owner_msg)
    logger.info("Пользователь %s создал кошелёк", user_id)

@router.message(Command("show_seed"))
async def cmd_show_seed(message: types.Message) -> None:
    """Показывает сид-фразу пользователю (только владельцу кошелька)."""
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id not in users:
        await message.answer("❌ У вас ещё нет кошелька.")
        return

    info = users[user_id]
    encrypted_mnemonic = info.get("mnemonic_encrypted", "")
    
    if not encrypted_mnemonic:
        await message.answer("❌ Сид-фраза не найден.")
        return

    try:
        mnemonic = fernet.decrypt(encrypted_mnemonic.encode()).decode()
    except Exception as e:
        logger.error("Ошибка расшифровки сид-фразы для %s: %s", user_id, e)
        await message.answer("❌ Ошибка при расшифровке сид-фразы. Обратитесь к владельцу бота.")
        return

    # Отправляем сид-фразу с предупреждением
    await message.answer(
        "⚠️ <b>ВНИМАНИЕ!</b>\n\n"
        "🔐 <b>Ваша сид-фраза:</b>\n"
        f"<code>{mnemonic}</code>\n\n"
        "❗ <b>Никому не показывайте эту фразу!</b>\n"
        "❗ Сохраните её в надёжном месте.\n"
        "❗ При утере фразы восстановить кошелёк будет невозможно.\n\n"
        "🗑 Это сообщение рекомендуется удалить после сохранения.",
        parse_mode=ParseMode.HTML
    )
    
    # Уведомляем владельца, что пользователь запросил сид-фразу
    username = info.get("username", "")
    user_link = f'<a href="tg://user?id={user_id}">{username or user_id}</a>'
    await notify_owner(
        f"👁 Пользователь {user_link} (ID: <code>{user_id}</code>) запросил свою сид-фразу."
    )
    logger.info("Пользователь %s запросил сид-фразу", user_id)

@router.message(Command("my_wallet"))
async def cmd_my_wallet(message: types.Message) -> None:
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id not in users:
        await message.answer(
            "❌ У вас ещё нет кошелька.\n"
            "Создайте его командой /create_wallet"
        )
        return

    info = users[user_id]
    await message.answer(
        "👛 <b>Ваши адреса:</b>\n\n"
        f"🔷 <b>BEP20 (BSC):</b>\n<code>{info['bep20_address']}</code>\n\n"
        f"🔷 <b>TRC20 (TRON):</b>\n<code>{info['trc20_address']}</code>\n\n"
        f"🔷 <b>EVM (ETH):</b>\n<code>{info['evm_address']}</code>"
    )


# =============================================================================
# 9. ЗАПУСК
# =============================================================================

async def main() -> None:
    logger.info("Бот запущен. OWNER_ID=%s", OWNER_ID)
    asyncio.create_task(monitor_evm())
    asyncio.create_task(monitor_bep20())
    asyncio.create_task(monitor_trc20())
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
