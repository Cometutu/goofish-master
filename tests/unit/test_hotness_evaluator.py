"""
热度评估引擎 (hotness_evaluator) 的单元测试。
覆盖：速率计算、阈值判断（or/and 逻辑）、过期检测、时间差计算、格式化输出、安全数值转换。
"""
from datetime import datetime, timedelta

from src.domain.models.task import HotnessConfig
from src.services.hotness_evaluator import (
    HotnessRates,
    _safe_number,
    calc_hotness_rates,
    calc_minutes_since_publish,
    evaluate_hotness,
    format_hotness_pending_reason,
    format_hotness_reason,
    is_watch_expired,
)


# ─── _safe_number ────────────────────────────────────────────────────────────


class TestSafeNumber:
    def test_integer_passthrough(self):
        assert _safe_number(42) == 42

    def test_float_truncated_to_int(self):
        assert _safe_number(3.9) == 3

    def test_string_number(self):
        assert _safe_number("100") == 100

    def test_string_with_comma(self):
        assert _safe_number("1,234") == 1234

    def test_none_returns_default(self):
        assert _safe_number(None) == 0
        assert _safe_number(None, default=5) == 5

    def test_empty_string(self):
        assert _safe_number("") == 0

    def test_dash(self):
        assert _safe_number("-") == 0

    def test_nan_string(self):
        assert _safe_number("NaN") == 0

    def test_na_string(self):
        assert _safe_number("N/A") == 0

    def test_invalid_string(self):
        assert _safe_number("abc") == 0


# ─── calc_minutes_since_publish ──────────────────────────────────────────────


class TestCalcMinutesSincePublish:
    def test_with_millisecond_timestamp(self):
        """使用毫秒级时间戳时应计算正确时间差"""
        now_ms = int(datetime.now().timestamp() * 1000)
        ten_min_ago_ms = now_ms - 10 * 60 * 1000
        result = calc_minutes_since_publish(
            None,
            datetime.now().isoformat(),
            publish_timestamp_ms=ten_min_ago_ms,
        )
        assert 9.5 <= result <= 11.0

    def test_with_iso_publish_time(self):
        """使用 ISO 格式发布时间"""
        crawl = datetime(2026, 1, 1, 12, 30, 0)
        publish = datetime(2026, 1, 1, 12, 0, 0)
        result = calc_minutes_since_publish(
            publish.isoformat(),
            crawl.isoformat(),
        )
        assert abs(result - 30.0) < 0.1

    def test_missing_publish_time_uses_crawl_time(self):
        """发布时间缺失时应回退到爬取时间，返回最小值 1.0"""
        crawl = datetime.now().isoformat()
        result = calc_minutes_since_publish(None, crawl)
        assert result == 1.0

    def test_minimum_one_minute(self):
        """时间差过小时至少返回 1 分钟"""
        same_time = datetime(2026, 1, 1, 12, 0, 0).isoformat()
        result = calc_minutes_since_publish(same_time, same_time)
        assert result == 1.0

    def test_future_publish_time_returns_one_minute(self):
        """发布时间在爬取时间之后时返回 1 分钟"""
        crawl = datetime(2026, 1, 1, 12, 0, 0)
        publish = datetime(2026, 1, 1, 13, 0, 0)
        result = calc_minutes_since_publish(
            publish.isoformat(),
            crawl.isoformat(),
        )
        assert result == 1.0


# ─── calc_hotness_rates ──────────────────────────────────────────────────────


