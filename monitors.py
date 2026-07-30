#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Фоновые мониторы депозитов: EVM (Ethereum), BEP20 (BSC), TRC20 (TRON).
Оптимизированная версия с уменьшенной нагрузкой на RPC.
"""

import asyncio
import time
from typing import Dict, List, Optional

import aiohttp
from tronpy.keys import to_base58check_address
from web3 import Web3

from collector import auto_collect_bep20, auto_collect_evm
from config import (
    BSC_USDT, BSC_USDC, ERC20_TRANSFER_TOPIC, ETH_USDT, ETH_USDC,
    MONITOR_INTERVAL, TRON_RPC_FALLBACK, TRON_RPC_URL, TRON_USDT, TRON_USDC,
    logger, w3_bsc, w3_bsc_fallback, w3_eth, w3_eth_fallback,
)
from db import storage
from notify import notify_owner

# === Вспомогательные функции ===

def get_user_addresses(users: Dict, network: str) -> Dict[str, str]:
    """Возвращает словарь {address_lower: user_id} для указанной сети."""
    addr_key = {
        "evm": "evm_address",
        "bep20": "bep20_address",
        "trc20": "trc20_address",
    }.get(network, "evm_address")
    result = {}
    for uid, info in users.items():
        addr = info.get(addr_key, "")
        if addr:
            result[addr.lower()] = uid
    return result

def get_web3_with_fallback(w3_primary, w3_fallback, network_name: str) -> Optional[Web3]:
    """Возвращает рабочий Web3-экземпляр, переключаясь на fallback при необходимости."""
    if w3_primary.is_connected():
        return w3_primary
    logger.warning(f"{network_name} RPC недоступен, пробуем fallback...")
    if w3_fallback.is_connected():
        logger.info(f"{network_name} переключён на fallback RPC")
        return w3_fallback
    logger.error(f"{network_name} fallback RPC тоже недоступен")
    return None

def get_block_with_retry(w3, block_num, full_tx=True, retries=3, delay=1):
    """Синхронный вызов get_block с повторными попытками."""
    for attempt in range(retries):
        try:
            return w3.eth.get_block(block_num, full_transactions=full_tx)
        except Exception as e:
            if attempt == retries - 1:
                raise
            logger.warning(f"Блок {block_num} ошибка (попытка {attempt+1}): {e}")
            time.sleep(delay * (2 ** attempt))
    return None

def get_logs_with_retry(w3, params, retries=3, delay=1):
    """Синхронный вызов get_logs с повторными попытками."""
    for attempt in range(retries):
        try:
            return w3.eth.get_logs(params)
        except Exception as e:
            # Если лимит превышен — делаем долгую паузу
            if "limit exceeded" in str(e).lower():
                logger.warning("RPC лимит превышен, пауза 60 секунд...")
                time.sleep(60)
                continue
            if attempt == retries - 1:
                raise
            logger.warning(f"get_logs ошибка (попытка {attempt+1}): {e}")
            time.sleep(delay * (2 ** attempt))
    return None

# =============================================================================
# EVM (ETHEREUM) — оптимизированный
# =============================================================================

async def monitor_evm() -> None:
    logger.info("Мониторинг EVM запущен (оптимизированный)")
    last_alert_time = 0
    while True:
        try:
            w3 = get_web3_with_fallback(w3_eth, w3_eth_fallback, "EVM")
            if not w3:
                if time.time() - last_alert_time > 300:
                    await notify_owner("🚨 <b>EVM основной и fallback RPC недоступны!</b>")
                    last_alert_time = time.time()
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            users = storage.get_users()
            if not users:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            state = storage.get_network_state()
            evm_state = state.get("evm", {"last_block": 0})
            last_block = evm_state.get("last_block", 0)
            current_block = w3.eth.block_number

            if last_block == 0:
                storage.update_network_state("evm", current_block, None)
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            from_block = last_block + 1
            to_block = min(current_block, from_block + 2)  # только 2 блока за раз

            if from_block <= to_block:
                addr_to_uid = get_user_addresses(users, "evm")
                # Сканируем блоки для нативных ETH
                for block_num in range(from_block, to_block + 1):
                    try:
                        block = get_block_with_retry(w3, block_num, full_tx=True, retries=2, delay=1)
                    except Exception as e:
                        logger.warning("EVM блок %s: %s", block_num, e)
                        continue
                    if not block:
                        continue
                    for tx in block.transactions:
                        to_addr = (tx.get("to") or "").lower()
                        if to_addr in addr_to_uid:
                            value = tx.get("value", 0)
                            if value > 0:
                                tx_hash = tx.get("hash", b"").hex()
                                if storage.is_tx_processed(tx_hash):
                                    continue
                                uid = addr_to_uid[to_addr]
                                eth_val = w3.from_wei(value, "ether")
                                collect_tx = await auto_collect_evm(uid, "ETH", eth_val, to_addr)
                                storage.add_deposit(uid, "evm", "ETH", eth_val, tx_hash)
                                storage.mark_tx_processed(tx_hash, "evm", "ETH")
                                username = users[uid].get("username", "")
                                user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                                msg = (f"🔔 <b>Пополнение + Автосбор</b>\n"
                                       f"🌐 EVM (Ethereum)\n💰 {eth_val:.6f} ETH\n👤 {user_link}\n"
                                       f"🔗 Депозит: <code>{tx_hash}</code>")
                                if collect_tx:
                                    msg += f"\n🔗 Сбор: <code>{collect_tx}</code>"
                                await notify_owner(msg)
                                logger.info("EVM ETH: %s собрано", eth_val)

                # Токены EVM (USDT, USDC) — объединяем в один запрос
                if ETH_USDT and ETH_USDC:
                    await _monitor_evm_tokens(
                        w3, "evm",
                        [ETH_USDT, ETH_USDC],
                        ["USDT", "USDC"],
                        [6, 6],
                        from_block, to_block, users, addr_to_uid
                    )

                storage.update_network_state("evm", to_block, None)

        except Exception as e:
            logger.error("Ошибка мониторинга EVM: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


async def _monitor_evm_tokens(w3: Web3, network: str, contract_addrs: List[str],
                              token_names: List[str], decimals: List[int],
                              from_block: int, to_block: int,
                              users: Dict, addr_to_uid: Dict) -> None:
    """Мониторинг нескольких токенов за один запрос eth_getLogs."""
    if not contract_addrs or len(contract_addrs) == 0:
        return
    valid = [(addr, name, dec) for addr, name, dec in zip(contract_addrs, token_names, decimals) if addr and addr != "0x"]
    if not valid:
        return
    try:
        addresses = [Web3.to_checksum_address(addr) for addr, _, _ in valid]
        logs = get_logs_with_retry(w3, {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": addresses,
            "topics": [ERC20_TRANSFER_TOPIC],
        }, retries=2, delay=1)
        if logs is None:
            return
        addr_to_info = {addr.lower(): (name, dec) for addr, name, dec in valid}
        for log in logs:
            tx_hash = log["transactionHash"].hex()
            contract_addr = log["address"].lower()
            if contract_addr not in addr_to_info:
                continue
            token_name, dec = addr_to_info[contract_addr]
            if storage.is_tx_processed(tx_hash + "_" + token_name):
                continue
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue
            to_addr = "0x" + topics[2].hex()[-40:]
            to_addr_lower = to_addr.lower()
            if to_addr_lower not in addr_to_uid:
                continue
            data = log.get("data", "0x")
            if data == "0x":
                continue
            amount = int(data, 16)
            if amount <= 0:
                continue
            uid = addr_to_uid[to_addr_lower]
            token_val = amount / (10 ** dec)

            # Автосбор
            if network == "evm":
                asyncio.create_task(auto_collect_evm(uid, token_name, token_val, to_addr))
            else:
                asyncio.create_task(auto_collect_bep20(uid, token_name, token_val, to_addr))

            storage.add_deposit(uid, network, token_name, token_val, tx_hash)
            storage.mark_tx_processed(tx_hash + "_" + token_name, network, token_name)

            username = users[uid].get("username", "")
            user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
            msg = (f"🔔 <b>Пополнение токена</b>\n"
                   f"🌐 {network.upper()}\n🪙 {token_name}: {token_val:.6f}\n"
                   f"👤 {user_link}\n🔗 <code>{tx_hash}</code>")
            asyncio.create_task(notify_owner(msg))
            logger.info("%s %s: %s собрано", network, token_name, token_val)
    except Exception as e:
        logger.error("Ошибка токен-мониторинга %s: %s", network, e)


# =============================================================================
# BEP20 (BSC) — аналогично EVM
# =============================================================================

async def monitor_bep20() -> None:
    logger.info("Мониторинг BEP20 запущен (оптимизированный)")
    last_alert_time = 0
    while True:
        try:
            w3 = get_web3_with_fallback(w3_bsc, w3_bsc_fallback, "BSC")
            if not w3:
                if time.time() - last_alert_time > 300:
                    await notify_owner("🚨 <b>BSC основной и fallback RPC недоступны!</b>")
                    last_alert_time = time.time()
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            users = storage.get_users()
            if not users:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            state = storage.get_network_state()
            bsc_state = state.get("bep20", {"last_block": 0})
            last_block = bsc_state.get("last_block", 0)
            current_block = w3.eth.block_number

            if last_block == 0:
                storage.update_network_state("bep20", current_block, None)
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            from_block = last_block + 1
            to_block = min(current_block, from_block + 2)

            if from_block <= to_block:
                addr_to_uid = get_user_addresses(users, "bep20")
                for block_num in range(from_block, to_block + 1):
                    try:
                        block = get_block_with_retry(w3, block_num, full_tx=True, retries=2, delay=1)
                    except Exception as e:
                        logger.warning("BSC блок %s: %s", block_num, e)
                        continue
                    if not block:
                        continue
                    for tx in block.transactions:
                        to_addr = (tx.get("to") or "").lower()
                        if to_addr in addr_to_uid:
                            value = tx.get("value", 0)
                            if value > 0:
                                tx_hash = tx.get("hash", b"").hex()
                                if storage.is_tx_processed(tx_hash):
                                    continue
                                uid = addr_to_uid[to_addr]
                                bnb_val = w3.from_wei(value, "ether")
                                collect_tx = await auto_collect_bep20(uid, "BNB", bnb_val, to_addr)
                                storage.add_deposit(uid, "bep20", "BNB", bnb_val, tx_hash)
                                storage.mark_tx_processed(tx_hash, "bep20", "BNB")
                                username = users[uid].get("username", "")
                                user_link = f'<a href="tg://user?id={uid}">{username or uid}</a>'
                                msg = (f"🔔 <b>Пополнение + Автосбор</b>\n"
                                       f"🌐 BEP20 (BSC)\n💰 {bnb_val:.6f} BNB\n👤 {user_link}\n"
                                       f"🔗 Депозит: <code>{tx_hash}</code>")
                                if collect_tx:
                                    msg += f"\n🔗 Сбор: <code>{collect_tx}</code>"
                                await notify_owner(msg)
                                logger.info("BEP20 BNB: %s собрано", bnb_val)

                if BSC_USDT and BSC_USDC:
                    await _monitor_evm_tokens(
                        w3, "bep20",
                        [BSC_USDT, BSC_USDC],
                        ["USDT", "USDC"],
                        [18, 18],
                        from_block, to_block, users, addr_to_uid
                    )

                storage.update_network_state("bep20", to_block, None)

        except Exception as e:
            logger.error("Ошибка мониторинга BEP20: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


# =============================================================================
# TRC20 (TRON) — оптимизированный
# =============================================================================

async def monitor_trc20() -> None:
    logger.info("Мониторинг TRC20 запущен (оптимизированный)")
    last_alert_time = 0
    while True:
        try:
            tron_url = await _get_tron_rpc_url(last_alert_time)
            if not tron_url:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            users = storage.get_users()
            if not users:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            addr_to_uid = get_user_addresses(users, "trc20")
            if not addr_to_uid:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            for address, uid in addr_to_uid.items():
                await _monitor_tron_native_optimized(address, uid, users[uid].get("username", ""), tron_url)
                await _monitor_tron_token_optimized(address, uid, users[uid].get("username", ""),
                                                    TRON_USDT, "USDT", 6, tron_url)
                await _monitor_tron_token_optimized(address, uid, users[uid].get("username", ""),
                                                    TRON_USDC, "USDC", 6, tron_url)

        except Exception as e:
            logger.error("Ошибка мониторинга TRC20: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


async def _get_tron_rpc_url(last_alert_time: float) -> Optional[str]:
    """Проверяет доступность TRON RPC и возвращает рабочий URL."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{TRON_RPC_URL}/v1/healthcheck", timeout=10) as resp:
                if resp.status == 200:
                    return TRON_RPC_URL
    except Exception:
        logger.warning("TRON RPC недоступен, пробуем fallback...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{TRON_RPC_FALLBACK}/v1/healthcheck", timeout=10) as resp:
                if resp.status == 200:
                    logger.info("TRON переключён на fallback RPC")
                    return TRON_RPC_FALLBACK
    except Exception:
        logger.error("TRON fallback RPC тоже недоступен")
        if time.time() - last_alert_time > 300:
            await notify_owner("🚨 <b>TRON основной и fallback RPC недоступны!</b>")
            last_alert_time = time.time()
    return None


