"""
测试 ItemAnalysisDispatcher 的热度监测模式。
覆盖：达标推荐、未达标 pending、无配置 fallback、watchlist 联动、通知触发。
"""
import asyncio

from src.domain.models.task import HotnessConfig
from src.services.item_analysis_dispatcher import (
    ItemAnalysisDispatcher,
    ItemAnalysisJob,
)


# ─── 公用 helper ─────────────────────────────────────────────────────────────


def _make_dispatcher(
    *,
    saver_records: list | None = None,
    notifications: list | None = None,
    watchlist_calls: list | None = None,
):
    """构建一个 dispatcher，所有外部依赖全部用 fake 替换。"""
    _saver_records = saver_records if saver_records is not None else []
    _notifications = notifications if notifications is not None else []
    _watchlist_calls = watchlist_calls if watchlist_calls is not None else []

    async def seller_loader(user_id: str):
        return {"卖家ID": user_id}

    async def image_downloader(product_id, image_urls, task_name):
        raise AssertionError("热度模式不应下载图片")

    async def ai_analyzer(record, image_paths, prompt_text):
        raise AssertionError("热度模式不应调用 AI 分析")

    async def notifier(item_data: dict, reason: str):
        _notifications.append((item_data, reason))

    async def saver(record: dict, keyword: str):
        _saver_records.append(record)
        return True

    async def watchlist_adder(**kwargs):
        _watchlist_calls.append(kwargs)

    return ItemAnalysisDispatcher(
        concurrency=1,
        skip_ai_analysis=False,
        seller_loader=seller_loader,
        image_downloader=image_downloader,
        ai_analyzer=ai_analyzer,
        notifier=notifier,
        saver=saver,
        watchlist_adder=watchlist_adder,
    )


def _make_job(
    *,
    hotness_config=None,
    want=0,
    browse=0,
    collect=0,
    publish_time="2026-01-01T12:00:00",
    crawl_time="2026-01-01T12:10:00",
    item_id="item-001",
):
    """构建一个热度模式的 ItemAnalysisJob。"""
    return ItemAnalysisJob(
        keyword="测试关键词",
        task_name="热度测试任务",
        decision_mode="hotness",
        analyze_images=False,
        prompt_text="",
        keyword_rules=(),
        final_record={
            "爬取时间": crawl_time,
            "商品信息": {
                "商品ID": item_id,
                "商品标题": f"测试商品-{item_id}",
                "商品链接": f"https://example.com/item/{item_id}&abc=1",
                "当前售价": "99.00",
                "发布时间": publish_time,
                "发布时间戳": None,
                "\u201c想要\u201d人数": want,
                "浏览量": browse,
                "收藏数": collect,
                "商品图片列表": [],
            },
            "卖家信息": {},
        },
        seller_id="seller-001",
        zhima_credit_text="优秀",
        registration_duration_text="来闲鱼2年",
        hotness_config=hotness_config,
    )


# ─── 热度达标 → 推荐 ────────────────────────────────────────────────────────


def test_hotness_mode_recommends_when_threshold_met():
    """商品热度达标时应产出 analysis_source='hotness' + is_recommended=True"""
    saved = []
    notified = []
    watchlist = []

    config = HotnessConfig(
        collect_per_minute=0.01,
        want_per_minute=None,
        browse_per_minute=None,
        condition_logic="or",
    )

    # 10 分钟内 5 次收藏 → 0.5/分钟 >> 阈值 0.01
    job = _make_job(
        hotness_config=config,
        collect=5,
        want=0,
        browse=0,
        publish_time="2026-01-01T12:00:00",
        crawl_time="2026-01-01T12:10:00",
    )

    async def run():
        d = _make_dispatcher(
            saver_records=saved,
            notifications=notified,
            watchlist_calls=watchlist,
        )
        d.submit(job)
        await d.join()
        return d

    dispatcher = asyncio.run(run())

    assert dispatcher.completed_count == 1
    assert len(saved) == 1

    analysis = saved[0]["ai_analysis"]
    assert analysis["analysis_source"] == "hotness"
    assert analysis["is_recommended"] is True
    assert "🔥" in analysis["reason"]
    assert "hotness_rates" in analysis
    assert analysis["hotness_rates"]["collect_per_minute"] > 0

    # 达标应触发通知
    assert len(notified) == 1

    # 达标不应加入 watchlist
    assert len(watchlist) == 0


# ─── 热度未达标 → pending + watchlist ────────────────────────────────────────


