"""ayin 画图插件：技能转 Danbooru tag + 可切换画师串 + 自定义生图接口。

支持两种生图接口形态：
- images：OpenAI 兼容的 /v1/images/generations
- chat：网关式 /v1/chat/completions（SSE 流式返回图片 URL 或 data:image base64）
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from pathlib import Path

import httpx

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, StarTools, register

PLUGIN_NAME = "astrbot_plugin_ayin_paint"

DRAW_TRIGGER = re.compile(r"^\s*/?文生图\s+\S")
PRESET_TRIGGER = re.compile(r"^\s*/?画师串\s*.*$")
STATUS_TRIGGER = re.compile(r"^\s*/?(?:画图状态|画图配置|生图配置|绘图配置|文生图状态|文生图配置)\s*$")
RATIO_PREFIX = re.compile(r"^(\d{1,2}\s*:\s*\d{1,2})\s+(.+)$")
MULTI_WORD = "多角色"
MULTI_SPLIT_RE = re.compile(r"\s*[|｜]\s*")
POS_RE = re.compile(r"@\s*([A-Ea-e][1-5])\s*$")
MULTI_SUFFIX = """

【多角色输出格式】用户会给出全局画面和若干角色。只输出一个 JSON 对象，不要解释、不要代码围栏：
{"prompt": "全局画面 tag，不含任何单个角色的专属特征", "characters": [{"prompt": "该角色专属 tag", "negative_prompt": "该角色禁止出现的特征", "position": "C3"}]}
characters 数量与用户给的角色数量一致；position 用 5x5 网格字符串 A-E 列、1-5 行。
每个角色 prompt 只写该角色自身特征（发色、眼色、兽耳、服装、手持物、朝向），互不混色。
"""

SET_WORDS = ("设", "设置", "加", "添加", "新增", "set")
DELETE_WORDS = ("删", "删除", "移除", "del", "remove")
USE_WORDS = ("用", "切换", "切", "选择", "use")

MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
DATA_IMAGE_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=_-]+")
PLAIN_URL_RE = re.compile(r"https?://[^\s\"'<>\)\]]+")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")

TAG_SYSTEM_SUFFIX = (
    "\n\n【最终输出要求】直接输出可复制的 Danbooru tag 串，英文逗号分隔；"
    "不写解释、不写前后缀、不用 Markdown 代码块、不写质量词、不写画师 tag、不写负面词。"
)

FALLBACK_SKILL = (
    "你是 NovelAI 提示词专家，精通 Danbooru 标签体系。把用户的中文画面描述转成英文 tag 串：\n"
    "1. 已知角色只写 角色名_(作品名)，不补发色发型瞳色；原创人物才补外貌。\n"
    "2. 顺序：人数 → 视角构图 → 角色 → 外观服装 → 动作 → 表情姿态 → 场景 → 光影 → year 2025。\n"
    "3. 权重用 X::tagA, tagB:: 语法，范围 0-8，精确到 0.1。\n"
    "4. 多人场景用 char1[B3]: 坐标写法。\n"
)

DANBOORU_BASE = "https://danbooru.donmai.us"
# 注意：浏览器 UA 会触发 Cloudflare 挑战，非浏览器 UA 才放行
DANBOORU_HEADERS = {
    "User-Agent": "astrbot-ayin-paint/1.3 (danbooru tag lookup)",
    "Accept": "application/json",
}

CHARACTER_EXTRACT_PROMPT = (
    "你是动漫角色识别助手。从用户的画面描述里找出所有「有具体出处的已知角色」，"
    "把每个角色转换成用于检索 Danbooru 角色标签的英文/罗马音名候选，"
    "写法用「作品名 角色名」或「角色名 作品名」都行。\n"
    "只输出一个 JSON 数组，例如：[\"hatsune miku\", \"rem re:zero\"]。\n"
    "没有已知角色、或人物明显是原创角色时输出 []。最多 {limit} 个，不要输出任何解释。"
)

CHARACTER_OUTPUT_RULE = (
    "\n\n【角色锁定】以下角色已联网核对 Danbooru 官方角色标签，必须原样使用，"
    "不要改写命名格式，不要换成相似角色：\n{tags}\n"
    "这些角色禁止再补发色、发型、瞳色、体型等外貌 tag（换装、裸体等状态变化除外）。"
)

# ---------- 角色收录库 ----------

LIBRARY_FILE_NAME = "character_library.json"

_NAME_STRIP_TABLE = str.maketrans("", "", """ _·、,，。./|:：'"-（）()""" + chr(9) + chr(10))




LIBRARY_TRIGGER = re.compile("^[ ]*/?(?:角色收录|收录角色|角色库|角色列表)[ ]*.*$")

BUILTIN_LIBRARY: list[dict] = [
    {"aliases": ["初音未来", "初音", "hatsune miku", "miku"], "tag": "hatsune_miku_(vocaloid)", "series": "VOCALOID"},
    {"aliases": ["洛天依", "天依", "luo tianyi"], "tag": "luo_tianyi_(vocaloid)", "series": "VOCALOID"},
    {"aliases": ["堂吉诃德", "堂吉柯德", "don quixote"], "tag": "don_quixote_(limbus_company)", "series": "Limbus Company"},
    {"aliases": ["堂吉诃德", "堂吉柯德", "don quixote"], "tag": "don_quixote_(fate)", "series": "Fate/Grand Order"},
]

LIBRARY_DISAMBIGUATE_PROMPT = (
    "你是动漫角色消歧助手。描述里出现了重名角色，候选是不同作品中的同名角色。"
    "请结合描述里的作品线索、同伴、服装、剧情、称呼判断最匹配的一个。"
    "只输出候选 tag 的原样字符串，不要解释、不要输出其他文字；实在判断不出就输出第一个候选。"
)

DEFAULT_RATIO_SIZES = {
    "9:16": "1152x2048",
    "1:1": "2048x2048",
    "3:4": "1536x2048",
    "4:3": "2048x1536",
    "16:9": "2048x1152",
    "2:3": "1365x2048",
    "3:2": "2048x1365",
    "21:9": "2048x878",
}


