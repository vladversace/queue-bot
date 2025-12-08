import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "queue.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            max_positions INTEGER DEFAULT 30,
            subgroup INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Миграция: добавить subgroup если нет
    cursor.execute("PRAGMA table_info(events)")
    columns = [col[1] for col in cursor.fetchall()]
    if "subgroup" not in columns:
        cursor.execute("ALTER TABLE events ADD COLUMN subgroup INTEGER DEFAULT 0")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(id),
            UNIQUE(event_id, position),
            UNIQUE(event_id, user_id)
        )
    """)
    
    conn.commit()
    conn.close()


def add_event(name: str, max_positions: int = 30, subgroup: int = 0) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO events (name, max_positions, subgroup) VALUES (?, ?, ?)",
            (name, max_positions, subgroup)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_events() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events ORDER BY name")
    events = cursor.fetchall()
    conn.close()
    return events


def find_event_by_keyword(keyword: str) -> Optional[dict]:
    """Find event by partial name match"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM events WHERE LOWER(name) LIKE ? ORDER BY created_at DESC",
        (f"%{keyword.lower()}%",)
    )
    event = cursor.fetchone()
    conn.close()
    return dict(event) if event else None


def get_event_by_id(event_id: int) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    event = cursor.fetchone()
    conn.close()
    return dict(event) if event else None


def delete_event(event_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM queue WHERE event_id = ?", (event_id,))
    cursor.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def rename_event(event_id: int, new_name: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE events SET name = ? WHERE id = ?",
            (new_name, event_id)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def register_position(event_id: int, position: int, user_id: int, 
                      username: str, first_name: str) -> tuple[bool, str]:
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if event exists
    cursor.execute("SELECT max_positions FROM events WHERE id = ?", (event_id,))
    event = cursor.fetchone()
    if not event:
        conn.close()
        return False, "Событие не найдено"
    
    max_pos = event["max_positions"]
    if position < 1 or position > max_pos:
        conn.close()
        return False, f"Позиция должна быть от 1 до {max_pos}"
    
    # Check if position is taken
    cursor.execute(
        "SELECT user_id, username, first_name FROM queue WHERE event_id = ? AND position = ?",
        (event_id, position)
    )
    existing = cursor.fetchone()
    if existing:
        name = existing["first_name"] or existing["username"] or f"ID:{existing['user_id']}"
        conn.close()
        return False, f"Позиция {position} уже занята ({name})"
    
    # Check if user already registered for this event
    cursor.execute(
        "SELECT position FROM queue WHERE event_id = ? AND user_id = ?",
        (event_id, user_id)
    )
    user_pos = cursor.fetchone()
    if user_pos:
        conn.close()
        return False, f"Ты уже записан на позицию {user_pos['position']}"
    
    try:
        cursor.execute(
            """INSERT INTO queue (event_id, position, user_id, username, first_name)
               VALUES (?, ?, ?, ?, ?)""",
            (event_id, position, user_id, username, first_name)
        )
        conn.commit()
        return True, f"Записан на позицию {position}"
    except sqlite3.IntegrityError:
        return False, "Ошибка записи"
    finally:
        conn.close()


def cancel_registration(event_id: int, user_id: int) -> tuple[bool, str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM queue WHERE event_id = ? AND user_id = ?",
        (event_id, user_id)
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    if deleted:
        return True, "Запись отменена"
    return False, "Ты не записан на это событие"


def get_queue(event_id: int) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT position, user_id, username, first_name, registered_at
           FROM queue WHERE event_id = ? ORDER BY position""",
        (event_id,)
    )
    queue = cursor.fetchall()
    conn.close()
    return queue


def get_all_data() -> dict:
    """Get all events with their queues for dashboard"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM events ORDER BY name")
    events = cursor.fetchall()
    
    result = {}
    for event in events:
        event_dict = dict(event)
        cursor.execute(
            """SELECT position, user_id, username, first_name, registered_at
               FROM queue WHERE event_id = ? ORDER BY position""",
            (event["id"],)
        )
        event_dict["queue"] = [dict(q) for q in cursor.fetchall()]
        result[event["id"]] = event_dict
    
    conn.close()
    return result
