#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автосбор депозитов: перевод средств с адресов пользователей на COLLECT_* адреса.
"""

from web3 import Web3

from config import (
    BSC_USDT, BSC_USDC, COLLECT_BEP20, COLLECT_EVM, ERC20_ABI,
    ETH_USDT, ETH_USDC, fernet, logger, w3_bsc, w3_eth,
)
from db import storage
from wallet_utils import get_account_from_mnemonic


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