@register(
    PLUGIN_NAME,
    "ayin",
    "画图插件：技能转 Danbooru tag，可切换画师串，自定义生图接口（images/chat 双模式）",
    "v1.3.0",
)
class AyinPaintPlugin(Star):
    """Danbooru tag 画图插件"""

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config or {}
        try:
            self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            self.data_dir = Path(__file__).parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"
        self.state = self._load_state()
        self._load_library()

    # ---------- 基础 ----------

    def _cfg(self, key: str, default=None):
        try:
            value = self.config.get(key, default)
        except Exception:
            value = default
        if value is None or (isinstance(value, str) and not value.strip()):
            return default
        return value

    def _load_state(self) -> dict:
        try:
            if self.state_path.is_file():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception as exc:
            logger.warning("[ayin_paint] 读取状态失败: %s", exc)
        return {}

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("[ayin_paint] 保存状态失败: %s", exc)

    # ---------- 画师串 ----------

    @staticmethod
    def _parse_presets(text) -> dict:
        result: dict[str, str] = {}
        for line in str(text or "").replace("＝", "=").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip()
            if name and value:
                result[name] = value
        return result

    def _presets(self) -> dict:
        presets = self._parse_presets(self._cfg("artist_presets", ""))
        custom = self.state.get("custom_presets")
        if isinstance(custom, dict):
            presets.update({str(k): str(v) for k, v in custom.items() if k and v})
        return presets

    def _active_name(self) -> str:
        presets = self._presets()
        name = str(
            self.state.get("active_preset") or self._cfg("active_preset", "") or ""
        ).strip()
        if name in presets:
            return name
        return next(iter(presets), "")

    def _compose_prompt(self, tags: str) -> str:
        preset = self._presets().get(self._active_name(), "")
        return f"{tags}, {preset}".strip(" ,") if preset else tags

    # ---------- tag 转换 ----------

    def _skill_text(self) -> str:
        raw = str(self._cfg("skill_path", "") or "").strip()
        if raw:
            path = Path(raw)
            try:
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="ignore").strip()
                    if text:
                        return text + TAG_SYSTEM_SUFFIX
            except Exception as exc:
                logger.warning("[ayin_paint] 读取技能失败: %s", exc)
        return FALLBACK_SKILL + TAG_SYSTEM_SUFFIX

    def _resolve_provider(self, event: AstrMessageEvent):
        want = str(self._cfg("tag_model", "") or "").strip()
        provider = None
        try:
            if want:
                getter = getattr(self.context, "get_provider_by_id", None)
                if callable(getter):
                    provider = getter(want)
            if provider is None:
                provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        except Exception as exc:
            logger.warning("[ayin_paint] 获取对话模型失败: %s", exc)
            provider = None
        if provider is None or not hasattr(provider, "text_chat"):
            return None
        return provider

    @staticmethod
    def _clean_tags(text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if text.startswith("```"):
            text = re.sub(r"^```[A-Za-z]*\s*", "", text)
            text = re.sub(r"```\s*$", "", text)
        text = text.replace("\r", "\n")
        text = re.sub(r"\n+", ", ", text)
        text = text.replace("，", ", ")
        text = re.sub(r"\s*,\s*", ", ", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" ,")
        seen: set[str] = set()
        parts: list[str] = []
        for part in text.split(", "):
            piece = part.strip()
            if not piece:
                continue
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(piece)
        text = ", ".join(parts)
        return text[:1500].rsplit(", ", 1)[0] if len(text) > 1500 else text

    # ---------- 角色收录库 ----------

    @staticmethod
    def _norm_name(text: str) -> str:
        text = str(text or "").lower().replace("（", "(").replace("）", ")")
        return text.translate(_NAME_STRIP_TABLE)

    @classmethod
    def _normalize_entry(cls, item: dict, builtin: bool = False) -> dict | None:
        if not isinstance(item, dict):
            return None
        tag = str(item.get("tag") or "").strip()
        if not tag:
            return None
        aliases = item.get("aliases")
        if isinstance(aliases, str):
            aliases = re.split("[/,，、|]", aliases)
        if not isinstance(aliases, list):
            aliases = []
        alias_list: list[str] = []
        for alias in aliases:
            piece = str(alias).strip()
            if piece and piece not in alias_list:
                alias_list.append(piece)
        spaced = tag.replace("_", " ")
        if spaced not in alias_list:
            alias_list.append(spaced)
        return {
            "tag": tag,
            "aliases": alias_list,
            "series": str(item.get("series") or "").strip(),
            "builtin": builtin,
        }

    def _load_library(self) -> None:
        self.library_path = self.data_dir / LIBRARY_FILE_NAME
        custom: list[dict] = []
        try:
            if self.library_path.is_file():
                data = json.loads(self.library_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        entry = self._normalize_entry(item)
                        if entry:
                            custom.append(entry)
        except Exception as exc:
            logger.warning("[ayin_paint] 读取角色收录库失败: %s", exc)
        self.custom_library = custom
        builtin = [
            entry
            for entry in (self._normalize_entry(item, True) for item in BUILTIN_LIBRARY)
            if entry
        ]
        self.library = builtin + custom

    def _save_library(self) -> None:
        try:
            payload = [
                {"tag": item["tag"], "aliases": item["aliases"], "series": item.get("series", "")}
                for item in self.custom_library
            ]
            self.library_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("[ayin_paint] 保存角色收录库失败: %s", exc)

    def _library_enabled(self) -> bool:
        return bool(self._cfg("enable_library", True))

    @classmethod
    def _alias_hit(cls, alias: str, desc: str) -> bool:
        alias = str(alias or "").strip()
        if not alias or not desc:
            return False
        if re.search("[a-zA-Z]", alias):
            words = re.findall("[a-z0-9]+", str(desc).lower())
            target = " ".join(alias.lower().split())
            return (" " + target + " ") in (" " + " ".join(words) + " ")
        return cls._norm_name(alias) in cls._norm_name(desc)

    def _library_matches(self, desc: str) -> tuple[list[dict], list[tuple[str, list[dict]]]]:
        """命中收录库：名字唯一的直接锁定，重名的交给 AI 消歧。"""
        groups: dict[str, list[dict]] = {}
        for entry in self.library:
            for alias in entry["aliases"]:
                if not self._alias_hit(alias, desc):
                    continue
                bucket = groups.setdefault(self._norm_name(alias), [])
                if entry["tag"] not in [item["tag"] for item in bucket]:
                    bucket.append(entry)
                break
        hits: list[dict] = []
        ambiguous: list[tuple[str, list[dict]]] = []
        for entries in groups.values():
            if len(entries) == 1:
                hits.append(entries[0])
            else:
                ambiguous.append((entries[0]["tag"], entries))
        unique: list[dict] = []
        seen: set[str] = set()
        for entry in hits:
            if entry["tag"] in seen:
                continue
            seen.add(entry["tag"])
            unique.append(entry)
        return unique, ambiguous

    async def _classify_library(
        self, provider, desc: str, name: str, candidates: list[dict]
    ) -> dict:
        options = chr(10).join(
            f"{index}. {item['tag']}｜{item.get('series') or '未知作品'}"
            for index, item in enumerate(candidates, 1)
        )
        prompt = f"画面描述：{desc}{chr(10)}{chr(10)}候选角色：{chr(10)}{options}{chr(10)}{chr(10)}只输出一个 tag。"
        text = ""
        try:
            resp = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                image_urls=[],
                func_tool=None,
                system_prompt=LIBRARY_DISAMBIGUATE_PROMPT,
            )
            text = str(getattr(resp, "completion_text", "") or "")
        except Exception as exc:
            logger.warning("[ayin_paint] 重名消歧调用失败(%s): %s", name, exc)
        norm_text = self._norm_name(text)
        for item in candidates:
            if self._norm_name(item["tag"]) in norm_text or item["tag"].lower() in text.lower():
                logger.info("[ayin_paint] 重名消歧 %s -> %s", name, item["tag"])
                return item
            series = self._norm_name(item.get("series", ""))
            if series and series in norm_text:
                logger.info("[ayin_paint] 重名消歧 %s -> %s（按作品）", name, item["tag"])
                return item
        logger.info("[ayin_paint] 重名消歧 %s 无结果，用首选 %s", name, candidates[0]["tag"])
        return candidates[0]

    # ---------- 角色联网核对 ----------

    def _lookup_enabled(self) -> bool:
        return bool(self._cfg("enable_lookup", True))

    async def _extract_character_names(self, provider, desc: str) -> list[str]:
        limit = int(self._cfg("lookup_max_names", 3) or 3)
        try:
            resp = await provider.text_chat(
                prompt=desc,
                contexts=[],
                image_urls=[],
                func_tool=None,
                system_prompt=CHARACTER_EXTRACT_PROMPT.format(limit=limit),
            )
            text = str(getattr(resp, "completion_text", "") or "")
        except Exception as exc:
            logger.warning("[ayin_paint] 角色识别失败: %s", exc)
            return []
        return self._parse_name_list(text, limit)

    @staticmethod
    def _parse_name_list(text: str, limit: int) -> list[str]:
        raw = str(text or "").strip()
        names: list[str] = []
        match = re.search(r"\[.*?\]", raw, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    names = [str(item).strip() for item in data if str(item).strip()]
            except Exception:
                names = []
        if not names:
            cleaned = raw.strip("` \n")
            names = [line.strip(" -\t\"',[]") for line in cleaned.splitlines()]
        result: list[str] = []
        skip = {"[]", "none", "null", "无", "原创", "无已知角色"}
        for name in names:
            piece = re.sub(r"[\"']", "", str(name)).strip()
            if not piece or piece.lower() in skip or piece in result:
                continue
            result.append(piece)
        return result[:limit]

    async def _danbooru_candidates(self, client: httpx.AsyncClient, name: str) -> list[dict]:
        base = str(self._cfg("danbooru_base", DANBOORU_BASE) or DANBOORU_BASE).rstrip("/")
        query = name.replace("_", " ")
        items: list[dict] = []
        try:
            resp = await client.get(
                f"{base}/autocomplete.json",
                params={"search[query]": query, "search[type]": "tag_query", "limit": 12},
                headers=DANBOORU_HEADERS,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    items = [item.get("tag") or {} for item in data if isinstance(item, dict)]
        except Exception as exc:
            logger.warning("[ayin_paint] Danbooru 联想失败(%s): %s", name, exc)
        if not items:
            try:
                resp = await client.get(
                    f"{base}/tags.json",
                    params={
                        "search[name_matches]": f"*{query}*",
                        "search[category]": 4,
                        "search[order]": "count",
                        "limit": 8,
                    },
                    headers=DANBOORU_HEADERS,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        items = [item for item in data if isinstance(item, dict)]
            except Exception as exc:
                logger.warning("[ayin_paint] Danbooru 搜索失败(%s): %s", name, exc)
        return [
            item
            for item in items
            if item.get("name") and int(item.get("category", 0) or 0) == 4
        ]

    @staticmethod
    def _match_score(name: str, tag_name: str, post_count: int) -> int:
        query = re.sub(r"[^a-z0-9]", "", str(name).lower())
        tag = re.sub(r"[^a-z0-9]", "", str(tag_name).lower())
        if not query or not tag:
            return 0
        if query == tag:
            score = 100
        elif tag.startswith(query) or query.startswith(tag):
            score = 80
        elif query in tag:
            score = 65
        elif tag in query:
            score = 55
        else:
            return 0
        if "cosplay" in str(tag_name):
            score -= 40
        return score + min(19, int(math.log10(max(1, post_count)) * 4))

    async def _web_reference(self, client: httpx.AsyncClient, names: list[str]) -> str:
        key = str(self._cfg("search_api_key", "") or "").strip()
        if not key or not names:
            return ""
        try:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": f"danbooru character tag {' '.join(names)} series name",
                    "max_results": 3,
                    "include_answer": True,
                },
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
        except Exception as exc:
            logger.warning("[ayin_paint] 联网搜索失败: %s", exc)
            return ""
        parts = [str(data.get("answer") or "")]
        for item in data.get("results", [])[:3]:
            parts.append(str(item.get("content") or "")[:200])
        return "；".join(part.strip() for part in parts if part and part.strip())[:600]

    async def _resolve_characters(
        self, provider, desc: str, client: httpx.AsyncClient
    ) -> tuple[list[dict], str]:
        min_posts = int(self._cfg("lookup_min_posts", 30) or 30)
        max_names = int(self._cfg("lookup_max_names", 3) or 3)
        cache = self.state.get("character_cache")
        if not isinstance(cache, dict):
            cache = {}
            self.state["character_cache"] = cache

        found: list[dict] = []
        covered: list[dict] = []
        if self._library_enabled():
            hits, ambiguous = self._library_matches(desc)
            for entry in hits:
                found.append(
                    {
                        "tag": entry["tag"],
                        "posts": 0,
                        "query": entry.get("series", ""),
                        "from": "library",
                    }
                )
                covered.append(entry)
            for name, candidates in ambiguous[:max_names]:
                chosen = (
                    await self._classify_library(provider, desc, name, candidates)
                    if provider is not None
                    else candidates[0]
                )
                if chosen is None:
                    continue
                found.append(
                    {
                        "tag": chosen["tag"],
                        "posts": 0,
                        "query": chosen.get("series", ""),
                        "from": "library",
                    }
                )
                covered.append(chosen)
            if found:
                logger.info(
                    "[ayin_paint] 收录库命中: %s", ", ".join(item["tag"] for item in found)
                )

        def is_covered(name: str) -> bool:
            norm = self._norm_name(name)
            if any(norm == self._norm_name(item["tag"]) for item in found):
                return True
            for entry in covered:
                for alias in entry["aliases"]:
                    if self._norm_name(alias) == norm or self._alias_hit(alias, name):
                        return True
            return False

        names = await self._extract_character_names(provider, desc) if provider is not None else []
        names = [name for name in names if not is_covered(name)]

        missing: list[str] = []
        changed = False
        for name in names[: max(0, max_names - len(found))]:
            key = name.lower().strip()
            cached = cache.get(key)
            if isinstance(cached, list) and cached:
                found.extend(item for item in cached if isinstance(item, dict))
                continue
            best: dict | None = None
            best_score = 0
            for tag in await self._danbooru_candidates(client, name):
                score = self._match_score(
                    name, str(tag.get("name")), int(tag.get("post_count", 0) or 0)
                )
                if score > best_score:
                    best, best_score = tag, score
            posts = int((best or {}).get("post_count", 0) or 0)
            if best and best_score >= 60 and posts >= min_posts:
                entry = {"tag": best["name"], "posts": posts, "query": name}
                found.append(entry)
                cache[key] = [entry]
                changed = True
                logger.info("[ayin_paint] 角色锁定 %s -> %s (%s posts)", name, best["name"], posts)
            else:
                missing.append(name)
        if changed:
            self._save_state()

        unique: list[dict] = []
        seen: set[str] = set()
        for item in found:
            tag = str(item.get("tag") or "").strip()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            unique.append(item)
        reference = await self._web_reference(client, missing) if missing else ""
        return unique[:max_names], reference

    @staticmethod
    def _ensure_character_tags(tags: str, characters: list[dict]) -> str:
        parts = [part.strip() for part in str(tags).split(", ") if part.strip()]
        if not parts:
            return tags
        insert_at = 0
        count_re = re.compile(
            r"^(solo|solo_focus|1girl|1boy|\d+girls?|\d+boys?|multiple_girls|multiple_boys|no_humans)$"
        )
        for index, part in enumerate(parts[:5]):
            if count_re.match(part.strip()):
                insert_at = index + 1
        for item in reversed(characters):
            tag = str(item.get("tag") or "").strip()
            if not tag:
                continue
            norm = re.sub(r"[^a-z0-9]", "", tag.lower())
            if any(norm in re.sub(r"[^a-z0-9]", "", part.lower()) for part in parts):
                continue
            parts.insert(insert_at, tag)
        return ", ".join(parts)

    # ---------- tag 转换 ----------

    async def _to_tags(self, event: AstrMessageEvent, desc: str) -> str:
        if not bool(self._cfg("enable_tag", True)):
            return desc
        provider = self._resolve_provider(event)
        if provider is None:
            logger.warning("[ayin_paint] 没有可用对话模型，直接用原文出图")
            return desc
        characters: list[dict] = []
        reference = ""
        if self._lookup_enabled():
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    characters, reference = await self._resolve_characters(provider, desc, client)
            except Exception as exc:
                logger.warning("[ayin_paint] 角色核对异常，跳过: %s", exc)
        system_prompt = self._skill_text()
        if characters:
            system_prompt += CHARACTER_OUTPUT_RULE.format(
                tags=", ".join(str(item["tag"]) for item in characters)
            )
        if reference:
            system_prompt += f"\n\n【联网检索参考】{reference}"
        try:
            response = await provider.text_chat(
                prompt=desc,
                contexts=[],
                image_urls=[],
                func_tool=None,
                system_prompt=system_prompt,
            )
            tags = self._clean_tags(str(getattr(response, "completion_text", "") or ""))
            if tags:
                return self._ensure_character_tags(tags, characters) if characters else tags
        except Exception as exc:
            logger.warning("[ayin_paint] 转 tag 失败，回退原文: %s", exc)
        return desc

    # ---------- 多角色 ----------

    def _parse_multi_parts(self, desc: str) -> tuple[str, list[dict]]:
        parts = [item.strip() for item in MULTI_SPLIT_RE.split(desc) if item.strip()]
        if not parts:
            return "", []
        global_desc = parts[0]
        chars: list[dict] = []
        for raw in parts[1:]:
            pos = ""
            matched = POS_RE.search(raw)
            if matched:
                pos = matched.group(1).upper()
                raw = raw[: matched.start()].strip()
            if raw:
                chars.append({"desc": raw, "position": pos})
        return global_desc, chars[:6]

    async def _to_multi_tags(
        self, event: AstrMessageEvent, global_desc: str, chars: list[dict]
    ) -> tuple[str, list[dict]]:
        fallback = (
            global_desc,
            [
                {
                    "prompt": item["desc"],
                    "negative_prompt": "",
                    "position": item["position"] or "C3",
                }
                for item in chars
            ],
        )
        provider = self._resolve_provider(event)
        if provider is None:
            return fallback
        lines = [f"全局画面：{global_desc}"]
        for idx, item in enumerate(chars, 1):
            lines.append(f"角色{idx}：{item['desc']}")
        system_prompt = self._skill_text() + MULTI_SUFFIX
        try:
            response = await provider.text_chat(
                prompt="\n".join(lines),
                contexts=[],
                image_urls=[],
                func_tool=None,
                system_prompt=system_prompt,
            )
            raw = str(getattr(response, "completion_text", "") or "").strip()
            raw = re.sub(r"^```[A-Za-z]*\s*", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
            start, end = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[start : end + 1]) if start >= 0 and end > start else {}
            if not isinstance(data, dict):
                return fallback
            prompt = self._clean_tags(data.get("prompt", "") or global_desc)
            out_chars: list[dict] = []
            items = data.get("characters") if isinstance(data.get("characters"), list) else []
            for idx, item in enumerate(chars):
                src = items[idx] if idx < len(items) and isinstance(items[idx], dict) else {}
                char_prompt = self._clean_tags(src.get("prompt", "") or item["desc"])
                negative = self._clean_tags(src.get("negative_prompt", "") or "")
                pos = item["position"] or str(src.get("position", "") or "C3").upper()
                if not re.fullmatch(r"[A-E][1-5]", pos):
                    pos = "C3"
                out_chars.append(
                    {"prompt": char_prompt, "negative_prompt": negative, "position": pos}
                )
            return (prompt or global_desc), out_chars
        except Exception as exc:
            logger.warning("[ayin_paint] 多角色 tag 转换失败，回退原文: %s", exc)
            return fallback

    # ---------- 出图 ----------

    def _ratio_sizes(self) -> dict:
        mapping = self._parse_presets(self._cfg("ratio_sizes", ""))
        return mapping or DEFAULT_RATIO_SIZES

    def _default_ratio(self) -> str:
        ratio = str(self._cfg("default_ratio", "9:16") or "9:16").strip().replace(" ", "")
        if ratio in ("方", "方形"):
            return "1:1"
        if ratio in ("竖", "竖图", "手机", "手机竖屏", "竖屏"):
            return "9:16"
        if ratio in ("横", "横图"):
            return "16:9"
        return ratio or "9:16"

    def _resolve_size(self, ratio: str) -> str:
        ratio = str(ratio or "").strip().replace(" ", "")
        if ratio in ("方", "方形"):
            ratio = "1:1"
        elif ratio in ("竖", "竖图", "手机", "手机竖屏", "竖屏"):
            ratio = "9:16"
        elif ratio in ("横", "横图"):
            ratio = "16:9"
        if not ratio:
            ratio = self._default_ratio()
        sizes = self._ratio_sizes()
        if ratio in sizes:
            return sizes[ratio]
        fallback = str(self._cfg("size", "") or "").strip()
        return fallback or sizes.get("9:16") or "1152x2048"

    @staticmethod
    def _size_wh(size: str):
        matched = re.match(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$", str(size or ""))
        if not matched:
            return None
        return int(matched.group(1)), int(matched.group(2))

    def _chat_size_fields(self) -> list:
        raw = str(self._cfg("chat_size_fields", "size,width,height") or "")
        return [item for item in re.split(r"[,，\s]+", raw) if item]

    def _apply_size_params(self, payload: dict, size: str) -> None:
        wh = self._size_wh(size)
        for field in self._chat_size_fields():
            key = field.strip().lower()
            if key == "size" and size:
                payload["size"] = size
            elif key == "width" and wh:
                payload["width"] = wh[0]
            elif key == "height" and wh:
                payload["height"] = wh[1]

    def _chat_extra_body(self) -> dict:
        raw = str(self._cfg("chat_extra_body", "") or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("[ayin_paint] chat_extra_body 不是合法 JSON，已忽略: %s", exc)
            return {}

    def _request_mode(self) -> str:
        mode = str(self._cfg("request_mode", "auto") or "auto").strip().lower()
        if mode in ("images", "chat"):
            return mode
        url = str(self._cfg("api_url", "") or "").lower()
        return "chat" if "chat/completions" in url or url.endswith("/completions") else "images"

    def _endpoint(self, kind: str) -> str:
        url = str(self._cfg("api_url", "") or "").strip()
        if not url:
            raise RuntimeError("还没配置生图请求地址")
        url = url.rstrip("/")
        targets = (
            ("/chat/completions", "/completions")
            if kind == "chat"
            else ("/images/generations", "/images/edits")
        )
        if url.endswith(targets):
            return url
        for suffix in ("/chat/completions", "/completions", "/images/generations", "/images/edits"):
            if url.endswith(suffix):
                url = url[: -len(suffix)].rstrip("/")
                break
        if url.endswith("/v1") or url.endswith("/v1beta"):
            return f"{url}{targets[0]}"
        return f"{url}/v1{targets[0]}"

    def _extra_body(self) -> dict:
        raw = str(self._cfg("extra_body", "") or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("[ayin_paint] extra_body 不是合法 JSON，已忽略: %s", exc)
            return {}

    @staticmethod
    def _row_value(row) -> str:
        if isinstance(row, str):
            return row if len(row) > 24 else ""
        if isinstance(row, dict):
            for key in ("b64_json", "base64", "image_base64", "image", "url", "image_url"):
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    if value.startswith("http") or len(value) > 64:
                        return value.strip()
        return ""

    def _extract_items(self, data) -> list[str]:
        items: list[str] = []
        if isinstance(data, dict):
            for key in ("data", "images", "output", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    items.extend([v for v in (self._row_value(row) for row in value) if v])
            if not items:
                got = self._row_value(data)
                if got:
                    items.append(got)
        elif isinstance(data, list):
            items.extend([v for v in (self._row_value(row) for row in data) if v])
        return items

    def _extract_text_refs(self, text: str) -> list[str]:
        refs: list[str] = []
        for match in DATA_IMAGE_RE.findall(text or ""):
            if match not in refs:
                refs.append(match)
        for body in MD_IMAGE_RE.findall(text or ""):
            body = body.strip()
            if body.startswith("data:image/") and body not in refs:
                refs.append(body)
        for url in PLAIN_URL_RE.findall(text or ""):
            clean = url.rstrip(").,")
            low = clean.lower().split("?")[0]
            if low.endswith(IMAGE_SUFFIXES) and clean not in refs:
                refs.append(clean)
        return refs

    def _save_bytes(self, content: bytes, index: int, src_url: str = "") -> Path:
        out_dir = self.data_dir / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".png"
        low = (src_url or "").lower().split("?")[0]
        for ext in IMAGE_SUFFIXES:
            if low.endswith(ext):
                suffix = ext
                break
        if content[:3] == b"\xff\xd8\xff":
            suffix = ".jpg"
        elif content[:8] == b"\x89PNG\r\n\x1a\n":
            suffix = ".png"
        elif content[:4] == b"RIFF":
            suffix = ".webp"
        path = out_dir / f"paint_{int(time.time())}_{index}{suffix}"
        path.write_bytes(content)
        self._prune(out_dir)
        return path

    def _prune(self, out_dir: Path) -> None:
        try:
            keep = max(1, int(self._cfg("keep_files", 80) or 80))
        except Exception:
            keep = 80
        try:
            files = sorted(out_dir.glob("paint_*"), key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in files[keep:]:
                stale.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug("[ayin_paint] 清理旧图失败: %s", exc)

    def _headers(self) -> dict:
        key = str(self._cfg("api_key", "") or "").strip()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def _download(self, client: httpx.AsyncClient, items: list[str], count: int) -> list[Path]:
        wanted = items[: max(1, int(count or 1))] if count and int(count or 1) > 1 else items
        paths: list[Path] = []
        for index, item in enumerate(wanted):
            if item.startswith("http"):
                got = await client.get(item)
                got.raise_for_status()
                paths.append(self._save_bytes(got.content, index, item))
            else:
                raw = item.split(",", 1)[-1] if item.startswith("data:") else item
                paths.append(self._save_bytes(base64.b64decode(raw), index))
        return paths

    async def _request_images(
        self, prompt: str, size: str, count: int, extra: dict | None = None
    ) -> list[Path]:
        mode = self._request_mode()
        if mode == "chat":
            return await self._request_images_chat(prompt, size, count, extra)
        return await self._request_images_rest(prompt, size, count, extra)

    async def _request_images_rest(
        self, prompt: str, size: str, count: int, extra: dict | None = None
    ) -> list[Path]:
        url = self._endpoint("images")
        model = str(self._cfg("model", "") or "").strip()
        payload: dict = {"prompt": prompt, "n": max(1, int(count or 1)), "size": size or self._resolve_size("")}
        if model:
            payload["model"] = model
        payload.update(self._extra_body())
        if extra:
            payload.update(extra)
        try:
            timeout = float(self._cfg("timeout", 180) or 180)
        except Exception:
            timeout = 180.0
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            if response.status_code >= 400 and "model" in payload:
                retry_payload = dict(payload)
                retry_payload.pop("model", None)
                response = await client.post(url, headers=self._headers(), json=retry_payload)
            if response.status_code >= 400:
                raise RuntimeError(f"生图接口返回 {response.status_code}：{response.text[:180]}")
            try:
                data = response.json()
            except Exception:
                raise RuntimeError("生图接口返回了非 JSON 内容")
            items = self._extract_items(data)
            if not items:
                raise RuntimeError(f"生图接口没返回图片：{str(data)[:180]}")
            return await self._download(client, items, count)

    async def _request_images_chat(
        self, prompt: str, size: str, count: int, extra: dict | None = None
    ) -> list[Path]:
        url = self._endpoint("chat")
        model = str(self._cfg("model", "") or "").strip()
        stream = bool(self._cfg("stream", True))
        payload: dict = {
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if model:
            payload["model"] = model
        self._apply_size_params(payload, size)
        negative = str(self._cfg("negative_prompt", "") or "").strip()
        if negative:
            payload["negative_prompt"] = negative
        payload.update(self._chat_extra_body())
        payload.update(self._extra_body())
        if extra:
            payload.update(extra)
        try:
            timeout = float(self._cfg("timeout", 180) or 180)
        except Exception:
            timeout = 180.0
        text_parts: list[str] = []
        items: list[str] = []
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if not stream:
                response = await client.post(url, headers=self._headers(), json=payload)
                if response.status_code >= 400:
                    raise RuntimeError(f"生图接口返回 {response.status_code}：{response.text[:180]}")
                try:
                    data = response.json()
                except Exception:
                    raise RuntimeError("生图接口返回了非 JSON 内容")
                items = self._extract_items(data)
                for choice in (data.get("choices") or []) if isinstance(data, dict) else []:
                    message = choice.get("message") if isinstance(choice, dict) else None
                    if isinstance(message, dict) and isinstance(message.get("content"), str):
                        text_parts.append(message["content"])
            else:
                async with client.stream(
                    "POST", url, headers=self._headers(), json=payload
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "ignore")
                        raise RuntimeError(
                            f"生图接口返回 {response.status_code}：{body[:180]}"
                        )
                    async for line in response.aiter_lines():
                        line = (line or "").strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if not line or line == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            text_parts.append(line)
                            continue
                        items.extend(self._extract_items(chunk))
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") if isinstance(choice, dict) else None
                            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                                text_parts.append(delta["content"])
                            message = choice.get("message") if isinstance(choice, dict) else None
                            if isinstance(message, dict) and isinstance(message.get("content"), str):
                                text_parts.append(message["content"])
            merged = "".join(text_parts)
            for ref in self._extract_text_refs(merged):
                if ref not in items:
                    items.append(ref)
            if not items:
                raise RuntimeError(f"生图接口没返回图片：{merged[:180] or '空响应'}")
            return await self._download(client, items, count)

    # ---------- 文案 ----------

    def _presets_text(self) -> str:
        presets = self._presets()
        if not presets:
            return "还没有画师串喵，用「画师串 设 名称=内容」加一个吧"
        active = self._active_name()
        items = [f"{'★' if name == active else '·'}{name}" for name in presets]
        lines = ["画师串列表：" + " / ".join(items)]
        lines.append("用法：画师串 用 <名称> / 画师串 设 名称=内容 / 画师串 删 <名称>")
        return "\n".join(lines)

    def _status_text(self) -> str:
        key = str(self._cfg("api_key", "") or "")
        masked = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else ("已配置" if key else "未配置")
        skill = str(self._cfg("skill_path", "") or "")
        mode = self._request_mode()
        return "\n".join(
            [
                "画图配置：",
                f"请求模式：{mode}",
                f"地址：{self._cfg('api_url', '未配置')}",
                f"模型：{self._cfg('model', '未配置')}",
                f"密钥：{masked}",
                f"默认比例：{self._default_ratio()} → {self._resolve_size('')}（2K 手机竖屏）",
                f"chat 尺寸参数：{','.join(self._chat_size_fields()) or '不传'}",
                f"转 tag：{'开' if self._cfg('enable_tag', True) else '关'}（模型：{self._cfg('tag_model', '默认对话模型')}）",
                f"技能文件：{skill}（{'存在' if Path(skill).is_file() else '读不到，用内置规则'}）",
                f"角色联网核对：{'开' if self._lookup_enabled() else '关'}（Danbooru，最少 {self._cfg('lookup_min_posts', 30)} 帖）",
                f"角色收录：{len(self.library)} 个（命中优先，重名走 AI 消歧）",
                f"当前画师串：{self._active_name() or '无'}",
            ]
        )

    # ---------- 指令 ----------

    @filter.regex(DRAW_TRIGGER)
    async def on_draw(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        desc = re.sub(r"^\s*/?(?:文生图|画图|绘图|生图|出图)\s*", "", msg, count=1).strip()
        if desc.startswith(MULTI_WORD):
            async for result in self._draw_multi(event, desc[len(MULTI_WORD):].strip()):
                yield result
            return
        ratio = ""
        matched = RATIO_PREFIX.match(desc)
        if matched:
            ratio = matched.group(1)
            desc = matched.group(2).strip()
        if not desc:
            yield event.plain_result("想画什么呀，给句描述喵")
            return
        yield event.plain_result("收到，先按技能推 tag 再出图喵...")
        try:
            tags = await self._to_tags(event, desc)
            prompt = self._compose_prompt(tags)
            size = self._resolve_size(ratio)
            paths = await self._request_images(prompt, size, self._cfg("count", 1))
        except Exception as exc:
            logger.error("[ayin_paint] 出图失败: %s", exc)
            yield event.plain_result(f"出图失败了喵：{exc}")
            return
        yield event.plain_result(f"tag：{tags[:300]}")
        yield event.chain_result([Image.fromFileSystem(str(path)) for path in paths])

    async def _draw_multi(self, event: AstrMessageEvent, desc: str):
        ratio = ""
        matched = RATIO_PREFIX.match(desc)
        if matched:
            ratio = matched.group(1)
            desc = matched.group(2).strip()
        global_desc, chars = self._parse_multi_parts(desc)
        if not global_desc or not chars:
            yield event.plain_result(
                "格式是：文生图 多角色 全局画面 | 角色1 @B2 | 角色2 @D4 喵"
            )
            return
        yield event.plain_result(f"多角色模式，{len(chars)} 个角色，先推 tag 喵...")
        try:
            prompt, char_payload = await self._to_multi_tags(event, global_desc, chars)
            prompt = self._compose_prompt(prompt)
            size = self._resolve_size(ratio)
            extra = {"use_coords": True, "characters": char_payload}
            paths = await self._request_images(
                prompt, size, self._cfg("count", 1), extra
            )
        except Exception as exc:
            logger.error("[ayin_paint] 多角色出图失败: %s", exc)
            yield event.plain_result(f"出图失败了喵：{exc}")
            return
        brief = " / ".join(
            f"{item['position']}:{item['prompt'][:40]}" for item in char_payload
        )
        yield event.plain_result(f"全局：{prompt[:160]}\n角色：{brief[:300]}")
        yield event.chain_result([Image.fromFileSystem(str(path)) for path in paths])

    @filter.regex(PRESET_TRIGGER)
    async def on_preset(self, event: AstrMessageEvent):
        rest = re.sub(r"^\s*/?画师串\s*", "", event.message_str.strip(), count=1).strip()
        presets = self._presets()
        if not rest:
            yield event.plain_result(self._presets_text())
            return
        parts = rest.split(maxsplit=1)
        action = parts[0]
        payload = parts[1].strip() if len(parts) > 1 else ""
        if action in SET_WORDS:
            body = payload
        elif "=" in rest and action not in DELETE_WORDS:
            body = rest
        else:
            body = ""
        if body:
            name, _, value = body.partition("=")
            name, value = name.strip(), value.strip()
            if not name or not value:
                yield event.plain_result("格式是：画师串 设 名称=内容")
                return
            custom = self.state.setdefault("custom_presets", {})
            custom[name] = value
            self.state["active_preset"] = name
            self._save_state()
            yield event.plain_result(f"画师串「{name}」已保存并启用喵")
            return
        if action in DELETE_WORDS:
            name = payload or ""
            if not name:
                yield event.plain_result("格式是：画师串 删 <名称>")
                return
            custom = self.state.setdefault("custom_presets", {})
            removed = custom.pop(name, None) is not None
            if not removed:
                in_config = name in self._parse_presets(self._cfg("artist_presets", ""))
                yield event.plain_result(
                    f"「{name}」在配置里，去配置删，或者换个名字喵"
                    if in_config
                    else f"没有「{name}」这个画师串喵"
                )
                return
            if self.state.get("active_preset") == name:
                self.state.pop("active_preset", None)
            self._save_state()
            yield event.plain_result(f"已删掉画师串「{name}」喵")
            return
        name = payload if action in USE_WORDS else (payload or action)
        if name not in presets:
            yield event.plain_result(f"没有「{name}」这个画师串喵\n{self._presets_text()}")
            return
        self.state["active_preset"] = name
        self._save_state()
        yield event.plain_result(f"画师串切到「{name}」了喵")

    def _library_text(self) -> str:
        if not self.library:
            return "收录库是空的喵，用「角色收录 加 别名 = tag」加一个"
        lines = [f"角色收录共 {len(self.library)} 个（内置 {len(BUILTIN_LIBRARY)}）："]
        for item in self.library[-20:]:
            mark = "内置" if item.get("builtin") else "自定义"
            series = f"［{item['series']}］" if item.get("series") else ""
            lines.append(f"·{item['tag']}{series} {mark} ← {'/'.join(item['aliases'][:3])}")
        lines.append("用法：角色收录 [列表] / 角色收录 加 别名1/别名2 = tag = 作品 / 角色收录 删 别名")
        return chr(10).join(lines)

    def _library_add(self, payload: str) -> str:
        body = str(payload or "").replace("＝", "=").strip()
        chunks = [piece.strip() for piece in body.split("=")]
        if len(chunks) < 2 or not chunks[0] or not chunks[1]:
            return "格式：角色收录 加 初音未来/初音/miku = hatsune_miku_(vocaloid) = VOCALOID"
        aliases = [a.strip() for a in re.split("[/,，、|]", chunks[0]) if a.strip()]
        entry = self._normalize_entry(
            {"aliases": aliases, "tag": chunks[1], "series": chunks[2] if len(chunks) > 2 else ""}
        )
        if not entry:
            return "这个 tag 看起来不对喵"
        for index, item in enumerate(self.custom_library):
            if item["tag"] == entry["tag"]:
                self.custom_library[index] = entry
                self._save_library()
                self._load_library()
                return f"已更新收录「{entry['tag']}」喵"
        self.custom_library.append(entry)
        self._save_library()
        self._load_library()
        return (
            f"已收录「{entry['tag']}」，以后描述里出现 "
            f"{'/'.join(entry['aliases'][:4])} 会优先用它喵"
        )

    def _library_delete(self, payload: str) -> str:
        key = self._norm_name(payload)
        if not key:
            return "格式：角色收录 删 别名或 tag"
        for index, item in enumerate(self.custom_library):
            if key == self._norm_name(item["tag"]) or key in [
                self._norm_name(alias) for alias in item["aliases"]
            ]:
                self.custom_library.pop(index)
                self._save_library()
                self._load_library()
                return f"已删掉收录「{payload}」喵"
        for item in self.library:
            if not item.get("builtin"):
                continue
            if key == self._norm_name(item["tag"]) or key in [
                self._norm_name(alias) for alias in item["aliases"]
            ]:
                return f"「{item['tag']}」是内置收录，删不掉喵"
        return f"收录库里没有「{payload}」喵"

    @filter.regex(LIBRARY_TRIGGER)
    async def on_library(self, event: AstrMessageEvent):
        rest = re.sub(
            "^[ ]*/?(?:角色收录|收录角色|角色库|角色列表)[ ]*",
            "",
            event.message_str.strip(),
            count=1,
        ).strip()
        if not rest or rest in ("列表", "查看", "ls", "list"):
            yield event.plain_result(self._library_text())
            return
        parts = rest.split(maxsplit=1)
        action = parts[0]
        payload = parts[1].strip() if len(parts) > 1 else ""
        if action in SET_WORDS or "=" in rest:
            yield event.plain_result(self._library_add(rest))
            return
        if action in DELETE_WORDS:
            yield event.plain_result(self._library_delete(payload))
            return
        yield event.plain_result(self._library_text())

    @filter.regex(STATUS_TRIGGER)
    async def on_status(self, event: AstrMessageEvent):
        yield event.plain_result(self._status_text())

    @filter.llm_tool(name="paint_image")
    async def paint_image(self, event: AstrMessageEvent, prompt: str, aspect_ratio: str = ""):
        """按 Danbooru 技能把描述转成 tag 并生成图片，返回生成的图片。

        Args:
            prompt(string): 画面描述，中英文都行
            aspect_ratio(string): 出图比例，如 1:1、3:4、16:9，可留空
        """
        try:
            tags = await self._to_tags(event, prompt)
            full_prompt = self._compose_prompt(tags)
            size = self._resolve_size(aspect_ratio)
            paths = await self._request_images(full_prompt, size, self._cfg("count", 1))
        except Exception as exc:
            logger.error("[ayin_paint] 工具出图失败: %s", exc)
            return f"出图失败：{exc}"
        try:
            await event.send(
                event.chain_result([Image.fromFileSystem(str(path)) for path in paths])
            )
        except Exception as exc:
            logger.warning("[ayin_paint] 发送图片失败: %s", exc)
        return f"已生成 {len(paths)} 张图片。使用 tag：{tags[:200]}"