class TestCalcHotnessRates:
    def test_basic_rate_calculation(self):
        rates = calc_hotness_rates(
            want_cnt=10,
            browse_cnt=100,
            collect_cnt=5,
            minutes_since_publish=10.0,
        )
        assert rates.want_per_minute == 1.0
        assert rates.browse_per_minute == 10.0
        assert rates.collect_per_minute == 0.5
        assert rates.minutes_since_publish == 10.0

    def test_zero_minutes_treated_as_one(self):
        """分钟数 <= 0 时应被修正为 1.0"""
        rates = calc_hotness_rates(
            want_cnt=5,
            browse_cnt=50,
            collect_cnt=2,
            minutes_since_publish=0,
        )
        assert rates.want_per_minute == 5.0
        assert rates.browse_per_minute == 50.0
        assert rates.collect_per_minute == 2.0

    def test_zero_counts(self):
        """所有计数为 0 时速率应为 0"""
        rates = calc_hotness_rates(
            want_cnt=0,
            browse_cnt=0,
            collect_cnt=0,
            minutes_since_publish=60.0,
        )
        assert rates.want_per_minute == 0.0
        assert rates.browse_per_minute == 0.0
        assert rates.collect_per_minute == 0.0

    def test_large_numbers(self):
        rates = calc_hotness_rates(
            want_cnt=10000,
            browse_cnt=500000,
            collect_cnt=3000,
            minutes_since_publish=1440.0,  # 24h
        )
        assert abs(rates.want_per_minute - 10000 / 1440) < 0.01
        assert abs(rates.browse_per_minute - 500000 / 1440) < 0.1


# ─── evaluate_hotness ────────────────────────────────────────────────────────


class TestEvaluateHotness:
    """测试热度阈值判断逻辑"""

    def _make_rates(self, *, collect=0.0, want=0.0, browse=0.0, minutes=60.0):
        return HotnessRates(
            collect_per_minute=collect,
            want_per_minute=want,
            browse_per_minute=browse,
            minutes_since_publish=minutes,
        )

    # --- OR 模式 ---

    def test_or_mode_single_threshold_met(self):
        """OR 模式：任一阈值达标即为 True"""
        config = HotnessConfig(
            collect_per_minute=0.01,
            want_per_minute=0.05,
            browse_per_minute=1.0,
            condition_logic="or",
        )
        # 只有收藏达标
        rates = self._make_rates(collect=0.02, want=0.01, browse=0.5)
        assert evaluate_hotness(rates, config) is True

    def test_or_mode_none_met(self):
        """OR 模式：全部未达标"""
        config = HotnessConfig(
            collect_per_minute=0.1,
            want_per_minute=0.1,
            browse_per_minute=5.0,
            condition_logic="or",
        )
        rates = self._make_rates(collect=0.01, want=0.01, browse=1.0)
        assert evaluate_hotness(rates, config) is False

    def test_or_mode_all_met(self):
        """OR 模式：全部达标"""
        config = HotnessConfig(
            collect_per_minute=0.01,
            want_per_minute=0.01,
            browse_per_minute=0.1,
            condition_logic="or",
        )
        rates = self._make_rates(collect=0.1, want=0.1, browse=1.0)
        assert evaluate_hotness(rates, config) is True

    def test_or_mode_partial_config(self):
        """OR 模式：只配置部分阈值"""
        config = HotnessConfig(
            collect_per_minute=0.01,
            want_per_minute=None,  # 未配置
            browse_per_minute=None,  # 未配置
            condition_logic="or",
        )
        rates = self._make_rates(collect=0.02)
        assert evaluate_hotness(rates, config) is True

    # --- AND 模式 ---

    def test_and_mode_all_met(self):
        """AND 模式：全部达标"""
        config = HotnessConfig(
            collect_per_minute=0.01,
            want_per_minute=0.01,
            browse_per_minute=0.1,
            condition_logic="and",
        )
        rates = self._make_rates(collect=0.1, want=0.1, browse=1.0)
        assert evaluate_hotness(rates, config) is True

    def test_and_mode_partial_met(self):
        """AND 模式：部分达标 → False"""
        config = HotnessConfig(
            collect_per_minute=0.01,
            want_per_minute=0.1,
            browse_per_minute=5.0,
            condition_logic="and",
        )
        rates = self._make_rates(collect=0.1, want=0.01, browse=10.0)
        assert evaluate_hotness(rates, config) is False

    def test_and_mode_with_partial_config(self):
        """AND 模式：只配置部分阈值，全部配置的都达标"""
        config = HotnessConfig(
            collect_per_minute=0.01,
            want_per_minute=None,
            browse_per_minute=None,
            condition_logic="and",
        )
        rates = self._make_rates(collect=0.02)
        assert evaluate_hotness(rates, config) is True

    # --- 边界情况 ---

    def test_no_thresholds_configured(self):
        """无任何阈值配置时应返回 False"""
        config = HotnessConfig(
            collect_per_minute=None,
            want_per_minute=None,
            browse_per_minute=None,
        )
        rates = self._make_rates(collect=100, want=100, browse=100)
        assert evaluate_hotness(rates, config) is False

    def test_exact_threshold_boundary(self):
        """速率等于阈值时应视为达标 (>=)"""
        config = HotnessConfig(
            collect_per_minute=0.05,
            condition_logic="or",
        )
        rates = self._make_rates(collect=0.05)
        assert evaluate_hotness(rates, config) is True

    def test_just_below_threshold(self):
        """速率略低于阈值时应返回 False"""
        config = HotnessConfig(
            collect_per_minute=0.05,
            condition_logic="or",
        )
        rates = self._make_rates(collect=0.049)
        assert evaluate_hotness(rates, config) is False


