"""
飞书机器人通知客户端
"""
import asyncio
import base64
import hashlib
import hmac
import time
from typing import Dict

import requests

from .base import NotificationClient, NotificationMessage


class FeishuBotClient(NotificationClient):
    """飞书机器人通知客户端"""

    channel_key = "feishu"
    display_name = "飞书"

    def __init__(
        self,
        bot_url: str | None = None,
        bot_secret: str | None = None,
        pcurl_to_mobile: bool = True,
    ):
        super().__init__(enabled=bool(bot_url), pcurl_to_mobile=pcurl_to_mobile)
        self.bot_url = bot_url
        self.bot_secret = bot_secret

    async def send(self, product_data: Dict, reason: str) -> None:
        if not self.is_enabled():
            raise RuntimeError("飞书 未启用")

        payload = self._build_payload(self._build_message(product_data, reason))
        headers = {"Content-Type": "application/json"}
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                self.bot_url,
                json=payload,
                headers=headers,
                timeout=10,
            ),
        )
        response.raise_for_status()
        result = response.json()
        if result.get("code", result.get("StatusCode", 0)) != 0:
            raise RuntimeError(
                result.get("msg")
                or result.get("StatusMessage")
                or "飞书返回未知错误"
            )

    def _build_payload(self, message: NotificationMessage) -> dict:
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": message.notification_title,
                        "content": self._build_post_content(message),
                    }
                }
            },
        }
        if not self.bot_secret:
            return payload
        payload.update(self._build_signature())
        return payload

    def _build_post_content(self, message: NotificationMessage) -> list[list[dict[str, str]]]:
        content = [
            [{"tag": "text", "text": f"商品：{message.title}"}],
            [{"tag": "text", "text": f"价格：{message.price}"}],
            [{"tag": "text", "text": f"原因：{message.reason}"}],
        ]
        if message.mobile_link:
            content.append(self._build_link_paragraph("手机端：", message.mobile_link))
        content.append(self._build_link_paragraph("电脑端：", message.desktop_link))
        return content

    def _build_link_paragraph(self, label: str, href: str) -> list[dict[str, str]]:
        return [
            {"tag": "text", "text": label},
            {"tag": "a", "text": "打开链接", "href": href},
        ]

    def _build_signature(self) -> dict[str, str]:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self.bot_secret}"
        sign = base64.b64encode(
            hmac.new(
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return {"timestamp": timestamp, "sign": sign}
