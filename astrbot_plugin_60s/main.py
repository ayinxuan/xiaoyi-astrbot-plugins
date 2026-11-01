"""
60s API 全量聚合插件 for AstrBot
==================================
基于 60s API (https://github.com/vikiboss/60s) 提供的全部开放接口
支持 50+ 命令 + AI Function Calling 工具调用
"""

import json
import asyncio
import os
import traceback
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import aiohttp
from astrbot.api import logger
from astrbot.api.all import (
    AstrMessageEvent,
    CommandResult,
    Context,
    Plain,
    Image,
    register,
    Star,
    llm_tool,
)
from astrbot.api.event.filter import command

API_BASE = "https://60s.viki.moe"

# ===================== 自定义实例配置 =====================
_INSTANCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_instance.txt")


def _load_custom_instance() -> str | None:
    """从配置文件加载自定义 API 实例地址"""
    if os.path.exists(_INSTANCE_FILE):
        try:
            with open(_INSTANCE_FILE, "r") as f:
                url = f.read().strip()
                if url:
                    return url
        except Exception:
            pass
    return None


def _save_custom_instance(url: str):
    """保存自定义 API 实例地址到配置文件"""
    with open(_INSTANCE_FILE, "w") as f:
        f.write(url.strip())


# 启动时尝试加载自定义实例（会被 __init__ 中的 WebUI 配置覆盖）
_custom_url = _load_custom_instance()
if _custom_url:
    API_BASE = _custom_url

# ===================== HTTP Client =====================
_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _session


async def call_api(endpoint: str, params: dict = None, encoding: str = "json") -> Optional[dict]:
    url = f"{API_BASE}{endpoint}"
    if params is None:
        params = {}
    params["encoding"] = encoding
    try:
        session = await _get_session()
        async with session.get(url, params=params) as resp:
            if encoding == "json":
                return await resp.json()
            else:
                text = await resp.text()
                return {"code": 200, "data": text}
    except Exception as e:
        logger.error(f"60s API 调用失败 [{endpoint}]: {traceback.format_exc()}")
        return None


# ===================== Formatters =====================
def fmt_hot_list(data_list: list, top_n: int = 15, use_rank: bool = True) -> str:
    lines = []
    for i, item in enumerate(data_list[:top_n], 1):
        title = item.get("title", "") or item.get("name", "")
        hot = item.get("hot_value", "") or item.get("score", "")
        if isinstance(hot, (int, float)):
            if hot > 100000000:
                hot_str = f"{hot/100000000:.1f}亿"
            elif hot > 10000:
                hot_str = f"{hot/10000:.0f}万"
            else:
                hot_str = str(int(hot))
        else:
            hot_str = str(hot) if hot else ""

        if not hot_str:
            score_desc = item.get("score_desc", "") or item.get("hot_value_desc", "")
            hot_str = str(score_desc) if score_desc else ""

        hot_part = f" 🔥{hot_str}" if hot_str else ""
        emoji_map = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
                      6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}
        idx = emoji_map.get(i, f"{i}.")
        lines.append(f"{idx} {title}{hot_part}")
    return "\n".join(lines)


def fmt_hot_list_with_link(data_list: list, top_n: int = 10) -> str:
    lines = []
    for i, item in enumerate(data_list[:top_n], 1):
        title = item.get("title", "")
        score = item.get("score", "")
        author = item.get("author", "")
        link = item.get("link", "")
        lines.append(f"{i}. **{title}** (↑{score} by {author})")
        if link:
            lines.append(f"   {link}")
    return "\n".join(lines)


def fmt_hn(data_list: list) -> str:
    return f"💻 **Hacker News 热帖**\n\n{fmt_hot_list_with_link(data_list)}"


def fmt_60s(data: dict) -> str:
    date = data.get("date", "")
    news = data.get("news", [])
    tip = data.get("tip", "")
    day = data.get("day_of_week", "")
    lunar = data.get("lunar_date", "")
    lines = [f"📰 **每天60秒读懂世界** | {date} {day} {lunar}\n"]
    for i, n in enumerate(news[:15], 1):
        lines.append(f"{i}. {n}")
    if tip:
        lines.append(f"\n💭 {tip}")
    return "\n".join(lines)


def fmt_ai_news(data: dict) -> str:
    date = data.get("date", "")
    news = data.get("news", [])
    if not news:
        return f"🤖 **AI资讯快报** | {date}\n\n今日暂无重大AI资讯"
    lines = [f"🤖 **AI资讯快报** | {date}\n"]
    for i, n in enumerate(news[:10], 1):
        lines.append(f"{i}. **{n.get('title', '')}**")
        detail = (n.get('detail', '') or "")[:120]
        lines.append(f"   {detail}...")
        lines.append("")
    return "\n".join(lines)


def fmt_it_news(data_list: list) -> str:
    lines = ["💻 **实时IT资讯 (IT之家)**\n"]
    for i, n in enumerate(data_list[:10], 1):
        title = n.get("title", "")
        desc = (n.get("description", "") or "")[:100]
        lines.append(f"{i}. **{title}**")
        if desc:
            lines.append(f"   {desc}...")
        lines.append("")
    return "\n".join(lines)


def fmt_weather(data: dict) -> str:
    loc = data.get("location", {})
    w = data.get("weather", {})
    air = data.get("air_quality", {})
    sunrise = data.get("sunrise", {})
    alerts = data.get("alerts", [])
    lines = [
        f"🌤 **{loc.get('name', '未知')} 实时天气**",
        f"🌡 温度: {w.get('temperature', 'N/A')}°C | 天气: {w.get('condition', 'N/A')}",
        f"💧 湿度: {w.get('humidity', 'N/A')}% | 风力: {w.get('wind_direction', '')} {w.get('wind_power', '')}",
        f"🌬 AQI: {air.get('aqi', 'N/A')} ({air.get('quality', 'N/A')})",
        f"🌅 日出: {sunrise.get('sunrise_desc', 'N/A')} | 日落: {sunrise.get('sunset_desc', 'N/A')}",
    ]
    for a in alerts[:1]:
        lines.append(f"⚠ {a.get('type', '')}{a.get('level', '')}预警")
    return "\n".join(lines)


