"""刷B站 · AstrBot 移植版

移植自 DSH 插件 c1overzz/dsh-bili-scroll：
按关键词搜 B 站视频 -> 逐条读字幕原文判断 -> 写一份日报 -> 值得的留一条评论 @ 你。
B 站能力由 bili-cli（kukemc/bili-cli）提供，全部走 JSON 输出。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime
from functools import wraps
from typing import Any

from astrbot.api import logger
from astrbot.api.all import (
    AstrBotConfig,
    AstrMessageEvent,
    Context,
    Image,
    MessageChain,
    Plain,
    Star,
    register,
)
from astrbot.api.event import filter

PLUGIN_NAME = "astrbot_plugin_bili_duty"
PLUGIN_VERSION = "1.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_DIR = os.path.join(DATA_DIR, "out")
SEEN_FILE = os.path.join(DATA_DIR, "seen.json")
TARGET_FILE = os.path.join(DATA_DIR, "target.json")
LOG_FILE = os.path.join(DATA_DIR, "duty.log")

DEFAULT_KEYWORDS = ["AI Agent", "编程", "DeepSeek"]
DEFAULT_ADMIN_IDS = ["1491867183"]


def _owner_only(func):
    """命令仅对管理员 ID 生效，其他任何人发都不响应具体功能。"""

    @wraps(func)
    async def wrapper(self, event, *args, **kwargs):
        try:
            sender = str(event.get_sender_id())
        except Exception:  # noqa: BLE001
            sender = ""
        if sender not in self._admin_ids():
            yield event.plain_result("喵，这条命令只有主人能碰。")
            return
        async for item in func(self, event, *args, **kwargs):
            yield item

    return wrapper



def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    line = f"[{_now()}] {msg}"
    logger.info(f"[BiliDuty] {msg}")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _split_text(text: str, size: int = 1200) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts, buf = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:size])
            line = line[size:]
        if len(buf) + len(line) + 1 > size and buf:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


@register(PLUGIN_NAME, "小伊", "每天替你刷一轮 B 站：读字幕挑值得的、写日报，值得的 @ 你", PLUGIN_VERSION)
class BiliDutyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}
        os.makedirs(OUT_DIR, exist_ok=True)
        self.bili_bin = self._resolve_bin()
        self._loop_task: asyncio.Task | None = None
        self._running = False
        self._fired: set[str] = set()

    # ---------------- 基础 ----------------
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            value = default
        return default if value is None or value == "" else value

    def _admin_ids(self) -> set[str]:
        raw = self._cfg("admin_ids", DEFAULT_ADMIN_IDS)
        if isinstance(raw, str):
            raw = [x for x in re.split(r"[,，\s]+", raw) if x]
        return {str(x).strip() for x in (raw or []) if str(x).strip()}

    def _keywords(self) -> list[str]:
        raw = self._cfg("keywords", DEFAULT_KEYWORDS)
        if isinstance(raw, str):
            raw = [x.strip() for x in re.split(r"[\n,，]", raw) if x.strip()]
        return [str(x).strip() for x in (raw or DEFAULT_KEYWORDS) if str(x).strip()]

    def _resolve_bin(self) -> str:
        configured = str(self._cfg("bili_bin", "") or "").strip()
        if configured and os.path.exists(configured):
            return configured
        for cand in (
            configured,
            shutil.which("bili") or "",
            os.path.expanduser("~/.local/bin/bili"),
            "/usr/local/bin/bili",
        ):
            if cand and os.path.exists(cand):
                return cand
        return configured or "bili"

    async def _bili(self, *args: str, timeout: int = 180) -> dict:
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(self.bili_bin) + ":" + env.get("PATH", "")
        try:
            proc = await asyncio.create_subprocess_exec(
                self.bili_bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": {"code": "timeout", "message": "bili 命令超时"}}
        except FileNotFoundError:
            return {"ok": False, "error": {"code": "no_bin", "message": f"找不到 bili：{self.bili_bin}"}}
        text = (out or b"").decode("utf-8", "ignore").strip()
        if not text:
            text = (err or b"").decode("utf-8", "ignore").strip()
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
        return {"ok": False, "error": {"code": "parse", "message": text[:200] or "bili 无输出"}}

    async def _llm(self, prompt: str, system_prompt: str = "", max_tokens: int = 1200) -> str:
        kwargs: dict[str, Any] = {"prompt": prompt, "max_tokens": max_tokens}
        pid = str(self._cfg("provider_id", "") or "").strip()
        if not pid:
            try:
                pid = str(
                    ((self.context.get_config() or {}).get("provider_settings", {}) or {}).get(
                        "default_provider_id", ""
                    )
                    or ""
                ).strip()
            except Exception:  # noqa: BLE001
                pid = ""
        if not pid:
            _log("没有可用的模型 Provider，跳过本次生成")
            return ""
        kwargs["chat_provider_id"] = pid
        if system_prompt:
            kwargs["system_prompt"] = system_prompt
        try:
            resp = await self.context.llm_generate(**kwargs)
            return str(getattr(resp, "completion_text", "") or "").strip()
        except Exception as exc:  # noqa: BLE001
            _log(f"模型调用失败：{str(exc)[:160]}")
            return ""

    # ---------------- 生命周期 ----------------
    async def initialize(self) -> None:
        self._loop_task = asyncio.create_task(self._scheduler())
        _log("插件已启动，bili=%s" % self.bili_bin)

    async def terminate(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        _log("插件已停止")

    async def _scheduler(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                if bool(self._cfg("enabled", True)):
                    times = [t.strip() for t in str(self._cfg("run_times", "12:30,21:00")).split(",") if t.strip()]
                    now = datetime.now()
                    hhmm = now.strftime("%H:%M")
                    key = f"{now.strftime('%Y-%m-%d')} {hhmm}"
                    if hhmm in times and key not in self._fired:
                        self._fired.add(key)
                        if len(self._fired) > 40:
                            self._fired = set(list(self._fired)[-20:])
                        _log(f"到达定时点 {hhmm}，开始自动刷")
                        asyncio.create_task(self._auto_round())
            except Exception as exc:  # noqa: BLE001
                _log(f"调度异常：{str(exc)[:160]}")
            await asyncio.sleep(30)

    # ---------------- 会话记录 ----------------
    def _remember_target(self, umo: str) -> None:
        if not umo:
            return
        _write_json(TARGET_FILE, {"umo": umo, "ts": int(time.time())})

    def _target(self) -> str:
        configured = str(self._cfg("target_session", "") or "").strip()
        if configured:
            return configured
        return str(_read_json(TARGET_FILE, {}).get("umo", "") or "")

    async def _push(self, umo: str, text: str) -> None:
        if not umo:
            _log("没有可用的推送会话，日报只写到本地")
            return
        for chunk in _split_text(text):
            try:
                await self.context.send_message(umo, MessageChain([Plain(chunk)]))
            except Exception as exc:  # noqa: BLE001
                _log(f"推送失败：{str(exc)[:160]}")
                return

    # ---------------- 命令 ----------------
    @filter.command("b站登录")
    @_owner_only
    async def cmd_login(self, event: AstrMessageEvent):
        """扫码登录 B 站账号"""
        res = await self._bili("auth", "login", timeout=60)
        data = res.get("data") or {}
        if not res.get("ok"):
            err = (res.get("error") or {}).get("message", "登录失败")
            yield event.plain_result(f"二维码没出来喵：{err}")
            return
        qr = data.get("qr_image") or ""
        if qr and os.path.exists(qr):
            yield event.chain_result([Image.fromFileSystem(qr)])
        else:
            yield event.plain_result(str(data.get("qr_url", "没拿到二维码")))
        yield event.plain_result("用 B 站 App 扫一下，记得选「记住我」，然后说一声喵。")
        status = await self._bili("auth", "status", "--wait", "150", timeout=170)
        sdata = status.get("data") or {}
        if sdata.get("logged_in"):
            name = sdata.get("name") or sdata.get("uname") or "你的账号"
            yield event.plain_result(f"登录成功喵，{name} 已绑定。")
        else:
            yield event.plain_result("还没等到确认，稍后再来一次 /b站登录 喵。")

    @filter.command("b站cookie登录")
    @_owner_only
    async def cmd_cookie_login(self, event: AstrMessageEvent):
        """用浏览器 Cookie 直接登录，绕开扫码"""
        raw = str(getattr(event, "message_str", "") or "")
        raw = re.sub(r"^[/／]?\s*b站cookie登录\s*", "", raw).strip()
        if not raw:
            yield event.plain_result(
                "把 Cookie 一起发过来喵：/b站cookie登录 SESSDATA=xx; bili_jct=yy; DedeUserID=123"
            )
            return
        pairs: dict[str, str] = {}
        for item in raw.replace("\uff1b", ";").replace("\r", ";").replace("\n", ";").split(";"):
            if "=" in item:
                key, val = item.split("=", 1)
                pairs[key.strip().lower()] = val.strip()
        sess = pairs.get("sessdata", "")
        jct = pairs.get("bili_jct", "")
        if not sess or not jct:
            yield event.plain_result("少了 SESSDATA 或 bili_jct 喵，这两个是必须的。")
            return
        cred = {
            "sessdata": sess,
            "bili_jct": jct,
            "buvid3": pairs.get("buvid3", ""),
            "buvid4": pairs.get("buvid4", ""),
            "dedeuserid": pairs.get("dedeuserid", "") or pairs.get("dedeuid", ""),
            "ac_time_value": pairs.get("ac_time_value", ""),
        }
        path = os.path.join(os.path.expanduser("~"), ".config", "bili-cli", "credential.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cred, fh, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"写凭证没成功喵：{str(exc)[:80]}")
            return
        res = await self._bili("auth", "status", timeout=60)
        data = res.get("data") or {}
        if data.get("logged_in"):
            name = data.get("name") or data.get("uname") or "你的账号"
            yield event.plain_result(f"登录成功喵，{name} 已绑定。")
        else:
            yield event.plain_result("凭证写好了，但校验没过喵，大概 SESSDATA 已经过期。")

    @filter.command("b站状态")
    @_owner_only
    async def cmd_status(self, event: AstrMessageEvent):
        """查看登录与配置状态"""
        res = await self._bili("auth", "status", timeout=60)
        data = res.get("data") or {}
        logged = "已登录" if data.get("logged_in") else "未登录"
        lines = [
            f"账号：{logged}",
            f"定时：{self._cfg('run_times', '12:30,21:00')}",
            f"关键词：{'、'.join(self._keywords())}",
            f"每轮：看 {self._cfg('scan_limit', 8)} 条，挑 {self._cfg('daily_pick', 3)} 条",
            f"自动评论：{'开' if self._cfg('auto_comment', False) else '关'}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("刷b站")
    @_owner_only
    async def cmd_run(self, event: AstrMessageEvent):
        """立刻跑一轮"""
        self._remember_target(event.unified_msg_origin)
        if self._running:
            yield event.plain_result("上一轮还在跑喵，等等。")
            return
        yield event.plain_result("开始刷了喵，读字幕要花点时间。")
        try:
            report = await self.run_round(umo=event.unified_msg_origin, push=False)
        except Exception as exc:  # noqa: BLE001
            _log(f"手动一轮异常：{str(exc)[:200]}")
            yield event.plain_result(f"这轮崩了喵：{str(exc)[:120]}")
            return
        if not report:
            yield event.plain_result("今天没挑出值得的喵。")
            return
        for chunk in _split_text(report):
            yield event.plain_result(chunk)

    @filter.command("b站日报")
    @_owner_only
    async def cmd_report(self, event: AstrMessageEvent):
        """看最近一份日报"""
        files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".md")) if os.path.isdir(OUT_DIR) else []
        if not files:
            yield event.plain_result("还没有日报喵，先 /刷b站 来一轮。")
            return
        with open(os.path.join(OUT_DIR, files[-1]), "r", encoding="utf-8") as f:
            text = f.read()
        for chunk in _split_text(text):
            yield event.plain_result(chunk)

    # ---------------- 一轮 ----------------
    async def _auto_round(self) -> None:
        umo = self._target()
        if not umo:
            _log("没有记录到推送会话，跳过自动轮")
            return
        try:
            report = await self.run_round(umo=umo, push=False)
        except Exception as exc:  # noqa: BLE001
            _log(f"自动一轮异常：{str(exc)[:200]}")
            return
        if report and str(self._cfg("report_style", "full")) != "none":
            await self._push(umo, report)

    async def _search(self, keyword: str) -> list[dict]:
        size = max(1, min(50, int(self._cfg("search_size", 8))))
        res = await self._bili("search", "video", keyword, "--order", "new", "--size", str(size))
        if not res.get("ok"):
            err = (res.get("error") or {}).get("message", "")
            _log(f"搜索「{keyword}」失败：{err[:120]}")
            return []
        return list((res.get("data") or {}).get("items") or [])

    async def _watch(self, bvid: str) -> dict | None:
        max_chars = str(max(200, int(self._cfg("max_chars", 1500))))
        res = await self._bili(
            "video", "watch", bvid,
            "--frames", "0", "--comments", "2", "--max-chars", max_chars,
            timeout=150,
        )
        if res.get("ok"):
            return res.get("data") or {}
        code = (res.get("error") or {}).get("code", "")
        if code == "risk_control":
            await asyncio.sleep(4)
            res = await self._bili(
                "video", "watch", bvid,
                "--frames", "0", "--comments", "2", "--max-chars", max_chars,
                timeout=150,
            )
            if res.get("ok"):
                return res.get("data") or {}
        _log(f"看 {bvid} 失败：{(res.get('error') or {}).get('message', '')[:100]}")
        return None

    @staticmethod
    def _dig(data: dict, *keys: str) -> str:
        for key in keys:
            cur: Any = data
            ok = True
            for part in key.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, str) and cur.strip():
                return cur.strip()
        return ""

    def _candidate_block(self, item: dict, detail: dict | None) -> str:
        detail = detail or {}
        title = str(detail.get("title") or item.get("title") or "")
        author = ""
        if isinstance(item.get("author"), dict):
            author = str(item["author"].get("name") or "")
        if not author and isinstance(detail.get("owner"), dict):
            author = str(detail["owner"].get("name") or "")
        play = item.get("play") or (detail.get("stat") or {}).get("view") or 0
        desc = str(detail.get("desc") or item.get("desc") or "")[:300]
        ai_raw = detail.get("ai_summary")
        ai = ai_raw[:400] if isinstance(ai_raw, str) else ""
        transcript = self._dig(detail, "transcript.text")
        transcript = str(transcript)[: max(200, int(self._cfg("max_chars", 1500)))]
        pub = item.get("pubdate") or detail.get("pubdate", "")
        return (
            f"BV：{item.get('bvid')}\n标题：{title}\nUP：{author}\n"
            f"播放：{play}　发布：{pub}\n"
            f"简介：{desc}\n站内摘要：{ai or '（无）'}\n"
            f"字幕正文：{transcript or '（没读到字幕）'}"
        )

    async def run_round(self, umo: str = "", push: bool = False) -> str:
        if self._running:
            return "上一轮还在跑喵"
        self._running = True
        try:
            seen = _read_json(SEEN_FILE, {})
            keywords = self._keywords()
            scan_limit = max(1, int(self._cfg("scan_limit", 8)))
            daily_pick = max(1, int(self._cfg("daily_pick", 3)))

            candidates: list[dict] = []
            seen_bv: set[str] = set()
            for kw in keywords:
                for item in await self._search(kw):
                    bvid = str(item.get("bvid") or "")
                    if not bvid or bvid in seen_bv or bvid in seen:
                        continue
                    seen_bv.add(bvid)
                    candidates.append(item)
                if len(candidates) >= scan_limit * 3:
                    break
            _log(f"候选 {len(candidates)} 条，关键词 {keywords}")
            if not candidates:
                return ""

            blocks: list[str] = []
            picked_pool: list[dict] = []
            for item in candidates[:scan_limit]:
                detail = await self._watch(str(item.get("bvid")))
                blocks.append(self._candidate_block(item, detail))
                picked_pool.append(item)
                await asyncio.sleep(1.5)

            persona = str(self._cfg("persona", "") or "").strip()
            style = str(self._cfg("report_style", "full"))
            system_prompt = (
                (persona + "\n\n" if persona else "")
                + "你在替主人筛 B 站视频。只依据给出的标题、简介和字幕原文判断，读不到内容就不要猜。"
                "宁缺毋滥，宁可少挑也不要凑数。输出 JSON，不要解释。"
            )
            prompt = (
                f"下面是今天的候选视频：\n\n" + "\n\n---\n\n".join(blocks)
                + f"\n\n请从中挑出最值得看的 {daily_pick} 条以内，只输出 JSON："
                '{"picks":[{"bvid":"BV号","why":"为什么值得看，40字内","line":"你的一句话点评，25字内"}]}\n'
                f"日报风格：{style}。挑不出来就返回空数组。"
            )
            raw = await self._llm(prompt, system_prompt=system_prompt, max_tokens=1200)
            if not str(raw or "").strip():
                _log("模型没有返回内容，本轮不标记已看，稍后重试")
                return ""
            picks = self._parse_picks(raw, [str(x.get("bvid")) for x in picked_pool])
            picks = picks[:daily_pick]
            if not picks:
                _log("模型没挑出值得的")
                for item in picked_pool:
                    seen[str(item.get("bvid"))] = int(time.time())
                _write_json(SEEN_FILE, seen)
                return ""

            by_bv = {str(x.get("bvid")): x for x in picked_pool}
            lines = [f"## 今日刷到的（{datetime.now().strftime('%m-%d')}）", ""]
            for pick in picks:
                bvid = pick["bvid"]
                item = by_bv.get(bvid, {})
                title = str(item.get("title") or "")
                url = f"https://www.bilibili.com/video/{bvid}"
                if style == "brief":
                    lines.append(f"- [{title}]({url})：{pick.get('why', '')}")
                else:
                    lines += [
                        f"### {bvid}",
                        f"**{title}**",
                        f"{url}",
                        f"- 为什么值得：{pick.get('why', '')}",
                        f"- 一句话：{pick.get('line', '')}",
                        "",
                    ]
            report = "\n".join(lines).strip()

            path = os.path.join(OUT_DIR, datetime.now().strftime("%Y-%m-%d") + ".md")
            prev = ""
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    prev = f.read().rstrip()
            body = "\n".join(lines[2:]).strip()
            if prev:
                rounds = prev.count("## 第 ") + 2
                block = f"\n\n## 第 {rounds} 轮（{datetime.now().strftime('%H:%M')}）\n\n{body}"
            else:
                block = report
            with open(path, "w", encoding="utf-8") as f:
                f.write((prev + "\n\n" + block).strip() + "\n")

            if bool(self._cfg("auto_comment", False)):
                await self._comment(picks, by_bv)

            for item in picked_pool:
                seen[str(item.get("bvid"))] = int(time.time())
            _write_json(SEEN_FILE, seen)
            _log(f"本轮挑出 {len(picks)} 条")
            if push and umo:
                await self._push(umo, report)
            return report
        finally:
            self._running = False

    @staticmethod
    def _parse_picks(raw: str, allowed: list[str]) -> list[dict]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
        data = None
        try:
            data = json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except Exception:
                    data = None
        picks = []
        for row in (data or {}).get("picks", []) if isinstance(data, dict) else []:
            bvid = str(row.get("bvid") or "").strip()
            if bvid in allowed:
                picks.append(
                    {
                        "bvid": bvid,
                        "why": str(row.get("why") or "").strip()[:80],
                        "line": str(row.get("line") or "").strip()[:60],
                    }
                )
        return picks

    async def _comment(self, picks: list[dict], by_bv: dict) -> None:
        limit = max(1, min(20, int(self._cfg("comment_limit", 1))))
        at_uid = str(self._cfg("at_uid", "") or "").strip()
        for pick in picks[:limit]:
            text = pick.get("line") or pick.get("why") or "这条值得一看"
            args = ["comment", "post", pick["bvid"], text]
            if at_uid:
                args += ["--at", at_uid]
            res = await self._bili(*args, timeout=90)
            if res.get("ok"):
                _log(f"已评论 {pick['bvid']}")
            else:
                _log(f"评论 {pick['bvid']} 失败：{(res.get('error') or {}).get('message', '')[:100]}")
            await asyncio.sleep(3)
