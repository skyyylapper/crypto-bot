#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Фоновые мониторы депозитов: EVM (Ethereum), BEP20 (BSC), TRC20 (TRON).
"""

import asyncio
from typing import Dict

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

# =============================================================================
# EVM (ETHEREUM)
# =============================================================================

async def monitor_evm() -> None:
    logger.info("Мониторинг EVM запущен")
    _evm_rpc_alerted = False
    while True:
        try:
            w3 = w3_eth
            if not w3_eth.is_connected():
                logger.warning("EVM RPC недоступен, пробуем fallback...")
                if w3_eth_fallback.is_connected():
                    w3 = w3_eth_fallback
                    logger.info("EVM переключён на fallback RPC")
                    if not _evm_rpc_alerted:
                        await notify_owner("⚠️ <b>EVM основной RPC недоступен</b>\nПереключён на fallback RPC.")
                        _evm_rpc_alerted = True
                else:
                    logger.error("EVM fallback RPC тоже недоступен")
                    if not _evm_rpc_alerted:
                        await notify_owner("🚨 <b>EVM основной и fallback RPC недоступны!</b>\nМониторинг приостановлен.")
                        _evm_rpc_alerted = True
                    await asyncio.sleep(MONITOR_INTERVAL)
                    continue
            else:
                if _evm_rpc_alerted:
                    await notify_owner("✅ <b>EVM основной RPC восстановлен</b>")
                    _evm_rpc_alerted = False

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
            to_block = min(current_block, from_block + 5)

            user_addrs = {uid: info["evm_address"].lower() for uid, info in users.items()}
            addr_to_uid = {v: k for k, v in user_addrs.items()}

            # Нативный ETH
            for block_num in range(from_block, to_block + 1):
                try:
                    block = w3.eth.get_block(block_num, full_transactions=True)
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
                        eth_val = w3.from_wei(value, "ether")

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

            _monitor_evm_token_sync(w3, "evm", "USDT", ETH_USDT, from_block, to_block, users, addr_to_uid, 6)
            _monitor_evm_token_sync(w3, "evm", "USDC", ETH_USDC, from_block, to_block, users, addr_to_uid, 6)
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


# =============================================================================
# BEP20 (BSC)
# =============================================================================

async def monitor_bep20() -> None:
    logger.info("Мониторинг BEP20 запущен")
    _bsc_rpc_alerted = False
    while True:
        try:
            w3 = w3_bsc
            if not w3_bsc.is_connected():
                logger.warning("BSC RPC недоступен, пробуем fallback...")
                if w3_bsc_fallback.is_connected():
                    w3 = w3_bsc_fallback
                    logger.info("BSC переключён на fallback RPC")
                    if not _bsc_rpc_alerted:
                        await notify_owner("⚠️ <b>BSC основной RPC недоступен</b>\nПереключён на fallback RPC.")
                        _bsc_rpc_alerted = True
                else:
                    logger.error("BSC fallback RPC тоже недоступен")
                    if not _bsc_rpc_alerted:
                        await notify_owner("🚨 <b>BSC основной и fallback RPC недоступны!</b>\nМониторинг приостановлен.")
                        _bsc_rpc_alerted = True
                    await asyncio.sleep(MONITOR_INTERVAL)
                    continue
            else:
                if _bsc_rpc_alerted:
                    await notify_owner("✅ <b>BSC основной RPC восстановлен</b>")
                    _bsc_rpc_alerted = False

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
            to_block = min(current_block, from_block + 5)

            user_addrs = {uid: info["bep20_address"].lower() for uid, info in users.items()}
            addr_to_uid = {v: k for k, v in user_addrs.items()}

            for block_num in range(from_block, to_block + 1):
                try:
                    block = w3.eth.get_block(block_num, full_transactions=True)
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
                        bnb_val = w3.from_wei(value, "ether")

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

            _monitor_evm_token_sync(w3, "bep20", "USDT", BSC_USDT, from_block, to_block, users, addr_to_uid, 18)
            _monitor_evm_token_sync(w3, "bep20", "USDC", BSC_USDC, from_block, to_block, users, addr_to_uid, 18)
            storage.update_network_state("bep20", to_block, None)

        except Exception as e:
            logger.error("Ошибка мониторинга BEP20: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


# =============================================================================
# TRC20 (TRON)
# =============================================================================

async def monitor_trc20() -> None:
    logger.info("Мониторинг TRC20 запущен")
    _tron_rpc_alerted = False
    while True:
        try:
            # Проверяем доступность основного TRON RPC
            tron_url = TRON_RPC_URL
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{TRON_RPC_URL}/v1/healthcheck", timeout=10) as resp:
                        if resp.status != 200:
                            raise Exception("Primary TRON RPC unhealthy")
            except Exception:
                logger.warning("TRON RPC недоступен, пробуем fallback...")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"{TRON_RPC_FALLBACK}/v1/healthcheck", timeout=10) as resp:
                            if resp.status == 200:
                                tron_url = TRON_RPC_FALLBACK
                                logger.info("TRON переключён на fallback RPC")
                                if not _tron_rpc_alerted:
                                    await notify_owner("⚠️ <b>TRON основной RPC недоступен</b>\nПереключён на fallback RPC.")
                                    _tron_rpc_alerted = True
                            else:
                                raise Exception("Fallback unhealthy")
                except Exception:
                    logger.error("TRON fallback RPC тоже недоступен")
                    if not _tron_rpc_alerted:
                        await notify_owner("🚨 <b>TRON основной и fallback RPC недоступны!</b>\nМониторинг приостановлен.")
                        _tron_rpc_alerted = True
                    await asyncio.sleep(MONITOR_INTERVAL)
                    continue
            else:
                if _tron_rpc_alerted:
                    await notify_owner("✅ <b>TRON основной RPC восстановлен</b>")
                    _tron_rpc_alerted = False

            users = storage.get_users()
            if not users:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            for uid, info in users.items():
                address = info.get("trc20_address", "")
                if not address:
                    continue
                await _monitor_tron_native(address, uid, info.get("username", ""), tron_url)
                await _monitor_tron_token(address, uid, info.get("username", ""), TRON_USDT, "USDT", 6, tron_url)
                await _monitor_tron_token(address, uid, info.get("username", ""), TRON_USDC, "USDC", 6, tron_url)
        except Exception as e:
            logger.error("Ошибка мониторинга TRC20: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


async def _monitor_tron_native(address: str, uid: str, username: str, tron_url: str = TRON_RPC_URL) -> None:
    url = f"{tron_url}/v1/accounts/{address}/transactions"
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
                               contract_addr: str, token_name: str, decimals: int, tron_url: str = TRON_RPC_URL) -> None:
    if not contract_addr:
        return
    url = f"{tron_url}/v1/accounts/{address}/transactions/trc20"
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
