import json
import logging
import os
from datetime import datetime, timedelta

from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.astr_message_event import AstrMessageEvent

logger = logging.getLogger(__name__)

DATA_FILE = os.path.join(os.path.dirname(__file__), "sleep_records.json")


def load_records() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载睡眠记录失败: {e}")
        return {}


def save_records(records: dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存睡眠记录失败: {e}")


@register(
    "astrbot_plugin_greeting",
    "小伊",
    "早安晚安消息监视器，记录睡眠时长",
    "1.0.0",
)
class GreetingPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

    @filter.regex(r"^晚安$")
    async def goodnight(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        records = load_records()
        records[user_id] = {
            "sleep_time": now,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
        save_records(records)

        yield event.plain_result("晚安~ 好梦喵 ✨")

    @filter.regex(r"^早安$")
    async def goodmorning(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        now = datetime.now()

        records = load_records()
        record = records.get(user_id)

        if record and "sleep_time" in record:
            try:
                sleep_time = datetime.strptime(record["sleep_time"], "%Y-%m-%d %H:%M:%S")
                diff = now - sleep_time
                if diff > timedelta(hours=24):
                    # 超过24小时，记录失效
                    records.pop(user_id, None)
                    save_records(records)
                    yield event.plain_result("早安喵~ 🌞")
                else:
                    # 计算小时和分钟
                    hours, remainder = divmod(int(diff.total_seconds()), 3600)
                    minutes = remainder // 60
                    records.pop(user_id, None)
                    save_records(records)
                    if hours > 0:
                        yield event.plain_result(f"早安喵~ 你睡了 {hours} 小时 {minutes} 分钟哦 🌞")
                    else:
                        yield event.plain_result(f"早安喵~ 你睡了 {minutes} 分钟哦 🌞")
            except ValueError:
                records.pop(user_id, None)
                save_records(records)
                yield event.plain_result("早安喵~ 🌞")
        else:
            yield event.plain_result("早安喵~ 🌞")
