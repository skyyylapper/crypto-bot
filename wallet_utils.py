#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генерация HD-кошельков (BIP39/BIP32) и получение аккаунтов из мнемоники.
"""

from typing import Dict

from bip32 import BIP32
from eth_account import Account
from mnemonic import Mnemonic
from tronpy.keys import PrivateKey


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
