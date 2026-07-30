#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite-хранилище: пользователи, депозиты, выводы, балансы, комиссии, состояние сетей.
"""

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import DB_FILE


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
