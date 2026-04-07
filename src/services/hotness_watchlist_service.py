"""
热度监测列表 (hotness_watchlist) 的 SQLite 读写服务。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_connection import sqlite_connection


@dataclass
class WatchlistItem:
    """热度监测列表中的一条记录"""

    id: int
    keyword: str
    task_name: str
    item_id: str
    link: str
    link_unique_key: str
    title: Optional[str]
    price_display: Optional[str]
    publish_time: Optional[str]
    publish_timestamp: Optional[int]
    first_crawl_time: str
    last_check_time: str
    check_count: int
    last_want_cnt: int
    last_browse_cnt: int
    last_collect_cnt: int
    status: str
    raw_item_json: Optional[str]


def _row_to_item(row) -> WatchlistItem:
    return WatchlistItem(**dict(row))


# ---------------------------------------------------------------------------
# Public API (async wrappers)
# ---------------------------------------------------------------------------


async def load_watching_items(keyword: str, *, limit: int = 50) -> list[WatchlistItem]:
    """加载指定关键词下所有 status='watching' 的待巡检商品。"""
    return await asyncio.to_thread(_load_watching_items_sync, keyword, limit)


async def add_to_watchlist(
    *,
    keyword: str,
    task_name: str,
    item_id: str,
    link: str,
    link_unique_key: str,
    title: str | None = None,
    price_display: str | None = None,
    publish_time: str | None = None,
    publish_timestamp: int | None = None,
    crawl_time: str,
    want_cnt: int = 0,
    browse_cnt: int = 0,
    collect_cnt: int = 0,
    raw_item_json: str | None = None,
) -> None:
    """将商品加入热度监测列表。如果已存在 (keyword, item_id) 会被忽略。"""
    await asyncio.to_thread(
        _add_to_watchlist_sync,
        keyword=keyword,
        task_name=task_name,
        item_id=item_id,
        link=link,
        link_unique_key=link_unique_key,
        title=title,
        price_display=price_display,
        publish_time=publish_time,
        publish_timestamp=publish_timestamp,
        crawl_time=crawl_time,
        want_cnt=want_cnt,
        browse_cnt=browse_cnt,
        collect_cnt=collect_cnt,
        raw_item_json=raw_item_json,
    )


async def update_watchlist_check(
    item_id_in_db: int,
    *,
    want_cnt: int,
    browse_cnt: int,
    collect_cnt: int,
) -> None:
    """巡检后更新监测记录的计数和检查次数。"""
    await asyncio.to_thread(
        _update_check_sync, item_id_in_db, want_cnt, browse_cnt, collect_cnt
    )


async def mark_watchlist_status(item_id_in_db: int, status: str) -> None:
    """将监测记录标记为 triggered / expired。"""
    await asyncio.to_thread(_mark_status_sync, item_id_in_db, status)


async def cleanup_expired_watchlist(keyword: str) -> int:
    """清理指定关键词下已过期/已触发的记录，返回删除条数。"""
    return await asyncio.to_thread(_cleanup_sync, keyword)


# ---------------------------------------------------------------------------
# Sync implementations
# ---------------------------------------------------------------------------


def _load_watching_items_sync(keyword: str, limit: int) -> list[WatchlistItem]:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM hotness_watchlist
            WHERE keyword = ? AND status = 'watching'
            ORDER BY last_check_time ASC
            LIMIT ?
            """,
            (keyword, limit),
        ).fetchall()
    return [_row_to_item(row) for row in rows]


def _add_to_watchlist_sync(
    *,
    keyword: str,
    task_name: str,
    item_id: str,
    link: str,
    link_unique_key: str,
    title: str | None,
    price_display: str | None,
    publish_time: str | None,
    publish_timestamp: int | None,
    crawl_time: str,
    want_cnt: int,
    browse_cnt: int,
    collect_cnt: int,
    raw_item_json: str | None,
) -> None:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO hotness_watchlist (
                keyword, task_name, item_id, link, link_unique_key,
                title, price_display, publish_time, publish_timestamp,
                first_crawl_time, last_check_time, check_count,
                last_want_cnt, last_browse_cnt, last_collect_cnt,
                status, raw_item_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'watching', ?)
            """,
            (
                keyword,
                task_name,
                item_id,
                link,
                link_unique_key,
                title,
                price_display,
                publish_time,
                publish_timestamp,
                crawl_time,
                crawl_time,
                want_cnt,
                browse_cnt,
                collect_cnt,
                raw_item_json,
            ),
        )
        conn.commit()


def _update_check_sync(
    item_id_in_db: int,
    want_cnt: int,
    browse_cnt: int,
    collect_cnt: int,
) -> None:
    bootstrap_sqlite_storage()
    now = datetime.now().isoformat()
    with sqlite_connection() as conn:
        conn.execute(
            """
            UPDATE hotness_watchlist
            SET check_count = check_count + 1,
                last_check_time = ?,
                last_want_cnt = ?,
                last_browse_cnt = ?,
                last_collect_cnt = ?
            WHERE id = ?
            """,
            (now, want_cnt, browse_cnt, collect_cnt, item_id_in_db),
        )
        conn.commit()


def _mark_status_sync(item_id_in_db: int, status: str) -> None:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        conn.execute(
            "UPDATE hotness_watchlist SET status = ? WHERE id = ?",
            (status, item_id_in_db),
        )
        conn.commit()


def _cleanup_sync(keyword: str) -> int:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM hotness_watchlist
            WHERE keyword = ? AND status IN ('triggered', 'expired')
            """,
            (keyword,),
        )
        conn.commit()
    return int(cursor.rowcount or 0)