def fmt_forecast(data: dict) -> str:
    loc = data.get("location", {}).get("name", "")
    daily = data.get("daily_forecast", [])
    lines = [f"📅 **{loc} 天气预报**\n"]
    for d in daily[:7]:
        date = d.get("date", "")
        day_c = d.get("day_condition", "")
        night_c = d.get("night_condition", "")
        hi = d.get("max_temperature", "")
        lo = d.get("min_temperature", "")
        aqi = d.get("air_quality", "")
        lines.append(f"📌 {date}: {day_c}→{night_c} | {lo}°C~{hi}°C | 空气:{aqi}")
    return "\n".join(lines)


def fmt_gold(data: dict) -> str:
    metals = data.get("metals", [])
    stores = data.get("stores", [])
    lines = ["🏅 **实时黄金价格**\n"]
    for m in metals[:5]:
        lines.append(f"• {m.get('name', '')}: {m.get('sell_price', '')} {m.get('unit', '')}")
    lines.append("\n💍 **品牌金价**")
    for s in stores[:6]:
        lines.append(f"• {s.get('brand', '')}: {s.get('formatted', '')}")
    return "\n".join(lines)


def fmt_fuel(data: dict) -> str:
    region = data.get("region", "")
    items = data.get("items", [])
    trend = data.get("trend", {})
    lines = [f"⛽ **{region} 汽油价格**\n"]
    for item in items:
        lines.append(f"• {item.get('name', '')}: {item.get('price_desc', '')}")
    if trend:
        lines.append(f"\n📈 {trend.get('description', '')}")
    return "\n".join(lines)


def fmt_olympics(data: dict) -> str:
    event = data.get("event_name", "")
    updated = data.get("updated", "")
    lst = data.get("list", [])
    lines = [f"🏅 **{event} 奖牌榜** ({updated})\n"]
    lines.append("|排|国家/地区|🥇金|🥈银|🥉铜|总|")
    lines.append("|--|--------|--|--|--|--|")
    for item in lst[:15]:
        lines.append(f"|{item.get('rank','')}|{item.get('country','')}|{item.get('gold',0)}|{item.get('silver',0)}|{item.get('bronze',0)}|{item.get('total',0)}|")
    return "\n".join(lines)


def fmt_history(data: dict) -> str:
    date = data.get("date", "")
    items = data.get("items", [])
    lines = [f"📅 **历史上的今天** ({date})\n"]
    emojis = {"birth": "🎂", "death": "🕯", "event": "📌"}
    for item in items[:10]:
        lines.append(f"{emojis.get(item.get('event_type',''),'•')} **{item.get('year','')}年** {item.get('title','')}")
    return "\n".join(lines)


def fmt_lyric(data: dict) -> str:
    title = data.get("title", "")
    artists = "、".join(data.get("artists", []))
    formatted = data.get("formatted", "")
    lines = [f"🎵 **{title}** - {artists}\n"]
    text = formatted[:800]
    if len(formatted) > 800:
        text += "\n...(歌词过长已截断)"
    lines.append(text)
    return "\n".join(lines)


def fmt_exchange(data: dict) -> str:
    base = data.get("base_code", "CNY")
    updated = data.get("updated", "")
    rates = data.get("rates", [])
    common = {"USD": "美元", "EUR": "欧元", "GBP": "英镑", "JPY": "日元",
              "KRW": "韩元", "HKD": "港币", "TWD": "新台币", "AUD": "澳元",
              "CAD": "加元", "SGD": "新加坡元", "THB": "泰铢", "RUB": "卢布"}
    lines = [f"💱 **汇率** (1{base}=) | {updated}\n"]
    for r in rates:
        cur = r.get("currency", "")
        if cur in common:
            lines.append(f"• {common[cur]}({cur}): {r.get('rate',0):.4f}")
    return "\n".join(lines)


def fmt_baike(data: dict) -> str:
    name = data.get("name", "")
    desc = (data.get("description", "") or "")[:500]
    link = data.get("link", "")
    lines = [f"📚 **{name}**\n", desc]
    if link:
        lines.append(f"\n🔗 {link}")
    return "\n".join(lines)


def fmt_moyu(data: dict) -> str:
    date_info = data.get("date", {})
    progress = data.get("progress", {})
    today = data.get("today", {})
    next_holiday = data.get("nextHoliday", {})
    quote = data.get("moyuQuote", "")
    lines = [
        f"🐟 **摸鱼日报** | {date_info.get('gregorian','')} {date_info.get('weekday','')}",
        "",
        f"📊 本周: {progress.get('week',{}).get('percentage',0)}% | 本月: {progress.get('month',{}).get('percentage',0)}% | 本年: {progress.get('year',{}).get('percentage',0)}%",
        f"🛑 距元旦: {progress.get('year',{}).get('remaining','?')}天",
    ]
    if next_holiday:
        lines.append(f"🎉 下个节假日: {next_holiday.get('name','')} ({next_holiday.get('until','?')}天后)")
    if today.get("isHoliday"):
        lines.append("🌟 今天是节假日！")
    if quote:
        lines.append(f"\n💬 {quote}")
    return "\n".join(lines)


def fmt_simple(data: dict, key: str, header: str) -> str:
    return f"{header}\n\n{data.get(key, '')}"


def fmt_bing(data: dict) -> str:
    lines = [f"🖼 **必应每日壁纸**\n📌 {data.get('title','')}"]
    desc = (data.get("description", "") or "")[:200]
    if desc:
        lines.append(f"\n{desc}")
    return "\n".join(lines)


def fmt_ip(data: dict) -> str:
    return f"🌐 **公网IP**\n`{data.get('ip','N/A')}`"


