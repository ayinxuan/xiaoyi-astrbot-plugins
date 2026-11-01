"""QQ空间评说说守卫：接管 astrbot_plugin_qzone 的 prob_read_feed 处理器。

规则：
- 主人（owner_id）发说说：100% 触发评论
- 好友：按 friend_read_prob（默认 0.5）触发，只处理最新一条未评论动态
- 非好友：完全不触发，不读取任何说说
- 每次评论成功后：私聊主人，带上对象昵称和评论内容
"""

from __future__ import annotations

import random
import time
import types

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.star.star_handler import star_handlers_registry

FRIEND_CACHE_TTL = 600
GUARD_ATTR = "_qzone_guard_plugin"


async def _get_friend_ids(guard: "QzoneGuardPlugin | None") -> set[str]:
    """取好友 QQ 列表，缓存 10 分钟"""
    if guard is None:
        return set()

    now = time.time()
    cached_at, cached = guard.friend_cache
    if cached_at and now - cached_at < FRIEND_CACHE_TTL:
        return cached

    client = guard.qzone_client
    ids: set[str] = set()
    if client is not None:
        try:
            friends = await client.get_friend_list()
            for f in friends or []:
                uid = f.get("user_id") if isinstance(f, dict) else None
                if uid:
                    ids.add(str(uid))
        except Exception as e:
            logger.warning(f"[qzone_guard] 获取好友列表失败，本轮不触发: {e}")
            return set()

    guard.friend_cache = (now, ids)
    return ids


async def _notify_owner(client, owner_id: str, post) -> None:
    """评论后私聊通知主人"""
    if not owner_id.isdigit() or client is None:
        return
    content = post.comments[-1].content if post.comments else ""
    text = f"刚在 {post.name} 的说说下面评论了喵：{content}"
    try:
        await client.send_private_msg(user_id=int(owner_id), message=text)
    except Exception as e:
        logger.warning(f"[qzone_guard] 通知主人失败: {e}")


async def _patched_prob_read_feed(self, event):
    """替换 qzone 插件原本的随机评说说逻辑"""
    guard = getattr(self, GUARD_ATTR, None)
    cfg = self.cfg

    if not cfg.client:
        cfg.client = event.bot
        logger.debug("[qzone_guard] QQ空间所需的 CQHttp 客户端已初始化")
    if guard is not None and guard.qzone_client is None:
        guard.qzone_client = cfg.client

    sender_id = str(event.get_sender_id())
    if cfg.source.is_ignore_user(sender_id):
        return

    owner_id = str(guard.conf("owner_id", "")).strip() if guard else ""
    is_owner = bool(owner_id) and sender_id == owner_id

    if is_owner:
        prob = 1.0
    else:
        if sender_id not in await _get_friend_ids(guard):
            return
        prob = float(guard.conf("friend_read_prob", 0.5)) if guard else 0.5

    if random.random() >= prob:
        return

    try:
        posts = await self.service.query_feeds(
            target_id=sender_id, pos=0, num=1, no_self=True, no_commented=True
        )
    except Exception as e:
        logger.warning(f"[qzone_guard] 读取说说失败: {e}")
        return

    for post in posts:
        try:
            await self.service.comment_posts(post, event=event)
        except Exception as e:
            logger.error(f"[qzone_guard] 评论说说失败: {e}")
            continue

        if cfg.trigger.like_when_comment:
            try:
                await self.service.like_posts(post)
            except Exception as e:
                logger.warning(f"[qzone_guard] 点赞失败: {e}")

        notify = guard.conf("notify_owner_on_comment", True) if guard else True
        if notify or is_owner:
            await _notify_owner(cfg.client, owner_id, post)


class QzoneGuardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.friend_cache: tuple[float, set[str]] = (0.0, set())
        self.qzone_client = None

    def conf(self, key: str, default):
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        if value is None or value == "":
            return default
        return value

    async def initialize(self):
        self._install()

    @filter.on_astrbot_loaded()
    async def _on_astrbot_loaded(self):
        self._install()

    def _install(self) -> None:
        patched = 0
        for handler in list(star_handlers_registry):
            if handler.handler_name != "prob_read_feed":
                continue
            if "astrbot_plugin_qzone" not in (handler.handler_module_path or ""):
                continue
            instance = getattr(handler.handler, "__self__", None)
            if instance is None:
                continue

            setattr(instance, GUARD_ATTR, self)
            if self.qzone_client is None:
                try:
                    self.qzone_client = instance.cfg.client
                except Exception:
                    pass
            handler.handler = types.MethodType(_patched_prob_read_feed, instance)
            patched += 1

        if patched:
            logger.info(f"[qzone_guard] 已接管 {patched} 个 prob_read_feed 处理器")
        else:
            logger.warning("[qzone_guard] 未找到 qzone 的 prob_read_feed，等待下次加载重试")

    async def terminate(self):
        """卸载时尽量把原逻辑还原（去掉 patch 标记即可）"""
        for handler in list(star_handlers_registry):
            if handler.handler_name == "prob_read_feed" and "astrbot_plugin_qzone" in (
                handler.handler_module_path or ""
            ):
                instance = getattr(handler.handler, "__self__", None)
                if instance is not None and hasattr(instance, GUARD_ATTR):
                    delattr(instance, GUARD_ATTR)
