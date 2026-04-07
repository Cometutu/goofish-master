"""
商品分析分发器
将卖家资料采集、图片下载、AI 分析和结果保存移出主抓取链路。
"""
import asyncio
import copy
import json
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from src.domain.models.task import HotnessConfig
from src.keyword_rule_engine import build_search_text, evaluate_keyword_rules
from src.services.hotness_evaluator import (
    HotnessRates,
    calc_hotness_rates,
    calc_minutes_since_publish,
    evaluate_hotness,
    format_hotness_pending_reason,
    format_hotness_reason,
    _safe_number,
)


SellerLoader = Callable[[str], Awaitable[dict]]
ImageDownloader = Callable[[str, list[str], str], Awaitable[list[str]]]
AIAnalyzer = Callable[[dict, list[str], str], Awaitable[Optional[dict]]]
Notifier = Callable[[dict, str], Awaitable[None]]
Saver = Callable[[dict, str], Awaitable[bool]]
WatchlistAdder = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class ItemAnalysisJob:
    keyword: str
    task_name: str
    decision_mode: str
    analyze_images: bool
    prompt_text: str
    keyword_rules: tuple[str, ...]
    final_record: dict
    seller_id: Optional[str]
    zhima_credit_text: Optional[str]
    registration_duration_text: str
    hotness_config: Optional[HotnessConfig] = field(default=None)