def test_hotness_mode_pending_when_below_threshold():
    """商品热度未达标时应返回 hotness_pending 并加入 watchlist"""
    saved = []
    notified = []
    watchlist = []

    config = HotnessConfig(
        collect_per_minute=10.0,  # 极高阈值，不可能达标
        want_per_minute=10.0,
        browse_per_minute=100.0,
        condition_logic="and",
    )

    job = _make_job(
        hotness_config=config,
        collect=1,
        want=1,
        browse=10,
    )

    async def run():
        d = _make_dispatcher(
            saver_records=saved,
            notifications=notified,
            watchlist_calls=watchlist,
        )
        d.submit(job)
        await d.join()
        return d

    dispatcher = asyncio.run(run())

    assert dispatcher.completed_count == 1
    analysis = saved[0]["ai_analysis"]
    assert analysis["analysis_source"] == "hotness_pending"
    assert analysis["is_recommended"] is False
    assert "未达标" in analysis["reason"]
    assert "hotness_rates" in analysis

    # 未达标不应触发通知
    assert len(notified) == 0

    # 未达标应加入 watchlist
    assert len(watchlist) == 1
    wl_call = watchlist[0]
    assert wl_call["keyword"] == "测试关键词"
    assert wl_call["task_name"] == "热度测试任务"
    assert wl_call["item_id"] == "item-001"


# ─── 热度配置缺失 → 安全降级 ────────────────────────────────────────────────


def test_hotness_mode_without_config_returns_not_recommended():
    """hotness_config 为 None 时应安全返回不推荐"""
    saved = []
    watchlist = []

    job = _make_job(hotness_config=None, collect=999, want=999, browse=999)

    async def run():
        d = _make_dispatcher(saver_records=saved, watchlist_calls=watchlist)
        d.submit(job)
        await d.join()

    asyncio.run(run())

    analysis = saved[0]["ai_analysis"]
    assert analysis["analysis_source"] == "hotness"
    assert analysis["is_recommended"] is False
    assert "未配置" in analysis["reason"]
    # 无配置时不应加入 watchlist (analysis_source != 'hotness_pending')
    assert len(watchlist) == 0


# ─── OR 模式下单一阈值突破 ───────────────────────────────────────────────────


def test_hotness_or_mode_single_threshold_triggers():
    """OR 模式下只有浏览量达标也应推荐"""
    saved = []

    config = HotnessConfig(
        collect_per_minute=1.0,  # 不达标
        want_per_minute=1.0,  # 不达标
        browse_per_minute=0.5,  # 将达标
        condition_logic="or",
    )

    # 10 分钟内 100 次浏览 → 10/分 >> 阈值 0.5
    job = _make_job(
        hotness_config=config,
        collect=0,
        want=0,
        browse=100,
    )

    async def run():
        d = _make_dispatcher(saver_records=saved)
        d.submit(job)
        await d.join()

    asyncio.run(run())

    assert saved[0]["ai_analysis"]["is_recommended"] is True
    assert saved[0]["ai_analysis"]["analysis_source"] == "hotness"


# ─── AND 模式下需全部达标 ────────────────────────────────────────────────────


def test_hotness_and_mode_requires_all_thresholds():
    """AND 模式下需全部已配置的阈值均达标"""
    saved = []

    config = HotnessConfig(
        collect_per_minute=0.01,
        want_per_minute=0.01,
        browse_per_minute=0.5,
        condition_logic="and",
    )

    # 收藏和想要达标但浏览不达标
    job = _make_job(
        hotness_config=config,
        collect=5,  # 0.5/分 → 达标
        want=5,  # 0.5/分 → 达标
        browse=1,  # 0.1/分 → 不达标 (阈值 0.5)
    )

    async def run():
        d = _make_dispatcher(saver_records=saved)
        d.submit(job)
        await d.join()

    asyncio.run(run())

    assert saved[0]["ai_analysis"]["is_recommended"] is False
    assert saved[0]["ai_analysis"]["analysis_source"] == "hotness_pending"


def test_hotness_and_mode_all_met():
    """AND 模式下全部达标应推荐"""
    saved = []

    config = HotnessConfig(
        collect_per_minute=0.01,
        want_per_minute=0.01,
        browse_per_minute=0.1,
        condition_logic="and",
    )

    job = _make_job(
        hotness_config=config,
        collect=5,
        want=5,
        browse=50,
    )

    async def run():
        d = _make_dispatcher(saver_records=saved)
        d.submit(job)
        await d.join()

    asyncio.run(run())

    assert saved[0]["ai_analysis"]["is_recommended"] is True
    assert saved[0]["ai_analysis"]["analysis_source"] == "hotness"


