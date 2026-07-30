#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вывод средств с WITHDRAW_ADDRESS на адрес пользователя.
"""

from web3 import Web3

from config import (
    BSC_USDT, BSC_USDC, ERC20_ABI, ETH_USDT, ETH_USDC,
    WITHDRAW_PRIVATE_KEY, logger, w3_bsc, w3_eth,
)
from eth_account import Account


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