def fmt_epic(data: dict) -> str:
    games = data.get("games", data if isinstance(data, list) else [])
    if not games:
        games = data.get("data", [])
    if isinstance(games, dict):
        games = games.get("games", [])
    lines = ["🎮 **Epic Games 本周免费游戏**\n"]
    if isinstance(games, list):
        for g in games[:5]:
            lines.append(f"• {g.get('title','')} {g.get('description','')[:80] if g.get('description') else ''}")
    return "\n".join(lines)


# ===================== Tool Definitions for AI =====================
def get_llm_tools() -> List[Dict[str, Any]]:
    """返回所有可供 AI 调用的工具定义"""
    return [
        {"type": "function", "function": {
            "name": "get_60s_news", "description": "获取每天60秒读懂世界新闻简报，包含15条当日要闻和一句话小贴士",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_ai_news", "description": "获取最新AI/大模型领域资讯快报，注意：并非每天都有新闻",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_bing_wallpaper", "description": "获取必应每日壁纸和描述",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_exchange_rate", "description": "获取当日人民币对主要货币汇率",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_today_in_history", "description": "获取今天在历史上发生的重要事件",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_it_news", "description": "获取IT之家实时科技资讯",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_olympics_medal", "description": "获取奥运奖牌榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_gold_price", "description": "获取实时黄金价格和品牌金价",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_fuel_price", "description": "获取指定地区的汽油价格",
            "parameters": {"type": "object", "properties": {"region": {"type": "string", "description": "地区名，如北京、上海、成都"}}, "required": ["region"]}}},
        {"type": "function", "function": {
            "name": "get_weather", "description": "获取指定城市的实时天气、AQI、日出日落等信息",
            "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "城市名，如北京、上海、深圳"}}, "required": ["city"]}}},
        {"type": "function", "function": {
            "name": "get_weather_forecast", "description": "获取指定城市未来7天天气预报",
            "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "城市名"}}, "required": ["city"]}}},
        {"type": "function", "function": {
            "name": "get_moyu", "description": "获取摸鱼日报，包含日期进度、节假日倒计时、摸鱼语录",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "search_lyric", "description": "搜索歌曲歌词",
            "parameters": {"type": "object", "properties": {"title": {"type": "string", "description": "歌曲名称或歌手"}}, "required": ["title"]}}},
        {"type": "function", "function": {
            "name": "search_baike", "description": "搜索百度百科词条",
            "parameters": {"type": "object", "properties": {"word": {"type": "string", "description": "词条名称"}}, "required": ["word"]}}},
        {"type": "function", "function": {
            "name": "translate_text", "description": "在线翻译文本，支持109种语言自动检测",
            "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "要翻译的文本"}}, "required": ["text"]}}},
        {"type": "function", "function": {
            "name": "get_public_ip", "description": "获取当前公网IP地址",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_douyin_hot", "description": "获取抖音实时热搜榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_bilibili_hot", "description": "获取B站实时热搜榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_weibo_hot", "description": "获取微博实时热搜榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_zhihu_hot", "description": "获取知乎实时话题榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_toutiao_hot", "description": "获取头条实时热搜榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_baidu_hot", "description": "获取百度实时热搜榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_baidu_teleplay", "description": "获取百度电视剧排行榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_rednote_hot", "description": "获取小红书实时热点",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_quark_hot", "description": "获取夸克实时热点",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_ithome_rank", "description": "获取IT之家热门文章排行榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_dongchedi_hot", "description": "获取懂车帝汽车热搜榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_hackernews", "description": "获取Hacker News热帖",
            "parameters": {"type": "object", "properties": {"type": {"type": "string", "description": "类型: top/new/best, 默认top"}}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_ncm_rank_list", "description": "获取网易云音乐榜单列表(飙升榜/热歌榜/新歌榜等)",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_joke", "description": "获取一条随机搞笑段子",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_hitokoto", "description": "获取随机一言（名言）",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_fabing", "description": "生成一条随机的发病文学",
            "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "主角名字"}}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_kfc_text", "description": "获取一条随机KFC疯狂星期四文案",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_dad_joke", "description": "获取一条随机冷笑话",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_fortune", "description": "获取今日随机运势",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_maoyan_box", "description": "获取猫眼电影实时票房",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_douban_movie", "description": "获取豆瓣全球口碑电影榜",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
    ]


# Tool name -> (endpoint, params_from_args, formatter)
TOOL_MAP = {
    "get_60s_news": ("/v2/60s", None, lambda d: fmt_60s(d)),
    "get_ai_news": ("/v2/ai-news", None, lambda d: fmt_ai_news(d)),
    "get_bing_wallpaper": ("/v2/bing", None, lambda d: fmt_bing(d)),
    "get_exchange_rate": ("/v2/exchange-rate", None, lambda d: fmt_exchange(d)),
    "get_today_in_history": ("/v2/today-in-history", None, lambda d: fmt_history(d)),
    "get_it_news": ("/v2/it-news", None, lambda d: fmt_it_news(d)),
    "get_olympics_medal": ("/v2/olympics", None, lambda d: fmt_olympics(d)),
    "get_gold_price": ("/v2/gold-price", None, lambda d: fmt_gold(d)),
    "get_fuel_price": ("/v2/fuel-price", lambda a: {"region": a.get("region", "北京")}, lambda d: fmt_fuel(d)),
    "get_weather": ("/v2/weather", lambda a: {"query": a.get("city", "北京")}, lambda d: fmt_weather(d)),
    "get_weather_forecast": ("/v2/weather/forecast", lambda a: {"query": a.get("city", "北京"), "days": 7}, lambda d: fmt_forecast(d)),
    "get_moyu": ("/v2/moyu", None, lambda d: fmt_moyu(d)),
    "search_lyric": ("/v2/lyric", lambda a: {"query": a.get("title", "")}, lambda d: fmt_lyric(d)),
    "search_baike": ("/v2/baike", lambda a: {"word": a.get("word", "")}, lambda d: fmt_baike(d)),
    "translate_text": ("/v2/fanyi", lambda a: {"text": a.get("text", ""), "from": "auto", "to": "auto"}, lambda d: f"翻译结果: {d.get('target',{}).get('text','')}"),
    "get_public_ip": ("/v2/ip", None, lambda d: fmt_ip(d)),
    "get_douyin_hot": ("/v2/douyin", None, lambda d: f"🎵 抖音热搜\n{fmt_hot_list(d)}"),
    "get_bilibili_hot": ("/v2/bili", None, lambda d: f"📺 B站热搜\n{fmt_hot_list(d)}"),
    "get_weibo_hot": ("/v2/weibo", None, lambda d: f"🔥 微博热搜\n{fmt_hot_list(d)}"),
    "get_zhihu_hot": ("/v2/zhihu", None, lambda d: f"❓ 知乎话题\n{fmt_hot_list(d)}"),
    "get_toutiao_hot": ("/v2/toutiao", None, lambda d: f"📰 头条热搜\n{fmt_hot_list(d)}"),
    "get_baidu_hot": ("/v2/baidu/hot", None, lambda d: f"🔍 百度热搜\n{fmt_hot_list(d)}"),
    "get_baidu_teleplay": ("/v2/baidu/teleplay", None, lambda d: f"📺 百度电视剧榜\n{fmt_hot_list(d)}"),
    "get_rednote_hot": ("/v2/rednote", None, lambda d: f"📕 小红书热点\n{fmt_hot_list(d)}"),
    "get_quark_hot": ("/v2/quark", None, lambda d: f"🔍 夸克热点\n{fmt_hot_list(d)}"),
    "get_ithome_rank": ("/v2/it-news/rank", None, lambda d: f"💻 IT之家热门\n{fmt_hot_list(d)}"),
    "get_dongchedi_hot": ("/v2/dongchedi", None, lambda d: f"🚗 懂车帝热搜\n{fmt_hot_list(d)}"),
    "get_hackernews": ("/v2/hacker-news/top", None, lambda d: fmt_hn(d)),
    "get_ncm_rank_list": ("/v2/ncm-rank/list", None, lambda d: f"🎶 网易云榜单\n{fmt_hot_list(d)}"),
    "get_joke": ("/v2/duanzi", None, lambda d: f"😂 搞笑段子\n{d.get('duanzi','')}"),
    "get_hitokoto": ("/v2/hitokoto", None, lambda d: f"💬 一言\n「{d.get('hitokoto','')}」"),
    "get_fabing": ("/v2/fabing", lambda a: {"name": a.get("name", "叶子")}, lambda d: f"🤒 发病文学\n{d.get('saying','')}"),
    "get_kfc_text": ("/v2/kfc", None, lambda d: f"🍗 KFC文案\n{d.get('kfc','')}"),
    "get_dad_joke": ("/v2/dad-joke", None, lambda d: f"🥶 冷笑话\n{d.get('content','')}"),
    "get_fortune": ("/v2/fortune", None, lambda d: f"🔮 今日运势: {d}"),
    "get_maoyan_box": ("/v2/maoyan", None, lambda d: f"🎬 猫眼票房\n{fmt_hot_list(d) if isinstance(d, list) else str(d)[:500]}"),
    "get_douban_movie": ("/v2/douban", None, lambda d: f"🎬 豆瓣电影榜\n{fmt_hot_list(d) if isinstance(d, list) else str(d)[:500]}"),
}


async def dispatch_tool(tool_name: str, args: dict) -> str:
    """分派 AI 工具调用并返回格式化结果"""
    if tool_name not in TOOL_MAP:
        return f"未知工具: {tool_name}"
    endpoint, params_fn, fmt_fn = TOOL_MAP[tool_name]
    params = params_fn(args) if params_fn else {}
    result = await call_api(endpoint, params)
    if result and result.get("code") == 200:
        data = result.get("data", {})
        return fmt_fn(data) if data else "获取成功但数据为空"
    return f"调用 {tool_name} 失败"


# ===================== Help Text =====================
HELP_TEXT = """📡 **60s API 全量聚合服务**

🕘 **周期资讯** (7项)
  /60s - 每天60秒读懂世界
  /60s图片 - 60秒读世界(图片)
  /ai - AI资讯快报
  /bing - 必应每日壁纸
  /汇率 - 货币汇率
  /历史 - 历史上的今天
  /it - IT之家实时资讯

🍱 **实用功能** (12项)
  /奥运 - 奥运奖牌榜
  /黄金 - 黄金价格
  /油价 [地区] - 汽油价格
  /天气 [城市] - 实时天气
  /预报 [城市] - 7天天气预报
  /摸鱼 - 摸鱼日报
  /歌词 [歌名] - 歌词搜索
  /百科 [词条] - 百度百科
  /翻译 [文本] - 在线翻译
  /ip - 公网IP
  /二维码 [文本] - 生成二维码
  /epic - Epic免费游戏

🔥 **热门榜单** (12项)
  /抖音 /b站 /微博 /知乎 /头条
  /百度热搜 /电视剧 /懂车帝
  /小红书 /夸克 /ith /hackernews
  /网易云榜单 /豆瓣电影 /猫眼

🎤 **消遣娱乐** (8项)
  /段子 /一言 /发病 [名字]
  /kfc /冷笑话 /运势
  /js题 /唱歌

💡 AI可自动调用以上全部功能

📋 **订阅推送**
  /60s订阅 <功能> <时间> - 订阅定时推送
  /60s退订 <功能> - 退订
  /60s订阅列表 - 查看订阅
  /60s推送 <功能> - 立即推送一次
"""


# ===================== Plugin Class =====================
@register("astrbot_plugin_60s", "ayin", "60s API 全量聚合服务 (50+功能，支持AI调用)", "1.0.0")
class AstrBotPlugin60s(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        # 从 WebUI 配置中读取 API 实例地址（优先级最高）
        if self.config and self.config.get("api_base_url"):
            global API_BASE
            cfg_url = self.config["api_base_url"].strip().rstrip("/")
            if cfg_url:
                API_BASE = cfg_url

    # ---- LLM Tools ----
    def get_llm_tools(self) -> List[Dict[str, Any]]:
        """向 AI 暴露所有可调用的工具"""
        return get_llm_tools()

    async def on_llm_tool_call(self, tool_name: str, args: dict) -> str:
        """AI 调用工具时分派处理"""
        return await dispatch_tool(tool_name, args)

    # ---- Period Info (7) ----
    @command("60s")
    async def cmd_60s(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取中...")
        r = await call_api("/v2/60s")
        yield event.plain_result(fmt_60s(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    @command("60s图片")
    async def cmd_60s_img(self, event: AstrMessageEvent):
        # 先获取真实的图片URL（处理302跳转）
        img_url = f"{API_BASE}/v2/60s?encoding=image"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(img_url, allow_redirects=True) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        # 保存到本地临时文件
                        tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
                        os.makedirs(tmp_dir, exist_ok=True)
                        tmp_path = os.path.join(tmp_dir, "60s_img.png")
                        with open(tmp_path, "wb") as f:
                            f.write(data)
                        yield event.image_result(tmp_path)
                        return
        except Exception as e:
            logger.error(f"60s图片下载失败: {e}")
        # 保底：发链接
        yield event.plain_result(f"图片加载失败，试试直接访问：\n{img_url}")

    @command("ai")
    async def cmd_ai(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取AI资讯...")
        r = await call_api("/v2/ai-news")
        yield event.plain_result(fmt_ai_news(r["data"]) if r and r.get("code") == 200 else "❌ 暂无AI资讯")

    @command("bing")
    async def cmd_bing(self, event: AstrMessageEvent):
        r = await call_api("/v2/bing")
        if r and r.get("code") == 200:
            d = r["data"]
            yield event.plain_result(fmt_bing(d))
            if d.get("cover"):
                yield event.image_result(d["cover"])
        else:
            yield event.plain_result("❌ 失败")

    @command("汇率")
    async def cmd_exchange(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取汇率...")
        r = await call_api("/v2/exchange-rate")
        yield event.plain_result(fmt_exchange(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    @command("历史")
    async def cmd_history(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取中...")
        r = await call_api("/v2/today-in-history")
        yield event.plain_result(fmt_history(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    @command("it")
    async def cmd_it(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取IT资讯...")
        r = await call_api("/v2/it-news")
        yield event.plain_result(fmt_it_news(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    # ---- Utility (12) ----
    @command("奥运")
    async def cmd_olympics(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取奖牌榜...")
        r = await call_api("/v2/olympics")
        yield event.plain_result(fmt_olympics(r["data"]) if r and r.get("code") == 200 else "❌ 暂无数据")

    @command("黄金")
    async def cmd_gold(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取金价...")
        r = await call_api("/v2/gold-price")
        yield event.plain_result(fmt_gold(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    @command("油价")
    async def cmd_fuel(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        region = args[1] if len(args) > 1 else "北京"
        yield event.plain_result(f"⏳ 查询 {region} 油价...")
        r = await call_api("/v2/fuel-price", {"region": region})
        yield event.plain_result(fmt_fuel(r["data"]) if r and r.get("code") == 200 else f"❌ 未找到 {region}")

    @command("天气")
    async def cmd_weather(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        city = args[1] if len(args) > 1 else "北京"
        yield event.plain_result(f"⏳ 查询 {city} 天气...")
        r = await call_api("/v2/weather", {"query": city})
        yield event.plain_result(fmt_weather(r["data"]) if r and r.get("code") == 200 else f"❌ 未找到 {city}")

    @command("预报")
    async def cmd_forecast(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        city = args[1] if len(args) > 1 else "北京"
        yield event.plain_result(f"⏳ 查询 {city} 7天预报...")
        r = await call_api("/v2/weather/forecast", {"query": city, "days": 7})
        if r and r.get("code") == 200:
            yield event.plain_result(fmt_forecast(r["data"]))
        else:
            yield event.plain_result(f"❌ 未找到 {city}")

    @command("摸鱼")
    async def cmd_moyu(self, event: AstrMessageEvent):
        r = await call_api("/v2/moyu")
        yield event.plain_result(fmt_moyu(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    @command("歌词")
    async def cmd_lyric(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("❌ /歌词 [歌名]")
            return
        yield event.plain_result(f"⏳ 搜索: {args[1]}")
        r = await call_api("/v2/lyric", {"query": args[1]})
        yield event.plain_result(fmt_lyric(r["data"]) if r and r.get("code") == 200 else f"❌ 未找到「{args[1]}」")

    @command("百科")
    async def cmd_baike(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("❌ /百科 [词条]")
            return
        yield event.plain_result(f"⏳ 查询: {args[1]}")
        r = await call_api("/v2/baike", {"word": args[1]})
        yield event.plain_result(fmt_baike(r["data"]) if r and r.get("code") == 200 else f"❌ 未找到「{args[1]}」")

    @command("翻译")
    async def cmd_fanyi(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("❌ /翻译 [文本]")
            return
        yield event.plain_result("⏳ 翻译中...")
        r = await call_api("/v2/fanyi", {"text": args[1], "from": "auto", "to": "auto"})
        if r and r.get("code") == 200:
            d = r["data"]
            yield event.plain_result(
                f"🌐 翻译\n原文({d.get('source',{}).get('type_desc','')}): {d.get('source',{}).get('text','')}\n"
                f"译文({d.get('target',{}).get('type_desc','')}): {d.get('target',{}).get('text','')}"
            )
        else:
            yield event.plain_result("❌ 翻译失败")

    @command("ip")
    async def cmd_ip(self, event: AstrMessageEvent):
        r = await call_api("/v2/ip")
        yield event.plain_result(fmt_ip(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    @command("二维码")
    async def cmd_qrcode(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("❌ /二维码 [文本]")
            return
        yield event.plain_result("⏳ 生成中...")
        yield event.image_result(f"{API_BASE}/v2/qrcode?text={args[1]}&encoding=image")

    @command("epic")
    async def cmd_epic(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 获取Epic免费游戏...")
        r = await call_api("/v2/epic")
        if r and r.get("code") == 200:
            yield event.plain_result(fmt_epic(r["data"]))
        else:
            yield event.plain_result("❌ 获取失败")

    # ---- Hot Lists (12) ----
    @command("抖音")
    async def cmd_douyin(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 抖音热搜...")
        r = await call_api("/v2/douyin")
        yield event.plain_result(f"🎵 **抖音热搜**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("b站")
    async def cmd_bili(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ B站热搜...")
        r = await call_api("/v2/bili")
        yield event.plain_result(f"📺 **B站热搜**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("微博")
    async def cmd_weibo(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 微博热搜...")
        r = await call_api("/v2/weibo")
        yield event.plain_result(f"🔥 **微博热搜**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("知乎")
    async def cmd_zhihu(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 知乎话题...")
        r = await call_api("/v2/zhihu")
        yield event.plain_result(f"❓ **知乎话题**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("头条")
    async def cmd_toutiao(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 头条热搜...")
        r = await call_api("/v2/toutiao")
        yield event.plain_result(f"📰 **头条热搜**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("百度热搜")
    async def cmd_baidu_hot(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 百度热搜...")
        r = await call_api("/v2/baidu/hot")
        yield event.plain_result(f"🔍 **百度热搜**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("电视剧")
    async def cmd_baidu_tv(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 电视剧榜...")
        r = await call_api("/v2/baidu/teleplay")
        yield event.plain_result(f"📺 **百度电视剧榜**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("小红书")
    async def cmd_rednote(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 小红书热点...")
        r = await call_api("/v2/rednote")
        yield event.plain_result(f"📕 **小红书热点**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("夸克")
    async def cmd_quark(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 夸克热点...")
        r = await call_api("/v2/quark")
        yield event.plain_result(f"🔍 **夸克热点**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("ith")
    async def cmd_ithome_rank(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ IT之家热门...")
        r = await call_api("/v2/it-news/rank")
        yield event.plain_result(f"💻 **IT之家热门**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("懂车帝")
    async def cmd_dongchedi(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 懂车帝热搜...")
        r = await call_api("/v2/dongchedi")
        yield event.plain_result(f"🚗 **懂车帝热搜**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("hackernews")
    async def cmd_hn(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        hn_type = args[1] if len(args) > 1 else "top"
        yield event.plain_result(f"⏳ HN {hn_type}...")
        r = await call_api(f"/v2/hacker-news/{hn_type}")
        yield event.plain_result(fmt_hn(r["data"]) if r and r.get("code") == 200 else "❌ 失败")

    @command("网易云榜单")
    async def cmd_ncm(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 网易云榜单...")
        r = await call_api("/v2/ncm-rank/list")
        yield event.plain_result(f"🎶 **网易云榜单**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("豆瓣电影")
    async def cmd_douban_movie(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 豆瓣电影榜...")
        r = await call_api("/v2/douban")
        yield event.plain_result(f"🎬 **豆瓣电影**\n\n{fmt_hot_list(r['data'])}" if r and r.get("code") == 200 else "❌ 失败")

    @command("猫眼")
    async def cmd_maoyan(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 猫眼票房...")
        r = await call_api("/v2/maoyan")
        if r and r.get("code") == 200:
            d = r["data"]
            yield event.plain_result(f"🎬 **猫眼票房**\n\n{fmt_hot_list(d) if isinstance(d, list) else str(d)[:500]}")
        else:
            yield event.plain_result("❌ 失败")

    # ---- Entertainment (8) ----
    @command("段子")
    async def cmd_duanzi(self, event: AstrMessageEvent):
        r = await call_api("/v2/duanzi")
        yield event.plain_result(f"😂 段子\n\n{r['data'].get('duanzi','')}" if r and r.get("code") == 200 else "❌ 失败")

    @command("一言")
    async def cmd_hitokoto(self, event: AstrMessageEvent):
        r = await call_api("/v2/hitokoto")
        yield event.plain_result(f"💬 「{r['data'].get('hitokoto','')}」" if r and r.get("code") == 200 else "❌ 失败")

    @command("发病")
    async def cmd_fabing(self, event: AstrMessageEvent):
        args = event.get_message_str().strip().split(maxsplit=1)
        name = args[1] if len(args) > 1 else "叶子"
        r = await call_api("/v2/fabing", {"name": name})
        yield event.plain_result(f"🤒 发病文学\n\n{r['data'].get('saying','')}" if r and r.get("code") == 200 else "❌ 失败")

    @command("kfc")
    async def cmd_kfc(self, event: AstrMessageEvent):
        r = await call_api("/v2/kfc")
        yield event.plain_result(f"🍗 KFC:\n{r['data'].get('kfc','')}" if r and r.get("code") == 200 else "❌ 失败")

    @command("冷笑话")
    async def cmd_dadjoke(self, event: AstrMessageEvent):
        r = await call_api("/v2/dad-joke")
        yield event.plain_result(f"🥶 {r['data'].get('content','')}" if r and r.get("code") == 200 else "❌ 失败")

    @command("运势")
    async def cmd_fortune(self, event: AstrMessageEvent):
        r = await call_api("/v2/fortune")
        if r and r.get("code") == 200:
            yield event.plain_result(f"🔮 今日运势: {r['data']}")
        else:
            yield event.plain_result("❌ 失败")

    @command("js题")
    async def cmd_jsquiz(self, event: AstrMessageEvent):
        r = await call_api("/v2/js-quiz")
        if r and r.get("code") == 200:
            yield event.plain_result(f"🧩 JS趣味题:\n{r['data']}")
        else:
            yield event.plain_result("❌ 失败")

    @command("唱歌")
    async def cmd_sing(self, event: AstrMessageEvent):
        r = await call_api("/v2/sing")
        if r and r.get("code") == 200:
            data = r["data"]
            if isinstance(data, dict) and data.get("url"):
                yield event.plain_result(f"🎤 随机唱歌音频: {data.get('url', '')}")
            else:
                yield event.plain_result(f"🎤 {data}")
        else:
            yield event.plain_result("❌ 失败")

    # ---- Help ----
    @command("60s帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        yield event.plain_result(HELP_TEXT)

    # ---- 自定义实例 ----
    @command("60s实例")
    async def cmd_instance(self, event: AstrMessageEvent):
        global API_BASE
        parts = event.get_message_str().strip().split(maxsplit=1)
        if len(parts) >= 2:
            new_url = parts[1].strip().rstrip("/")
            _save_custom_instance(new_url)
            API_BASE = new_url
            yield event.plain_result("API 实例已切换为: " + new_url)
        else:
            NL = chr(10)
            msg = "当前 API 实例: " + API_BASE + NL + NL + "可用公共实例:" + NL + "  https://60s.viki.moe（默认）" + NL + "  https://60api.09cdn.xyz" + NL + "  https://60s.zeabur.app" + NL + "  https://60s.crystelf.top" + NL + NL + "使用 /60s实例 <地址> 切换"
            yield event.plain_result(msg)


    # ═══════════════════════════════════════════
    # 订阅推送系统
    # ═══════════════════════════════════════════
    _SUB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscriptions.json")
    
    def _load_subs(self):
        if os.path.exists(self._SUB_FILE):
            try:
                with open(self._SUB_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []
    
    def _save_subs(self, subs):
        import copy
        safe = []
        for s in subs:
            clean = {}
            for k, v in s.items():
                if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    clean[k] = v
                else:
                    clean[k] = str(v)
            safe.append(clean)
        with open(self._SUB_FILE, "w", encoding="utf-8") as f:
            json.dump(safe, f, ensure_ascii=False, indent=2)
    
    _SUB_FEATURES = {
        "60s": "60s读报 /60s",
        "60s图片": "60s图片版 /60s图片",
        "天气": "实时天气 /天气 <城市>",
        "预报": "天气预报 /预报 <城市>",
        "黄金": "黄金价格 /黄金",
        "油价": "今日油价 /油价",
        "汇率": "汇率查询 /汇率",
        "历史": "历史上的今天 /历史",
        "摸鱼": "摸鱼资讯 /it",
        "段子": "搞笑段子 /段子",
        "一言": "一言 /一言",
        "运势": "今日运势 /运势",
        "kfc": "KFC文案 /kfc",
        "冷笑话": "冷笑话 /冷笑话",
    }
    
    async def initialize(self):
        """启动时恢复订阅定时任务"""
        subs = self._load_subs()
        logger.info(f"✅ 60s订阅: 已加载 {len(subs)} 个订阅配置，后台监控中...")
        # 取消旧监控任务（防止热重载后重复运行）
        if hasattr(self, '_monitor_task') and self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        # 启动新的后台监控协程
        self._monitor_task = asyncio.create_task(self._push_monitor())
    
    async def _push_monitor(self):
        """后台监控，每到整点推送订阅内容"""
        import asyncio
        last_push_hour = {}  # {session_feature: hour} 记录上次推送的小时
        while True:
            try:
                subs = self._load_subs()
                now_hour = __import__('datetime').datetime.now().hour
                for sub in subs:
                    if not sub.get('enabled', True):
                        continue
                    fid = sub['feature']
                    sid = sub['session']
                    push_hour = sub.get('push_time', 8)
                    key = f'{sid}_{fid}'
                    if push_hour == now_hour and last_push_hour.get(key) != now_hour:
                        last_push_hour[key] = now_hour
                        try:
                            async for msg in self._push_sub_content(fid, sid, sub, None):
                                if msg:
                                    await self._send_msg(sid, msg)
                        except Exception as e:
                            logger.error(f'订阅推送失败 [{fid}]: {e}')
                        # 订阅之间间隔2秒，避免API限流
                        await asyncio.sleep(2)
            except Exception as e:
                logger.error(f'监控循环错误: {e}')
            await asyncio.sleep(1)

    async def _send_msg(self, session, msg):
        """发送消息"""
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Plain, Image
            from astrbot.core.platform.message_session import MessageSession
            from astrbot.core.platform.message_type import MessageType
            # 解析session，获取真实平台名
            parts = session.split(":", 2)
            ms = None
            if len(parts) == 3:
                # 消息类型映射（兼容小写/原始字符串）
                mtype_map = {
                    "friend": MessageType.FRIEND_MESSAGE,
                    "group": MessageType.GROUP_MESSAGE,
                    "FriendMessage": MessageType.FRIEND_MESSAGE,
                    "GroupMessage": MessageType.GROUP_MESSAGE,
                    "OtherMessage": MessageType.OTHER_MESSAGE,
                }
                mtype = mtype_map.get(parts[1])
                if mtype is None:
                    logger.error(f'发送消息失败: 未知消息类型: {parts[1]}')
                    return
                # 找到匹配的平台实例
                for p in self.context.platform_manager.platform_insts:
                    if hasattr(p, 'meta') and p.meta().id == parts[0]:
                        ms = MessageSession(parts[0], mtype, parts[2])
                        break
                if not ms:
                    # 没找到匹配平台，尝试用第一个平台
                    for p in self.context.platform_manager.platform_insts:
                        if hasattr(p, 'meta'):
                            ms = MessageSession(p.meta().id, mtype, parts[2])
                            break
            if not ms:
                logger.error(f'发送消息失败: 无法解析session: {session}')
                return
            logger.info(f"[debug_send] type={type(msg).__name__} repr={repr(msg)[:200]}")
            if isinstance(msg, str):
                # 检测字符串是否为图片文件路径
                img_exts = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
                if msg.lower().endswith(img_exts) and os.path.exists(msg):
                    chain = MessageChain(chain=[Image.fromFileSystem(msg)])
                else:
                    chain = MessageChain().message(msg)
            elif hasattr(msg, 'type') and not isinstance(msg, (list, dict)):
                # 单个消息组件（如 Image）
                chain = MessageChain(chain=[msg])
            else:
                chain = msg
            await self.context.send_message(ms, chain)
        except Exception as e:
            logger.error(f'发送消息失败: {e} | type: {type(e).__name__} | repr: {repr(e)}')

    async def _push_sub_content(self, feature: str, session: str, sub: dict, event=None):
        """定时推送回调"""
        try:
            cmd_map = {
                "60s": self.cmd_60s,
                "60s图片": self.cmd_60s_img,
                "天气": self.cmd_weather,
                "预报": self.cmd_forecast,
                "黄金": self.cmd_gold,
                "油价": self.cmd_fuel,
                "汇率": self.cmd_exchange,
                "历史": self.cmd_history,
                "摸鱼": self.cmd_moyu,
                "段子": self.cmd_duanzi,
                "一言": self.cmd_hitokoto,
                "运势": self.cmd_fortune,
                "kfc": self.cmd_kfc,
                "冷笑话": self.cmd_dadjoke,
            }
            handler = cmd_map.get(feature)
            if not handler:
                return
            if event:
                # 有真实 event，直接走命令
                async for msg in handler(event):
                    yield msg
            else:
                # 定时推送：用轻量 fake_event 调命令，只取最后的有效结果
                fake = type('FakeEvent', (), {
                    'plain_result': lambda self, t: t,
                    'image_result': lambda self, u: u,
                })()
                # 对于需要解析参数的handler，设一个默认命令字符串
                # 天气默认北京/油价默认北京/预报默认北京…
                fake.message_str = '/' + feature
                fake.get_message_str = lambda: fake.message_str
                last = None
                async for msg in handler(fake):
                    if msg is not None:
                        last = msg
                if last is not None:
                    yield last
        except Exception as e:
            logger.error(f"订阅推送失败 [{feature}]: {traceback.format_exc()}")
    
    @command("60s订阅")
    async def cmd_subscribe(self, event: AstrMessageEvent):
        """60s订阅 <功能> <时间> — 订阅定时推送"""
        text = event.message_str.strip()
        parts = text.split()
        
        if len(parts) < 3:
            features = "\n".join([f"  {k} — {v}" for k, v in self._SUB_FEATURES.items()])
            yield event.plain_result(
                "📌 60s订阅系统\n\n"
                "用法: 60s订阅 <功能> <时间>\n"
                "示例: 60s订阅 60s 8  (每天8点推60s)\n"
                "      60s订阅 天气 7  (每天7点推天气)\n"
                "      60s订阅 摸鱼 9  (每天9点推摸鱼)\n\n"
                f"可订阅功能:\n{features}"
            )
            return
        
        feature = parts[1]
        try:
            hour = int(parts[2])
            if hour < 0 or hour > 23:
                yield event.plain_result("时间请输入 0~23")
                return
        except ValueError:
            yield event.plain_result("时间请输入 0~23 的数字")
            return
        
        if feature not in self._SUB_FEATURES:
            yield event.plain_result(f"未知功能 [{feature}]，可用: {' '.join(self._SUB_FEATURES.keys())}")
            return
        
        session = event.unified_msg_origin
        subs = self._load_subs()
        
        # 检查是否已订阅
        for s in subs:
            if s["feature"] == feature and s["session"] == session:
                s["push_time"] = hour
                s["enabled"] = True
                s.pop("_job_id", None)
                self._save_subs(subs)
                yield event.plain_result(f"✅ 已更新 [{feature}] → 每天{hour}点推送")
                return
        
        new_sub = {"feature": feature, "session": session, "push_time": hour, "enabled": True}
        subs.append(new_sub)
        self._save_subs(subs)
        yield event.plain_result(f"✅ 订阅成功! [{feature}] 每天{hour}点准时推送喵~")
    
    @command("60s退订")
    async def cmd_unsubscribe(self, event: AstrMessageEvent):
        """60s退订 <功能> — 取消订阅"""
        text = event.message_str.strip()
        parts = text.split()
        session = event.unified_msg_origin
        subs = self._load_subs()
        
        if len(parts) >= 2:
            feature = parts[1]
            new_subs = [s for s in subs if not (s["feature"] == feature and s["session"] == session)]
            if len(new_subs) == len(subs):
                yield event.plain_result(f"未找到 [{feature}] 的订阅")
                return
            self._save_subs(new_subs)
            yield event.plain_result(f"已退订 [{feature}]")
        else:
            new_subs = [s for s in subs if s["session"] != session]
            self._save_subs(new_subs)
            yield event.plain_result("已退订本会话所有订阅")
    
    @command("60s订阅列表")
    async def cmd_sub_list(self, event: AstrMessageEvent):
        """查看订阅列表"""
        subs = self._load_subs()
        if not subs:
            yield event.plain_result("暂无任何订阅")
            return
        lines = ["📋 60s订阅列表:"]
        for i, s in enumerate(subs, 1):
            st = "🟢" if s.get("enabled", True) else "🔴"
            lines.append(f"{i}. {st} [{s['feature']}] 每天{s.get('push_time',8)}点")
            lines.append(f"   {s['session'][:36]}")
        yield event.plain_result("\n".join(lines))


    @command("60s推送")
    async def cmd_push_all(self, event: AstrMessageEvent):
        """60s推送 — 统一推送所有已订阅内容"""
        subs = self._load_subs()
        session = event.unified_msg_origin
        pushed = []
        for sub in subs:
            if sub["session"] == session:
                async for msg in self._push_sub_content(sub["feature"], session, sub, event):
                    yield msg
                pushed.append(sub["feature"])
        if not pushed:
            yield event.plain_result("当前没有订阅，请先使用 60s订阅 <功能> <时间>")

    @command("60s测试")
    async def cmd_test_push(self, event: AstrMessageEvent, feature: str):
        """60s测试 <功能> — 10秒后推送测试"""
        if feature not in self._SUB_FEATURES:
            yield event.plain_result(f"未知功能 [{feature}]，可用: {' '.join(self._SUB_FEATURES.keys())}")
            return
        import asyncio
        yield event.plain_result(f"✅ 10秒后将推送 [{feature}] 测试消息")
        await asyncio.sleep(10)
        async for msg in self._push_sub_content(feature, event.unified_msg_origin, {"push_image": True}, event):
            yield msg