# ─── is_watch_expired ────────────────────────────────────────────────────────


class TestIsWatchExpired:
    def test_not_expired_within_limits(self):
        config = HotnessConfig(
            max_check_count=10,
            max_monitor_hours=24.0,
        )
        result = is_watch_expired(
            first_crawl_time=datetime.now().isoformat(),
            check_count=3,
            config=config,
        )
        assert result is False

    def test_expired_by_check_count(self):
        config = HotnessConfig(max_check_count=5)
        result = is_watch_expired(
            first_crawl_time=datetime.now().isoformat(),
            check_count=5,
            config=config,
        )
        assert result is True

    def test_expired_by_monitor_hours(self):
        config = HotnessConfig(max_monitor_hours=1.0)
        two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
        result = is_watch_expired(
            first_crawl_time=two_hours_ago,
            check_count=1,
            config=config,
        )
        assert result is True

    def test_no_limits_configured_never_expires(self):
        """不配置上限时永不过期"""
        config = HotnessConfig()
        result = is_watch_expired(
            first_crawl_time=(datetime.now() - timedelta(days=30)).isoformat(),
            check_count=9999,
            config=config,
        )
        assert result is False

    def test_invalid_crawl_time_does_not_crash(self):
        """无效的时间格式不应导致异常"""
        config = HotnessConfig(max_monitor_hours=1.0)
        result = is_watch_expired(
            first_crawl_time="invalid-time",
            check_count=0,
            config=config,
        )
        assert result is False


# ─── format_hotness_reason ───────────────────────────────────────────────────


class TestFormatHotnessReason:
    def test_contains_fire_emoji(self):
        rates = HotnessRates(
            collect_per_minute=0.05,
            want_per_minute=0.03,
            browse_per_minute=2.5,
            minutes_since_publish=120.0,
        )
        reason = format_hotness_reason(rates)
        assert "🔥" in reason
        assert "热度达标" in reason
        assert "小时" in reason

    def test_short_time_uses_minutes(self):
        rates = HotnessRates(
            collect_per_minute=0.1,
            want_per_minute=0.05,
            browse_per_minute=5.0,
            minutes_since_publish=30.0,
        )
        reason = format_hotness_reason(rates)
        assert "分钟" in reason

    def test_pending_reason_mentions_monitoring(self):
        rates = HotnessRates(
            collect_per_minute=0.001,
            want_per_minute=0.0,
            browse_per_minute=0.5,
            minutes_since_publish=60.0,
        )
        reason = format_hotness_pending_reason(rates)
        assert "未达标" in reason
        assert "监测" in reason
