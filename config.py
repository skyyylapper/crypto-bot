#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Конфигурация: переменные окружения, RPC-клиенты, ABI, инициализация бота.
"""

import logging
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# =============================================================================
# ОСНОВНЫЕ ПЕРЕМЕННЫЕ
# =============================================================================

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
FERNET_KEY: str = os.getenv("FERNET_KEY", "").strip().strip('"').strip("'").strip()
MONITOR_INTERVAL: int = int(os.getenv("MONITOR_INTERVAL", "30"))

# RPC
BSC_RPC_URL: str = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org")
BSC_RPC_FALLBACK: str = os.getenv("BSC_RPC_FALLBACK", "https://bsc-dataseed1.defibit.io")
TRON_RPC_URL: str = os.getenv("TRON_RPC_URL", "https://api.trongrid.io")
TRON_RPC_FALLBACK: str = os.getenv("TRON_RPC_FALLBACK", "https://api.tronstack.io")
ETH_RPC_URL: str = os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")
ETH_RPC_FALLBACK: str = os.getenv("ETH_RPC_FALLBACK", "https://ethereum-rpc.publicnode.com")

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

DB_FILE = "data.db"

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

# =============================================================================
# БОТ / ДИСПЕТЧЕР / РОУТЕР
# =============================================================================

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# =============================================================================
# WEB3 КЛИЕНТЫ
# =============================================================================

w3_eth = Web3(Web3.HTTPProvider(ETH_RPC_URL))
w3_eth_fallback = Web3(Web3.HTTPProvider(ETH_RPC_FALLBACK))
w3_bsc = Web3(Web3.HTTPProvider(BSC_RPC_URL))
w3_bsc_fallback = Web3(Web3.HTTPProvider(BSC_RPC_FALLBACK))


def _inject_poa(w3_instance: Web3) -> None:
    """Подключает POA middleware (нужно для BSC)."""
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
        w3_instance.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except ImportError:
        try:
            from web3.middleware.geth_poa import geth_poa_middleware
            w3_instance.middleware_onion.inject(geth_poa_middleware, layer=0)
        except ImportError:
            pass


_inject_poa(w3_bsc)
_inject_poa(w3_bsc_fallback)

# =============================================================================
# ERC20 ABI / КОНСТАНТЫ
# =============================================================================

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