# ─── 无 watchlist_adder 时 pending 不崩溃 ───────────────────────────────────


def test_hotness_pending_without_watchlist_adder_does_not_crash():
    """即使没有 watchlist_adder 回调，pending 也不应崩溃"""
    saved = []

    config = HotnessConfig(
        collect_per_minute=100.0,  # 极高阈值
        condition_logic="or",
    )

    job = _make_job(hotness_config=config, collect=0)

    async def seller_loader(uid):
        return {}

    async def image_downloader(pid, urls, tn):
        return []

    async def ai_analyzer(rec, paths, prompt):
        return {}

    async def notifier(item, reason):
        pass

    async def saver(rec, kw):
        saved.append(rec)
        return True

    async def run():
        d = ItemAnalysisDispatcher(
            concurrency=1,
            skip_ai_analysis=False,
            seller_loader=seller_loader,
            image_downloader=image_downloader,
            ai_analyzer=ai_analyzer,
            notifier=notifier,
            saver=saver,
            watchlist_adder=None,  # 不提供
        )
        d.submit(job)
        await d.join()

    asyncio.run(run())

    assert saved[0]["ai_analysis"]["analysis_source"] == "hotness_pending"
    assert saved[0]["ai_analysis"]["is_recommended"] is False


# ─── 多商品并发处理 ──────────────────────────────────────────────────────────


def test_hotness_mode_concurrent_items():
    """多个商品并发提交时应正确计算各自的热度"""
    saved = []
    watchlist = []

    config = HotnessConfig(
        collect_per_minute=0.1,
        condition_logic="or",
    )

    # 商品 A: 收藏高 → 达标
    job_a = _make_job(
        hotness_config=config,
        item_id="A",
        collect=50,
        want=0,
        browse=0,
    )

    # 商品 B: 收藏低 → 未达标
    job_b = _make_job(
        hotness_config=config,
        item_id="B",
        collect=0,
        want=0,
        browse=0,
    )

    async def run():
        d = _make_dispatcher(saver_records=saved, watchlist_calls=watchlist)
        d.submit(job_a)
        d.submit(job_b)
        await d.join()
        return d

    dispatcher = asyncio.run(run())

    assert dispatcher.completed_count == 2
    assert len(saved) == 2

    # 找到各自的 analysis
    analyses = {
        r["商品信息"]["商品ID"]: r["ai_analysis"]
        for r in saved
    }

    assert analyses["A"]["is_recommended"] is True
    assert analyses["A"]["analysis_source"] == "hotness"

    assert analyses["B"]["is_recommended"] is False
    assert analyses["B"]["analysis_source"] == "hotness_pending"

    # 只有 B 加入 watchlist
    assert len(watchlist) == 1
    assert watchlist[0]["item_id"] == "B"


# ─── hotness_rates 字段完整性 ────────────────────────────────────────────────


def test_hotness_rates_dict_present_and_correct():
    """返回结果中应包含 hotness_rates 字段且数值正确"""
    saved = []

    config = HotnessConfig(
        collect_per_minute=0.01,
        condition_logic="or",
    )

    job = _make_job(
        hotness_config=config,
        collect=20,
        want=10,
        browse=200,
    )

    async def run():
        d = _make_dispatcher(saver_records=saved)
        d.submit(job)
        await d.join()

    asyncio.run(run())

    rates = saved[0]["ai_analysis"]["hotness_rates"]
    assert "collect_per_minute" in rates
    assert "want_per_minute" in rates
    assert "browse_per_minute" in rates
    assert "minutes_since_publish" in rates

    # 10 分钟内的数据
    assert rates["minutes_since_publish"] >= 1.0
    assert rates["collect_per_minute"] > 0
    assert rates["want_per_minute"] > 0
    assert rates["browse_per_minute"] > 0


# ─── 卖家信息正确合并 ────────────────────────────────────────────────────────


def test_hotness_mode_seller_info_merged():
    """热度模式下卖家信息应正确合并到记录中"""
    saved = []

    config = HotnessConfig(
        collect_per_minute=0.01,
        condition_logic="or",
    )

    job = _make_job(hotness_config=config, collect=5)

    async def run():
        d = _make_dispatcher(saver_records=saved)
        d.submit(job)
        await d.join()

    asyncio.run(run())

    seller = saved[0]["卖家信息"]
    assert seller["卖家芝麻信用"] == "优秀"
    assert seller["卖家注册时长"] == "来闲鱼2年"
    assert seller["卖家ID"] == "seller-001"
