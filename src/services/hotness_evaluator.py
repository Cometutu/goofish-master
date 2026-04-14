"""
热度评估引擎
计算商品热度速率并判断是否达到阈值。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.domain.models.task import HotnessConfig


@dataclass
class HotnessRates:
    """热度速率数据"""

    collect_per_minute: float = 0.0
    want_per_minute: float = 0.0
    browse_per_minute: float = 0.0
    minutes_since_publish: float = 0.0


def _safe_number(value, default: int = 0) -> int:
    """安全地将值转换为整数"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    try:
        text = str(value).strip().replace(",", "")
        if not text or text in {"-", "NaN", "N/A"}:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _parse_timestamp_ms(value) -> Optional[int]:
    """将发布时间解析为毫秒时间戳"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return int(value)
    # 尝试解析 ISO 格式
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(value, fmt)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
    return None


def calc_minutes_since_publish(
    publish_time_value,
    crawl_time_str: str,
    publish_timestamp_ms: Optional[int] = None,
) -> float:
    """
    计算从发布到爬取的时间差（分钟）。

    优先使用 publish_timestamp_ms（毫秒），然后尝试解析 publish_time_value，
    如果都不可用则用 crawl_time 替代（按用户要求）。
    """
    # 解析爬取时间
    crawl_ts_ms: Optional[int] = None
    if crawl_time_str:
        parsed = _parse_timestamp_ms(crawl_time_str)
        if parsed:
            crawl_ts_ms = parsed
        else:
            # 尝试 ISO 格式
            try:
                crawl_dt = datetime.fromisoformat(crawl_time_str)
                crawl_ts_ms = int(crawl_dt.timestamp() * 1000)
            except (ValueError, TypeError):
                pass

    if crawl_ts_ms is None:
        crawl_ts_ms = int(datetime.now().timestamp() * 1000)

    # 解析发布时间
    pub_ts_ms: Optional[int] = None
    if publish_timestamp_ms and publish_timestamp_ms > 1_000_000_000:
        pub_ts_ms = publish_timestamp_ms
    else:
        pub_ts_ms = _parse_timestamp_ms(publish_time_value)

    if pub_ts_ms is None or pub_ts_ms <= 0:
        # 用户选择：发布时间未知时用首次爬到的时间替代
        pub_ts_ms = crawl_ts_ms

    diff_ms = crawl_ts_ms - pub_ts_ms
    if diff_ms <= 0:
        return 1.0  # 最少1分钟，避免除零

    return max(diff_ms / 60_000.0, 1.0)


def calc_hotness_rates(
    *,
    want_cnt: int,
    browse_cnt: int,
    collect_cnt: int,
    minutes_since_publish: float,
) -> HotnessRates:
    """计算每分钟的热度速率"""
    if minutes_since_publish <= 0:
        minutes_since_publish = 1.0

    return HotnessRates(
        collect_per_minute=collect_cnt / minutes_since_publish,
        want_per_minute=want_cnt / minutes_since_publish,
        browse_per_minute=browse_cnt / minutes_since_publish,
        minutes_since_publish=minutes_since_publish,
    )


def evaluate_hotness(rates: HotnessRates, config: HotnessConfig) -> bool:
    """
    根据配置的阈值和逻辑判断是否达标。

    - condition_logic == "or": 任何一个已配置的阈值被超过即为达标
    - condition_logic == "and": 所有已配置的阈值都被超过才达标
    """
    checks: list[bool] = []

    if config.collect_per_minute is not None:
        checks.append(rates.collect_per_minute >= config.collect_per_minute)

    if config.want_per_minute is not None:
        checks.append(rates.want_per_minute >= config.want_per_minute)

    if config.browse_per_minute is not None:
        checks.append(rates.browse_per_minute >= config.browse_per_minute)

    if not checks:
        # 没有配置任何阈值，不推荐
        return False

    if config.condition_logic == "and":
        return all(checks)

    return any(checks)


def is_watch_expired(
    *,
    first_crawl_time: str,
    check_count: int,
    config: HotnessConfig,
) -> bool:
    """判断监测是否已超过生命周期上限"""
    # 检查最大检查次数
    if config.max_check_count is not None and check_count >= config.max_check_count:
        return True

    # 检查最大监测时长
    if config.max_monitor_hours is not None:
        try:
            first_dt = datetime.fromisoformat(first_crawl_time)
            elapsed_hours = (datetime.now() - first_dt).total_seconds() / 3600
            if elapsed_hours >= config.max_monitor_hours:
                return True
        except (ValueError, TypeError):
            pass

    return False


def format_hotness_reason(rates: HotnessRates) -> str:
    """格式化热度推荐原因"""
    hours = rates.minutes_since_publish / 60
    if hours >= 1:
        time_str = f"{hours:.1f}小时"
    else:
        time_str = f"{rates.minutes_since_publish:.0f}分钟"

    return (
        f"🔥 热度达标! "
        f"收藏 {rates.collect_per_minute:.3f}/分 | "
        f"想要 {rates.want_per_minute:.3f}/分 | "
        f"浏览 {rates.browse_per_minute:.2f}/分 | "
        f"上架 {time_str}"
    )


def format_hotness_pending_reason(rates: HotnessRates) -> str:
    """格式化热度未达标的原因"""
    hours = rates.minutes_since_publish / 60
    if hours >= 1:
        time_str = f"{hours:.1f}小时"
    else:
        time_str = f"{rates.minutes_since_publish:.0f}分钟"

    return (
        f"热度未达标, 已加入监测 — "
        f"收藏 {rates.collect_per_minute:.3f}/分 | "
        f"想要 {rates.want_per_minute:.3f}/分 | "
        f"浏览 {rates.browse_per_minute:.2f}/分 | "
        f"上架 {time_str}"
    )
