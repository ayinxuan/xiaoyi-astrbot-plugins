import os
from pathlib import Path
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register(
    "astrbot_plugin_sponsor",
    "小伊",
    "赞助小伊，返回可爱图片",
    "1.0.0",
)
class SponsorPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        # 图片路径
        self.image_path = str(Path(__file__).parent / "sponsor_img.jpg")
        self._ensure_image()

    def _ensure_image(self):
        """确保图片存在"""
        src = "/root/data/workspaces/_FriendMessage_1491867183/cute_catgirl.jpg"
        if os.path.exists(src) and not os.path.exists(self.image_path):
            import shutil
            shutil.copy2(src, self.image_path)
            logger.info(f"已复制赞助图片到 {self.image_path}")

    @filter.command("赞助小伊")
    async def sponsor(self, event):
        """赞助小伊，返回可爱图片"""
        self._ensure_image()
        if os.path.exists(self.image_path):
            yield event.image_result(self.image_path)
            yield event.plain_result("谢谢小可爱的赞助～喵喵！")
        else:
            yield event.plain_result("呜哇，图片不见了喵(´;ω;｀)")
