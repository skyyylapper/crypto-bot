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
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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

# RPC
BSC_RPC_URL: str = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org")
TRON_RPC_URL: str = os.getenv("TRON_RPC_URL", "https://api.trongrid.io")
ETH_RPC_URL: str = os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")

# Адреса для СБОРА депозитов
COLLECT_BEP20: str = os.getenv("COLLECT_BEP20", "")
COLLECT_TRC20: str = os.getenv("COLLECT_TRC20", "")
COLLECT_EVM: str = os.getenv("COLLECT_EVM", "")

# Адреса и ключ для ВЫВОДА
WITHDRAW_BEP20: str = os.getenv("WITHDRAW_BEP20", "")
WITHDRAW_TRC20: str = os.getenv("WITHDRAW_TRC20", "")
WITHDRAW_EVM: str = os.getenv("WITHDRAW_EVM", "")
WITHDRAW_PRIVATE_KEY: str = os.getenv("WITHDRAW_PRIVATE_KEY", "")

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
if not WITHDRAW_PRIVATE_KEY:
    raise ValueError("WITHDRAW_PRIVATE_KEY не задан")

fernet = Fernet(FERNET_KEY.encode())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

w3_eth = Web3(Web3.HTTPProvider(ETH_RPC_URL))
w3_bsc = Web3(Web3.HTTPProvider(BSC_RPC_URL))

# POA middleware для BSC
try:
    from web3.middleware import ExtraDataToPOAMiddleware
    w3_bsc.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
except ImportError:
    try:
        from web3.middleware.geth_poa import geth_poa_middleware
        w3_bsc.middleware_onion.inject(geth_poa_middleware, layer=0)
    except ImportError:
        pass

# ERC20 ABI
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

DB_FILE = "data.db"

# =============================================================================
# 2. SQLITE ХРАНИЛИЩЕ
# =============================================================================