async def _monitor_tron_native_optimized(address: str, uid: str, username: str, tron_url: str) -> None:
    """Мониторинг нативных TRX с проверкой только неподтверждённых транзакций."""
    url = f"{tron_url}/v1/accounts/{address}/transactions"
    params = {"only_to": "true", "limit": "10", "order_by": "block_timestamp,desc"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=30) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        txs = data.get("data", [])
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
            await notify_owner(f"🔔 <b>Пополнение TRC20</b>\n💰 {trx_value:.6f} TRX\n👤 {user_link}\n🔗 <code>{tx_id}</code>")
            logger.info("TRC20 TRX: %s", trx_value)
    except Exception as e:
        logger.error("Ошибка TRX мониторинга для %s: %s", address, e)


async def _monitor_tron_token_optimized(address: str, uid: str, username: str,
                                        contract_addr: str, token_name: str, decimals: int, tron_url: str) -> None:
    """Мониторинг TRC20 токенов с проверкой только неподтверждённых транзакций."""
    if not contract_addr:
        return
    url = f"{tron_url}/v1/accounts/{address}/transactions/trc20"
    params = {"contract_address": contract_addr, "only_to": "true", "limit": "10", "order_by": "block_timestamp,desc"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=30) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        txs = data.get("data", [])
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
            await notify_owner(f"🔔 <b>Пополнение токена TRC20</b>\n🪙 {token_name}: {token_val:.6f}\n👤 {user_link}\n🔗 <code>{tx_id}</code>")
            logger.info("TRC20 %s: %s", token_name, token_val)
    except Exception as e:
        logger.error("Ошибка TRC20 %s для %s: %s", token_name, address, e)