class ItemAnalysisDispatcher:
    """用受控并发处理商品分析和落盘。"""

    def __init__(
        self,
        *,
        concurrency: int,
        skip_ai_analysis: bool,
        seller_loader: SellerLoader,
        image_downloader: ImageDownloader,
        ai_analyzer: AIAnalyzer,
        notifier: Notifier,
        saver: Saver,
        watchlist_adder: Optional[WatchlistAdder] = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._skip_ai_analysis = skip_ai_analysis
        self._seller_loader = seller_loader
        self._image_downloader = image_downloader
        self._ai_analyzer = ai_analyzer
        self._notifier = notifier
        self._saver = saver
        self._watchlist_adder = watchlist_adder
        self._tasks: set[asyncio.Task] = set()
        self.completed_count = 0

    def submit(self, job: ItemAnalysisJob) -> None:
        task = asyncio.create_task(self._process_with_limit(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def join(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def _process_with_limit(self, job: ItemAnalysisJob) -> None:
        async with self._semaphore:
            await self._process_job(job)

    async def _process_job(self, job: ItemAnalysisJob) -> None:
        record = copy.deepcopy(job.final_record)
        item_data = record.get("商品信息", {}) or {}
        record["卖家信息"] = await self._load_seller_info(job)
        record["ai_analysis"] = await self._build_analysis_result(job, record)
        if await self._saver(record, job.keyword):
            self.completed_count += 1

        analysis = record["ai_analysis"]

        # 热度模式: 未达标时加入 watchlist
        if (
            job.decision_mode == "hotness"
            and not analysis.get("is_recommended")
            and analysis.get("analysis_source") == "hotness_pending"
            and self._watchlist_adder is not None
        ):
            await self._add_to_watchlist(job, record, analysis)

        await self._notify_if_recommended(item_data, analysis)

    async def _load_seller_info(self, job: ItemAnalysisJob) -> dict:
        seller_info = {}
        if job.seller_id:
            try:
                seller_info = await self._seller_loader(job.seller_id)
            except Exception as exc:
                print(f"   [卖家] 采集卖家 {job.seller_id} 信息失败: {exc}")
        merged = copy.deepcopy(seller_info or {})
        merged["卖家芝麻信用"] = job.zhima_credit_text
        merged["卖家注册时长"] = job.registration_duration_text
        return merged

    async def _build_analysis_result(self, job: ItemAnalysisJob, record: dict) -> dict:
        if job.decision_mode == "keyword":
            return self._build_keyword_result(job, record)
        if job.decision_mode == "hotness":
            return self._build_hotness_result(job, record)
        if self._skip_ai_analysis:
            return self._build_skip_ai_result()
        return await self._run_ai_analysis(job, record)

    def _build_keyword_result(self, job: ItemAnalysisJob, record: dict) -> dict:
        search_text = build_search_text(record)
        return evaluate_keyword_rules(list(job.keyword_rules), search_text)

    def _build_hotness_result(self, job: ItemAnalysisJob, record: dict) -> dict:
        """计算热度速率并判断是否达标"""
        item = record.get("商品信息", {}) or {}
        config = job.hotness_config

        if config is None:
            return {
                "analysis_source": "hotness",
                "is_recommended": False,
                "reason": "热度模式未配置阈值参数",
                "keyword_hit_count": 0,
            }

        publish_time = item.get("发布时间")
        crawl_time = record.get("爬取时间", "")
        publish_ts = item.get("发布时间戳")

        minutes = calc_minutes_since_publish(
            publish_time, crawl_time, publish_timestamp_ms=publish_ts
        )

        want_cnt = _safe_number(item.get("\u201c想要\u201d人数", 0))
        browse_cnt = _safe_number(item.get("浏览量", 0))
        collect_cnt = _safe_number(item.get("收藏数", 0))

        rates = calc_hotness_rates(
            want_cnt=want_cnt,
            browse_cnt=browse_cnt,
            collect_cnt=collect_cnt,
            minutes_since_publish=minutes,
        )

        is_hot = evaluate_hotness(rates, config)

        rates_dict = {
            "collect_per_minute": rates.collect_per_minute,
            "want_per_minute": rates.want_per_minute,
            "browse_per_minute": rates.browse_per_minute,
            "minutes_since_publish": rates.minutes_since_publish,
        }

        if is_hot:
            return {
                "analysis_source": "hotness",
                "is_recommended": True,
                "reason": format_hotness_reason(rates),
                "keyword_hit_count": 0,
                "hotness_rates": rates_dict,
            }
        else:
            return {
                "analysis_source": "hotness_pending",
                "is_recommended": False,
                "reason": format_hotness_pending_reason(rates),
                "keyword_hit_count": 0,
                "hotness_rates": rates_dict,
            }

    async def _add_to_watchlist(
        self, job: ItemAnalysisJob, record: dict, analysis: dict
    ) -> None:
        """将热度未达标的商品加入热度监测列表"""
        if self._watchlist_adder is None:
            return
        item = record.get("商品信息", {}) or {}
        try:
            await self._watchlist_adder(
                keyword=job.keyword,
                task_name=job.task_name,
                item_id=str(item.get("商品ID", "")),
                link=str(item.get("商品链接", "")),
                link_unique_key=(str(item.get("商品链接", "")).split("&", 1)[0]),
                title=item.get("商品标题"),
                price_display=item.get("当前售价"),
                publish_time=item.get("发布时间"),
                publish_timestamp=item.get("发布时间戳"),
                crawl_time=record.get("爬取时间", ""),
                want_cnt=_safe_number(item.get("\u201c想要\u201d人数", 0)),
                browse_cnt=_safe_number(item.get("浏览量", 0)),
                collect_cnt=_safe_number(item.get("收藏数", 0)),
                raw_item_json=json.dumps(item, ensure_ascii=False),
            )
            print(f"   [热度] 商品 '{str(item.get('商品标题', ''))[:20]}...' 已加入热度监测列表")
        except Exception as exc:
            print(f"   [热度] 加入监测列表失败: {exc}")

    def _build_skip_ai_result(self) -> dict:
        return {
            "analysis_source": "ai",
            "is_recommended": True,
            "reason": "商品已跳过AI分析，直接通知",
            "keyword_hit_count": 0,
        }

    def _build_ai_error_result(self, reason: str, *, error: str = "") -> dict:
        payload = {
            "analysis_source": "ai",
            "is_recommended": False,
            "reason": reason,
            "keyword_hit_count": 0,
        }
        if error:
            payload["error"] = error
        return payload

    async def _run_ai_analysis(self, job: ItemAnalysisJob, record: dict) -> dict:
        image_paths: list[str] = []
        try:
            image_paths = await self._download_images(job, record)
            if not job.prompt_text:
                return self._build_ai_error_result("任务未配置AI prompt，跳过分析。")
            ai_result = await self._ai_analyzer(record, image_paths, job.prompt_text)
            if not ai_result:
                return self._build_ai_error_result(
                    "AI analysis returned None after retries.",
                    error="AI analysis returned None after retries.",
                )
            ai_result.setdefault("analysis_source", "ai")
            ai_result.setdefault("keyword_hit_count", 0)
            return ai_result
        except Exception as exc:
            return self._build_ai_error_result(
                f"AI分析异常: {exc}",
                error=str(exc),
            )
        finally:
            self._cleanup_images(image_paths)

    async def _download_images(self, job: ItemAnalysisJob, record: dict) -> list[str]:
        if not job.analyze_images:
            return []
        item_data = record.get("商品信息", {}) or {}
        image_urls = item_data.get("商品图片列表", [])
        if not image_urls:
            return []
        return await self._image_downloader(
            item_data["商品ID"],
            image_urls,
            job.task_name,
        )

    def _cleanup_images(self, image_paths: list[str]) -> None:
        for img_path in image_paths:
            try:
                if os.path.exists(img_path):
                    os.remove(img_path)
            except Exception as exc:
                print(f"   [图片] 删除图片文件时出错: {exc}")

    async def _notify_if_recommended(self, item_data: dict, analysis_result: dict) -> None:
        if not analysis_result.get("is_recommended"):
            return
        try:
            await self._notifier(item_data, analysis_result.get("reason", "无"))
        except Exception as exc:
            print(f"   [通知] 发送推荐通知失败: {exc}")