class SQLiteStorage:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                mnemonic_encrypted TEXT,
                bep20_address TEXT,
                trc20_address TEXT,
                evm_address TEXT,
                created_at TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                network TEXT,
                token TEXT,
                amount REAL,
                tx_hash TEXT,
                collect_tx_hash TEXT,
                status TEXT,
                created_at TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                network TEXT,
                token TEXT,
                amount REAL,
                fee REAL,
                to_address TEXT,
                tx_hash TEXT,
                status TEXT,
                created_at TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS balances (
                user_id TEXT,
                network TEXT,
                token TEXT,
                deposited REAL DEFAULT 0,
                withdrawn REAL DEFAULT 0,
                PRIMARY KEY (user_id, network, token)
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS fees (
                network TEXT,
                token TEXT,
                fee_amount REAL DEFAULT 0,
                PRIMARY KEY (network, token)
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

        for net in ["evm", "bep20", "trc20"]:
            c.execute("INSERT OR IGNORE INTO network_state (network, last_block, last_tx_hash) VALUES (?, 0, '')", (net,))

        # Дефолтные комиссии 0
        for net in ["bep20", "trc20", "evm"]:
            for tok in ["BNB", "USDT", "USDC", "ETH", "TRX"]:
                c.execute("INSERT OR IGNORE INTO fees (network, token, fee_amount) VALUES (?, ?, 0)", (net, tok))

        conn.commit()
        conn.close()

    def get_users(self) -> Dict[str, Dict[str, Any]]:
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
            INSERT INTO users (user_id, username, mnemonic_encrypted, bep20_address, trc20_address, evm_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, mnemonic_encrypted, bep20, trc20, evm, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()

    def get_network_state(self) -> Dict[str, Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT network, last_block, last_tx_hash FROM network_state")
        rows = c.fetchall()
        conn.close()
        return {row[0]: {"last_block": row[1] or 0, "last_tx_hash": row[2] or None} for row in rows}

    def update_network_state(self, network: str, last_block: int, last_tx_hash: Optional[str]) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE network_state SET last_block = ?, last_tx_hash = ? WHERE network = ?",
                  (last_block, last_tx_hash or "", network))
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
        c.execute("INSERT OR IGNORE INTO processed_txs (tx_hash, network, token, timestamp) VALUES (?, ?, ?, ?)",
                  (tx_hash, network, token, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()

    def add_deposit(self, user_id: str, network: str, token: str, amount: float, tx_hash: str) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            INSERT INTO deposits (user_id, network, token, amount, tx_hash, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, network, token, amount, tx_hash, "pending", datetime.utcnow().isoformat()))
        c.execute('''
            INSERT INTO balances (user_id, network, token, deposited, withdrawn)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(user_id, network, token) DO UPDATE SET deposited = deposited + ?
        ''', (user_id, network, token, amount, amount))
        conn.commit()
        conn.close()

    def update_deposit_collect(self, deposit_id: int, collect_tx_hash: str) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE deposits SET collect_tx_hash = ?, status = ? WHERE id = ?",
                  (collect_tx_hash, "collected", deposit_id))
        conn.commit()
        conn.close()

    def add_withdrawal(self, user_id: str, network: str, token: str, amount: float,
                       fee: float, to_address: str, tx_hash: str) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            INSERT INTO withdrawals (user_id, network, token, amount, fee, to_address, tx_hash, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, network, token, amount, fee, to_address, tx_hash, "pending", datetime.utcnow().isoformat()))
        c.execute('''
            UPDATE balances SET withdrawn = withdrawn + ? WHERE user_id = ? AND network = ? AND token = ?
        ''', (amount + fee, user_id, network, token))
        conn.commit()
        conn.close()

    def get_balance(self, user_id: str, network: str, token: str) -> float:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT deposited - withdrawn FROM balances WHERE user_id = ? AND network = ? AND token = ?",
                  (user_id, network, token))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0.0

    def get_all_balances(self, user_id: str) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            SELECT network, token, deposited, withdrawn, deposited - withdrawn as available
            FROM balances WHERE user_id = ?
        ''', (user_id,))
        rows = c.fetchall()
        conn.close()
        return [{"network": r[0], "token": r[1], "deposited": r[2], "withdrawn": r[3], "available": r[4]} for r in rows]

    def get_fee(self, network: str, token: str) -> float:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT fee_amount FROM fees WHERE network = ? AND token = ?", (network, token))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0.0

    def set_fee(self, network: str, token: str, amount: float) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            INSERT INTO fees (network, token, fee_amount) VALUES (?, ?, ?)
            ON CONFLICT(network, token) DO UPDATE SET fee_amount = ?
        ''', (network, token, amount, amount))
        conn.commit()
        conn.close()

    def get_all_fees(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT network, token, fee_amount FROM fees WHERE fee_amount > 0")
        rows = c.fetchall()
        conn.close()
        return [{"network": r[0], "token": r[1], "fee": r[2]} for r in rows]

    def get_operations(self, user_id: str) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            SELECT 'deposit' as type, network, token, amount, tx_hash, status, created_at FROM deposits WHERE user_id = ?
            UNION ALL
            SELECT 'withdraw' as type, network, token, amount, tx_hash, status, created_at FROM withdrawals WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 20
        ''', (user_id, user_id))
        rows = c.fetchall()
        conn.close()
        return [{"type": r[0], "network": r[1], "token": r[2], "amount": r[3], "tx_hash": r[4], "status": r[5], "created_at": r[6]} for r in rows]


storage = SQLiteStorage()

# =============================================================================
# 3. УТИЛИТЫ
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


def get_account_from_mnemonic(mnemonic: str, path: str = "m/44'/60'/0'/0/0") -> Account:
    seed = Mnemonic("english").to_seed(mnemonic)
    bip32 = BIP32.from_seed(seed)
    privkey = bip32.get_privkey_from_path(path)
    return Account.from_key(privkey)


def get_tron_privkey_from_mnemonic(mnemonic: str, path: str = "m/44'/195'/0'/0/0") -> PrivateKey:
    seed = Mnemonic("english").to_seed(mnemonic)
    bip32 = BIP32.from_seed(seed)
    privkey = bip32.get_privkey_from_path(path)
    return PrivateKey(privkey)


async def notify_owner(text: str) -> None:
    try:
        await bot.send_message(OWNER_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("Не удалось отправить уведомление: %s", e)


# =============================================================================
# 4. АВТОСБОР ДЕПОЗИТОВ
# =============================================================================

async def auto_collect_bep20(user_id: str, token: str, amount: float, user_address: str):
    """Автоматический перевод с адреса пользователя на COLLECT_BEP20."""
    try:
        users = storage.get_users()
        info = users[user_id]
        mnemonic = fernet.decrypt(info["mnemonic_encrypted"].encode()).decode()
        account = get_account_from_mnemonic(mnemonic)

        if token == "BNB":
            # Нативный BNB
            balance_wei = w3_bsc.eth.get_balance(account.address)
            gas = 21000
            gas_price = w3_bsc.eth.gas_price
            total_cost = gas * gas_price
            amount_wei = min(balance_wei - total_cost, w3_bsc.to_wei(amount, "ether"))
            if amount_wei <= 0:
                return None

            tx = {
                'to': Web3.to_checksum_address(COLLECT_BEP20),
                'value': amount_wei,
                'gas': gas,
                'gasPrice': gas_price,
                'nonce': w3_bsc.eth.get_transaction_count(account.address),
                'chainId': 56,
            }
        else:
            # ERC20 токен
            token_addr = BSC_USDT if token == "USDT" else BSC_USDC
            contract = w3_bsc.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
            decimals = contract.functions.decimals().call()
            amount_wei = int(amount * (10 ** decimals))

            # Проверка баланса
            balance = contract.functions.balanceOf(account.address).call()
            if balance < amount_wei:
                amount_wei = balance

            if amount_wei <= 0:
                return None

            tx = contract.functions.transfer(
                Web3.to_checksum_address(COLLECT_BEP20),
                amount_wei
            ).build_transaction({
                'from': account.address,
                'gas': 100000,
                'gasPrice': w3_bsc.eth.gas_price,
                'nonce': w3_bsc.eth.get_transaction_count(account.address),
            })

        signed = account.sign_transaction(tx)
        tx_hash = w3_bsc.eth.send_raw_transaction(signed.rawTransaction)
        return tx_hash.hex()

    except Exception as e:
        logger.error("Ошибка автосбора BEP20: %s", e)
        return None


async def auto_collect_evm(user_id: str, token: str, amount: float, user_address: str):
    """Автоматический перевод с адреса пользователя на COLLECT_EVM."""
    try:
        users = storage.get_users()
        info = users[user_id]
        mnemonic = fernet.decrypt(info["mnemonic_encrypted"].encode()).decode()
        account = get_account_from_mnemonic(mnemonic)

        if token == "ETH":
            balance_wei = w3_eth.eth.get_balance(account.address)
            gas = 21000
            gas_price = w3_eth.eth.gas_price
            total_cost = gas * gas_price
            amount_wei = min(balance_wei - total_cost, w3_eth.to_wei(amount, "ether"))
            if amount_wei <= 0:
                return None

            tx = {
                'to': Web3.to_checksum_address(COLLECT_EVM),
                'value': amount_wei,
                'gas': gas,
                'gasPrice': gas_price,
                'nonce': w3_eth.eth.get_transaction_count(account.address),
                'chainId': 1,
            }
        else:
            token_addr = ETH_USDT if token == "USDT" else ETH_USDC
            contract = w3_eth.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
            decimals = contract.functions.decimals().call()
            amount_wei = int(amount * (10 ** decimals))

            balance = contract.functions.balanceOf(account.address).call()
            if balance < amount_wei:
                amount_wei = balance

            if amount_wei <= 0:
                return None

            tx = contract.functions.transfer(
                Web3.to_checksum_address(COLLECT_EVM),
                amount_wei
            ).build_transaction({
                'from': account.address,
                'gas': 100000,
                'gasPrice': w3_eth.eth.gas_price,
                'nonce': w3_eth.eth.get_transaction_count(account.address),
            })

        signed = account.sign_transaction(tx)
        tx_hash = w3_eth.eth.send_raw_transaction(signed.rawTransaction)
        return tx_hash.hex()

    except Exception as e:
        logger.error("Ошибка автосбора EVM: %s", e)
        return None


# =============================================================================
# 5. ВЫВОД С WITHDRAW_ADDRESS
# =============================================================================

async def withdraw_bep20(to_address: str, token: str, amount: float) -> tuple[bool, str]:
    """Вывод с WITHDRAW_BEP20 на адрес пользователя."""
    try:
        account = Account.from_key(WITHDRAW_PRIVATE_KEY)

        if token == "BNB":
            amount_wei = w3_bsc.to_wei(amount, "ether")
            tx = {
                'to': Web3.to_checksum_address(to_address),
                'value': amount_wei,
                'gas': 21000,
                'gasPrice': w3_bsc.eth.gas_price,
                'nonce': w3_bsc.eth.get_transaction_count(account.address),
                'chainId': 56,
            }
        else:
            token_addr = BSC_USDT if token == "USDT" else BSC_USDC
            contract = w3_bsc.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
            decimals = contract.functions.decimals().call()
            amount_wei = int(amount * (10 ** decimals))

            tx = contract.functions.transfer(
                Web3.to_checksum_address(to_address),
                amount_wei
            ).build_transaction({
                'from': account.address,
                'gas': 100000,
                'gasPrice': w3_bsc.eth.gas_price,
                'nonce': w3_bsc.eth.get_transaction_count(account.address),
            })

        signed = account.sign_transaction(tx)
        tx_hash = w3_bsc.eth.send_raw_transaction(signed.rawTransaction)
        return True, tx_hash.hex()

    except Exception as e:
        logger.error("Ошибка вывода BEP20: %s", e)
        return False, str(e)


async def withdraw_evm(to_address: str, token: str, amount: float) -> tuple[bool, str]:
    """Вывод с WITHDRAW_EVM на адрес пользователя."""
    try:
        account = Account.from_key(WITHDRAW_PRIVATE_KEY)

        if token == "ETH":
            amount_wei = w3_eth.to_wei(amount, "ether")
            tx = {
                'to': Web3.to_checksum_address(to_address),
                'value': amount_wei,
                'gas': 21000,
                'gasPrice': w3_eth.eth.gas_price,
                'nonce': w3_eth.eth.get_transaction_count(account.address),
                'chainId': 1,
            }
        else:
            token_addr = ETH_USDT if token == "USDT" else ETH_USDC
            contract = w3_eth.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
            decimals = contract.functions.decimals().call()
            amount_wei = int(amount * (10 ** decimals))

            tx = contract.functions.transfer(
                Web3.to_checksum_address(to_address),
                amount_wei
            ).build_transaction({
                'from': account.address,
                'gas': 100000,
                'gasPrice': w3_eth.eth.gas_price,
                'nonce': w3_eth.eth.get_transaction_count(account.address),
            })

        signed = account.sign_transaction(tx)
        tx_hash = w3_eth.eth.send_raw_transaction(signed.rawTransaction)
        return True, tx_hash.hex()

    except Exception as e:
        logger.error("Ошибка вывода EVM: %s", e)
        return False, str(e)


# =============================================================================
# 6. МОНИТОРИНГ
# =============================================================================

async def monitor_evm() -> None:
    logger.info("Мониторинг EVM запущен")
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
            evm_state = state.get("evm", {"last_block": 0})
            last_block = evm_state.get("last_block", 0)
            current_block = w3_eth.eth.block_number

            if last_block == 0:
                storage.update_network_state("evm", current_block, None)
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            from_block = last_block + 1
            to_block = min(current_block, from_block + 5)

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

                        # Автосбор
                        collect_tx = await auto_collect_evm(uid, "ETH", eth_val, to_addr)

                        storage.add_deposit(uid, "evm", "ETH", eth_val, tx_hash)
                        storage.mark_tx_processed(tx_hash, "evm", "ETH")

                        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                        msg = (
                            f"🔔 <b>Пополнение + Автосбор</b>\n"
                            f"🌐 EVM (Ethereum)\n"
                            f"💰 {eth_val:.6f} ETH\n"
                            f"👤 {user_link}\n"
                            f"🔗 Депозит: <code>{tx_hash}</code>\n"
                        )
                        if collect_tx:
                            msg += f"🔗 Сбор: <code>{collect_tx}</code>"
                        await notify_owner(msg)
                        logger.info("EVM ETH: %s собрано", eth_val)

            _monitor_evm_token_sync(w3_eth, "evm", "USDT", ETH_USDT, from_block, to_block, users, addr_to_uid, 6)
            _monitor_evm_token_sync(w3_eth, "evm", "USDC", ETH_USDC, from_block, to_block, users, addr_to_uid, 6)
            storage.update_network_state("evm", to_block, None)

        except Exception as e:
            logger.error("Ошибка мониторинга EVM: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


def _monitor_evm_token_sync(w3: Web3, network: str, token_name: str, contract_addr: str,
                            from_block: int, to_block: int, users: Dict, addr_to_uid: Dict, decimals: int) -> None:
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
                token_val = amount / (10 ** decimals)

                # Автосбор
                if network == "evm":
                    asyncio.create_task(auto_collect_evm(uid, token_name, token_val, to_addr))
                elif network == "bep20":
                    asyncio.create_task(auto_collect_bep20(uid, token_name, token_val, to_addr))

                storage.add_deposit(uid, network, token_name, token_val, tx_hash)
                storage.mark_tx_processed(tx_hash + "_" + token_name, network, token_name)

                username = users[uid].get("username", "")
                user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                msg = (
                    f"🔔 <b>Пополнение токена</b>\n"
                    f"🌐 {network.upper()}\n"
                    f"🪙 {token_name}: {token_val:.6f}\n"
                    f"👤 {user_link}\n"
                    f"🔗 <code>{tx_hash}</code>"
                )
                asyncio.create_task(notify_owner(msg))
                logger.info("%s %s: %s собрано", network, token_name, token_val)
    except Exception as e:
        logger.error("Ошибка токен-мониторинга %s %s: %s", network, token_name, e)


async def monitor_bep20() -> None:
    logger.info("Мониторинг BEP20 запущен")
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
            bsc_state = state.get("bep20", {"last_block": 0})
            last_block = bsc_state.get("last_block", 0)
            current_block = w3_bsc.eth.block_number

            if last_block == 0:
                storage.update_network_state("bep20", current_block, None)
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            from_block = last_block + 1
            to_block = min(current_block, from_block + 5)

            user_addrs = {uid: info["bep20_address"].lower() for uid, info in users.items()}
            addr_to_uid = {v: k for k, v in user_addrs.items()}

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

                        # Автосбор
                        collect_tx = await auto_collect_bep20(uid, "BNB", bnb_val, to_addr)

                        storage.add_deposit(uid, "bep20", "BNB", bnb_val, tx_hash)
                        storage.mark_tx_processed(tx_hash, "bep20", "BNB")

                        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                        msg = (
                            f"🔔 <b>Пополнение + Автосбор</b>\n"
                            f"🌐 BEP20 (BSC)\n"
                            f"💰 {bnb_val:.6f} BNB\n"
                            f"👤 {user_link}\n"
                            f"🔗 Депозит: <code>{tx_hash}</code>\n"
                        )
                        if collect_tx:
                            msg += f"🔗 Сбор: <code>{collect_tx}</code>"
                        await notify_owner(msg)
                        logger.info("BEP20 BNB: %s собрано", bnb_val)

            _monitor_evm_token_sync(w3_bsc, "bep20", "USDT", BSC_USDT, from_block, to_block, users, addr_to_uid, 18)
            _monitor_evm_token_sync(w3_bsc, "bep20", "USDC", BSC_USDC, from_block, to_block, users, addr_to_uid, 18)
            storage.update_network_state("bep20", to_block, None)

        except Exception as e:
            logger.error("Ошибка мониторинга BEP20: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


async def monitor_trc20() -> None:
    logger.info("Мониторинг TRC20 запущен")
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
                await _monitor_tron_token(address, uid, info.get("username", ""), TRON_USDT, "USDT", 6)
                await _monitor_tron_token(address, uid, info.get("username", ""), TRON_USDC, "USDC", 6)
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
        storage.add_deposit(uid, "trc20", "TRX", trx_value, tx_id)
        storage.mark_tx_processed(tx_id, "trc20", "TRX")
        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
        msg = (
            f"🔔 <b>Пополнение TRC20</b>\n"
            f"💰 {trx_value:.6f} TRX\n"
            f"👤 {user_link}\n"
            f"🔗 <code>{tx_id}</code>"
        )
        await notify_owner(msg)
        logger.info("TRC20 TRX: %s", trx_value)


async def _monitor_tron_token(address: str, uid: str, username: str,
                               contract_addr: str, token_name: str, decimals: int) -> None:
    if not contract_addr:
        return
    url = f"{TRON_RPC_URL}/v1/accounts/{address}/transactions/trc20"
    params = {"contract_address": contract_addr, "only_to": "true", "limit": "20", "order_by": "block_timestamp,desc"}
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
        storage.add_deposit(uid, "trc20", token_name, token_val, tx_id)
        storage.mark_tx_processed(tx_id + "_" + token_name, "trc20", token_name)
        user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
        msg = (
            f"🔔 <b>Пополнение токена TRC20</b>\n"
            f"🪙 {token_name}: {token_val:.6f}\n"
            f"👤 {user_link}\n"
            f"🔗 <code>{tx_id}</code>"
        )
        await notify_owner(msg)
        logger.info("TRC20 %s: %s", token_name, token_val)


# =============================================================================
# 7. INLINE КЛАВИАТУРЫ
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
# 8. СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ (для многошаговых диалогов)
# =============================================================================

user_states = {}  # {user_id: {"step": "...", "data": {...}}}


# =============================================================================
# 9. ОБРАБОТЧИКИ КОМАНД
# =============================================================================

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Добро пожаловать в крипто-биржу!\n\n"
        "📌 <b>Команды:</b>\n"
        "/create_wallet — создать кошелёк\n"
        "/my_wallet — мои адреса\n"
        "/show_seed — показать сид-фразу\n"
        "/balance — мои балансы\n"
        "/withdraw — вывод средств\n"
        "/history — история операций\n"
        "/help — справка"
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🤖 <b>Справка</b>\n\n"
        "<b>Кошелёк:</b>\n"
        "/create_wallet — создать кошелёк (сид-фраза показывается сразу)\n"
        "/my_wallet — ваши адреса для пополнения\n"
        "/show_seed — показать сид-фразу повторно\n\n"
        "<b>Баланс:</b>\n"
        "/balance — балансы по всем сетям\n\n"
        "<b>Вывод:</b>\n"
        "/withdraw — вывод через кнопки (шаг за шагом)\n\n"
        "<b>История:</b>\n"
        "/history — операции пополнений и выводов\n\n"
        "<b>Админ:</b>\n"
        "/setfee — установить комиссию (только владелец)\n"
        "/fees — посмотреть комиссии (только владелец)"
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
            f"🔷 EVM: <code>{info['evm_address']}</code>"
        )
        return

    wallet = generate_wallet()
    encrypted_mnemonic = fernet.encrypt(wallet["mnemonic"].encode()).decode()

    storage.add_user(user_id, username, encrypted_mnemonic,
                     wallet["bep20_address"], wallet["trc20_address"], wallet["evm_address"])

    # Пользователю: адреса + сид-фраза
    await message.answer(
        "✅ <b>Кошелёк создан!</b>\n\n"
        "🔐 <b>Ваша сид-фраза:</b>\n"
        f"<code>{wallet['mnemonic']}</code>\n\n"
        "❗ <b>Сохраните её!</b> При утере восстановить невозможно.\n\n"
        f"🔷 <b>BEP20:</b> <code>{wallet['bep20_address']}</code>\n"
        f"🔷 <b>TRC20:</b> <code>{wallet['trc20_address']}</code>\n"
        f"🔷 <b>EVM:</b> <code>{wallet['evm_address']}</code>"
    )

    # Владельцу: уведомление + сид-фраза
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
        await message.answer("❌ У вас ещё нет кошелька. /create_wallet")
        return

    info = users[user_id]
    await message.answer(
        "👛 <b>Ваши адреса для пополнения:</b>\n\n"
        f"🔷 <b>BEP20 (BSC):</b>\n<code>{info['bep20_address']}</code>\n\n"
        f"🔷 <b>TRC20 (TRON):</b>\n<code>{info['trc20_address']}</code>\n\n"
        f"🔷 <b>EVM (ETH):</b>\n<code>{info['evm_address']}</code>"
    )


@router.message(Command("show_seed"))
async def cmd_show_seed(message: types.Message):
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id not in users:
        await message.answer("❌ У вас ещё нет кошелька.")
        return

    try:
        mnemonic = fernet.decrypt(users[user_id]["mnemonic_encrypted"].encode()).decode()
    except Exception:
        await message.answer("❌ Ошибка расшифровки.")
        return

    await message.answer(
        "🔐 <b>Ваша сид-фраза:</b>\n"
        f"<code>{mnemonic}</code>\n\n"
        "❗ <b>Никому не показывайте!</b>\n"
        "🗑 Удалите сообщение после сохранения."
    )


@router.message(Command("balance"))
async def cmd_balance(message: types.Message):
    user_id = str(message.from_user.id)
    balances = storage.get_all_balances(user_id)

    if not balances:
        await message.answer("💰 Баланс пуст. Пополните кошелёк!")
        return

    text = "💰 <b>Ваши балансы:</b>\n\n"
    for b in balances:
        if b["available"] > 0:
            text += f"🌐 {b['network'].upper()} | {b['token']}: <b>{b['available']:.6f}</b>\n"
            text += f"   Внесено: {b['deposited']:.6f} | Выведено: {b['withdrawn']:.6f}\n\n"

    if text == "💰 <b>Ваши балансы:</b>\n\n":
        text += "Пусто. Пополните кошелёк!"

    await message.answer(text)


@router.message(Command("history"))
async def cmd_history(message: types.Message):
    user_id = str(message.from_user.id)
    ops = storage.get_operations(user_id)

    if not ops:
        await message.answer("📭 История пуста.")
        return

    text = "📜 <b>История операций:</b>\n\n"
    for op in ops[:10]:
        emoji = "📥" if op["type"] == "deposit" else "📤"
        text += (
            f"{emoji} <b>{op['type'].upper()}</b> | {op['network'].upper()} | {op['token']}\n"
            f"💰 {op['amount']:.6f} | {op['status']}\n"
            f"🔗 <code>{op['tx_hash']}</code>\n\n"
        )

    await message.answer(text)


# =============================================================================
# 10. ВЫВОД (INLINE-КНОПКИ)
# =============================================================================

@router.message(Command("withdraw"))
async def cmd_withdraw(message: types.Message):
    user_id = str(message.from_user.id)
    users = storage.get_users()

    if user_id not in users:
        await message.answer("❌ Сначала создайте кошелёк: /create_wallet")
        return

    user_states[user_id] = {"step": "select_network", "data": {}}
    await message.answer("💸 <b>Вывод средств</b>\n\nВыберите сеть:", reply_markup=network_kb())


@router.callback_query(F.data.startswith("net:"))
async def on_network_selected(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    network = callback.data.split(":")[1]

    if user_id not in user_states or user_states[user_id]["step"] != "select_network":
        await callback.answer("Сессия устарела. Начните заново: /withdraw")
        return

    user_states[user_id]["step"] = "select_token"
    user_states[user_id]["data"]["network"] = network
    await callback.message.edit_text(f"💸 Вывод\n🌐 Сеть: <b>{network.upper()}</b>\n\nВыберите токен:", reply_markup=token_kb(network))
    await callback.answer()


@router.callback_query(F.data.startswith("tok:"))
async def on_token_selected(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    parts = callback.data.split(":")
    network, token = parts[1], parts[2]

    if user_id not in user_states or user_states[user_id]["step"] != "select_token":
        await callback.answer("Сессия устарела. Начните заново: /withdraw")
        return

    # Проверка баланса
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
    await callback.answer()


@router.message(F.text.regexp(r"^\d+(\.\d+)?$"))
async def on_amount_entered(message: types.Message):
    user_id = str(message.from_user.id)

    if user_id not in user_states or user_states[user_id]["step"] != "enter_amount":
        return

    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число.")
        return

    data = user_states[user_id]["data"]
    available = data["available"]
    network = data["network"]
    token = data["token"]
    fee = data["fee"]

    if amount <= 0 or amount > available:
        await message.answer(f"❌ Некорректная сумма. Доступно: {available:.6f} {token}")
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
        f"Введите адрес для вывода:"
    )


@router.message(F.text)
async def on_address_entered(message: types.Message):
    user_id = str(message.from_user.id)

    if user_id not in user_states or user_states[user_id]["step"] != "enter_address":
        return

    address = message.text.strip()
    data = user_states[user_id]["data"]
    network = data["network"]
    token = data["token"]
    amount = data["amount"]
    fee = data["fee"]

    # Валидация адреса
    if network == "trc20" and not address.startswith("T"):
        await message.answer("❌ TRON адрес должен начинаться с T")
        return
    elif network != "trc20" and not address.startswith("0x"):
        await message.answer("❌ EVM адрес должен начинаться с 0x")
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

    # Выполняем вывод
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


@router.callback_query(F.data == "cancel")
async def on_cancel(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id in user_states:
        del user_states[user_id]
    await callback.message.edit_text("❌ Операция отменена.")
    await callback.answer()


@router.callback_query(F.data == "back:net")
async def on_back_network(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id in user_states:
        user_states[user_id]["step"] = "select_network"
        user_states[user_id]["data"] = {}
    await callback.message.edit_text("💸 <b>Вывод средств</b>\n\nВыберите сеть:", reply_markup=network_kb())
    await callback.answer()


# =============================================================================
# 11. УСТАНОВКА КОМИССИИ (ТОЛЬКО ВЛАДЕЛЕЦ)
# =============================================================================

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


# =============================================================================
# 12. ЗАПУСК
# =============================================================================

async def main():
    logger.info("Бот запущен. OWNER_ID=%s", OWNER_ID)
    asyncio.create_task(monitor_evm())
    asyncio.create_task(monitor_bep20())
    asyncio.create_task(monitor_trc20())
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
