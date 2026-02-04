"""
KabaGuard - Async Database Module
Handles all database operations for user registration and attendance tracking.
"""

import aiosqlite
from datetime import date
from typing import Optional, List, Dict, Any
import os


class Database:
    """Async SQLite database handler for KabaGuard bot."""
    
    def __init__(self, db_path: str = "kabaguard.db"):
        self.db_path = db_path
    
    async def init_db(self) -> None:
        """Initialize database and create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    kaba_id TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    working_days TEXT NOT NULL,
                    last_morning_success_date TEXT,
                    last_morning_alert_date TEXT,
                    last_evening_success_date TEXT,
                    last_evening_alert_date TEXT
                )
            """)
            await db.commit()
    
    async def register_user(
        self,
        user_id: int,
        kaba_id: str,
        start_time: str,
        end_time: str,
        working_days: str
    ) -> None:
        """
        Register or update a user in the database.
        
        Args:
            user_id: Telegram user ID
            kaba_id: Company portal Kaba ID
            start_time: Shift start time in HH:MM format
            end_time: Shift end time in HH:MM format
            working_days: Comma-separated day indices (e.g., "0,1,2,3,4" for Mon-Fri)
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO users (user_id, kaba_id, start_time, end_time, working_days)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    kaba_id=excluded.kaba_id,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    working_days=excluded.working_days
            """, (user_id, kaba_id, start_time, end_time, working_days))
            await db.commit()
    
    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get user details by Telegram user ID.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Dictionary with user data or None if not found
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None
    
    async def get_all_active_users(self) -> List[Dict[str, Any]]:
        """
        Get all registered users for the scheduler.
        
        Returns:
            List of dictionaries containing user data
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def update_morning_success(self, user_id: int, success_date: date) -> None:
        """Update the last morning success date for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_morning_success_date = ? WHERE user_id = ?",
                (success_date.isoformat(), user_id)
            )
            await db.commit()
    
    async def update_morning_alert(self, user_id: int, alert_date: date) -> None:
        """Update the last morning alert date for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_morning_alert_date = ? WHERE user_id = ?",
                (alert_date.isoformat(), user_id)
            )
            await db.commit()
    
    async def update_evening_success(self, user_id: int, success_date: date) -> None:
        """Update the last evening success date for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_evening_success_date = ? WHERE user_id = ?",
                (success_date.isoformat(), user_id)
            )
            await db.commit()
    
    async def update_evening_alert(self, user_id: int, alert_date: date) -> None:
        """Update the last evening alert date for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_evening_alert_date = ? WHERE user_id = ?",
                (alert_date.isoformat(), user_id)
            )
            await db.commit()
