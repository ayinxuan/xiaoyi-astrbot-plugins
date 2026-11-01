import asyncio
import logging
import subprocess

from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.astr_message_event import AstrMessageEvent

logger = logging.getLogger(__name__)


@register(
    "astrbot_plugin_restart",
    "小伊",
    "重启AstrBot机器人",
    "1.0.0",
)
class RestartPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

    @filter.command("重启")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def restart_bot(self, event: AstrMessageEvent):
        yield event.plain_result("收到~ 正在重启机器人喵 🔄")

        # 等一小会让消息发出去
        await asyncio.sleep(1)

        try:
            subprocess.Popen(
                ["systemctl", "restart", "astrbot"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"重启失败: {e}")
            yield event.plain_result(f"重启失败喵: {e}")
