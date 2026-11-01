"""
main.py —— 身份认同插件主入口（v2.0.2 修复版）

修复重点：
1. 移除 fallback 数据目录，统一使用 StarTools.get_data_dir()
2. Web API 从局部函数改为实例方法，确保 self 绑定正确
3. 所有操作均使用官方 event 获取 API
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Star, Context, StarTools
from astrbot.core.provider.entities import ProviderRequest
from astrbot.api import logger
from quart import jsonify, request as quart_request
from .identity_manager import IdentityManager


PLUGIN_NAME = "astrbot_plugin_identity"


class IdentityPlugin(Star):
    """身份认同插件 — 纯数据驱动"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self._im: IdentityManager | None = None
        self._config: dict = config or {}
        self._admin_ids: set[str] = set()
        self._auto_register: bool = True
        self._data_dir: str = ""

    async def initialize(self) -> None:
        """Star.initialize —— 插件激活时调用"""
        # 1) 数据目录（不 fallback，使用官方 StarTools）
        self._data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        import os
        os.makedirs(str(self._data_dir), exist_ok=True)

        # 2) IdentityManager
        self._im = IdentityManager(str(self._data_dir))

        # 3) 从 extra.config 读取配置
        self._admin_ids = set(
            str(x) for x in self._config.get("admin_ids", ["1491867183"])
        )
        self._auto_register = self._config.get("auto_register_enabled", True)

        # 4) 注册 Web API
        self._register_web_apis()

        logger.info(
            f"[身份插件] v2.0.2 初始化完成, "
            f"数据: {self._im.storage_path}, "
            f"记录数: {len(self._im.list_all())}, "
            f"自动注册: {self._auto_register}"
        )

    async def terminate(self) -> None:
        if self._im:
            del self._im
            self._im = None

    # ──────── 属性 ────────

    @property
    def im(self) -> IdentityManager:
        if self._im is None:
            raise RuntimeError("IdentityManager 尚未初始化，请检查插件是否激活")
        return self._im

    # ──────── 内部工具 ────────

    @staticmethod
    def _uid(event: AstrMessageEvent) -> str:
        return event.get_sender_id() or "unknown"

    @staticmethod
    def _nick(event: AstrMessageEvent) -> str:
        return event.get_sender_name() or event.get_sender_id() or "anonymous"

    # ──────── Web API 注册 ────────

    def _register_web_apis(self):
        """注册页面需要的后端 API（使用实例方法而非局部函数）"""
        self.context.register_web_api(
            "/astrbot_plugin_identity/list", self.api_list, ["GET"], "获取全部身份记录"
        )
        self.context.register_web_api(
            "/astrbot_plugin_identity/add", self.api_add, ["POST"], "添加/更新身份"
        )
        self.context.register_web_api(
            "/astrbot_plugin_identity/update", self.api_update, ["POST"], "更新身份"
        )
        self.context.register_web_api(
            "/astrbot_plugin_identity/delete", self.api_delete, ["POST"], "删除身份"
        )
        self.context.register_web_api(
            "/astrbot_plugin_identity/stats", self.api_stats, ["GET"], "身份统计"
        )
        logger.info("[身份插件] Web API 已注册")

    # ──────── Web API 方法（显式绑定 self，确保数据一致） ────────

    async def api_list(self):
        """获取全部身份记录"""
        records = self.im.page_list()
        return jsonify({"ok": True, "data": records})

    async def api_add(self):
        """添加/更新身份"""
        body = await quart_request.get_json(silent=True) or {}
        result = self.im.page_add(body)
        return jsonify(result)

    async def api_update(self):
        """更新身份"""
        body = await quart_request.get_json(silent=True) or {}
        result = self.im.page_update(body)
        return jsonify(result)

    async def api_delete(self):
        """删除身份"""
        body = await quart_request.get_json(silent=True) or {}
        uid = body.get("user_id", "")
        logger.info(f"[身份插件] api_delete 收到请求, user_id={uid}")
        result = self.im.page_delete(uid)
        logger.info(f"[身份插件] api_delete 成功, result={result}")
        return jsonify(result)

    async def api_stats(self):
        """身份统计"""
        stats = self.im.stats()
        return jsonify(stats)

    # ──────── LLM 请求拦截 —— 注入身份标签 ────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        user_id = self._uid(event)
        nick = self._nick(event)

        # 查询 / 自动注册
        ok, record = self.im.check(user_id)
        if not ok:
            if self._auto_register:
                record = self.im.auto_register(user_id, nick)
            else:
                # 未开启自动注册，且未找到记录 → 不注入
                return
        elif record and record.get("nickname", "") != nick:
            self.im.update(user_id, nickname=nick)
            record["nickname"] = nick

        # 构造提示词
        identity = record.get("identity", "普通用户")
        per_prompt = (record.get("prompt") or "").strip()
        base = self._config.get(
            "success_prompt", "用户身份已确认。请根据用户标签回复。"
        )
        lines = [f"[身份标签: {identity}] {base}"]
        if per_prompt:
            lines.append(f"[个性提示: {per_prompt}]")
        tag_text = "\n".join(lines)

        from astrbot.core.agent.message import TextPart
        tp = TextPart(text=f"\n{tag_text}\n")
        tp.mark_as_temp()
        req.extra_user_content_parts.append(tp)

        logger.info(f"[身份插件] 注入: {user_id}({nick}) → {identity}")

    # ──────── 管理命令 ────────

    @filter.command("identity")
    async def cmd_identity(self, event: AstrMessageEvent):
        text = event.get_plain_text().strip()
        parts = text.split()

        # 权限检查
        if self._uid(event) not in self._admin_ids:
            yield event.plain_result("❌ 你没有管理员权限喵～")
            return

        # 帮助
        if not parts or parts[0] in ("帮助", "help", "-h"):
            yield event.plain_result(HELP_TEXT)
            return

        sub = parts[0]

        # 添加
        if sub in ("添加", "add"):
            if len(parts) < 4:
                yield event.plain_result("⚠️ /identity 添加 <QQ号> <昵称> <身份>")
                return
            qq, nick, ident = parts[1], parts[2], " ".join(parts[3:])
            ok = self.im.add(qq, nick, ident)
            yield event.plain_result(
                f"✅ 已添加：{qq}({nick}) → {ident}" if ok
                else f"⚠️ {qq} 已有记录"
            )

        # 删除
        elif sub in ("删除", "del"):
            if len(parts) < 2:
                yield event.plain_result("⚠️ /identity 删除 <QQ号>")
                return
            ok = self.im.delete(parts[1])
            yield event.plain_result(
                f"✅ 已删除 {parts[1]}" if ok else f"❌ 未找到 {parts[1]}"
            )

        # 更新
        elif sub in ("更新", "update"):
            if len(parts) < 4:
                yield event.plain_result("⚠️ /identity 更新 <QQ号> <新昵称> <新身份>")
                return
            ok = self.im.update(parts[1], parts[2], " ".join(parts[3:]))
            yield event.plain_result(
                f"✅ 已更新 {parts[1]}" if ok else f"❌ 未找到 {parts[1]}"
            )

        # 提示词
        elif sub in ("提示词", "prompt"):
            if len(parts) < 2:
                yield event.plain_result("⚠️ /identity 提示词 <QQ号> [内容]")
                return
            p = " ".join(parts[2:]) if len(parts) >= 3 else ""
            if p in ("无", "空", "清除", "clear", "none"):
                p = ""
            ok = self.im.set_prompt(parts[1], p)
            yield event.plain_result(
                f"✅ 已{'设置' if p else '清除'} {parts[1]} 的提示词" if ok
                else f"❌ 未找到 {parts[1]}"
            )

        # 列表
        elif sub in ("列表", "list"):
            data = self.im.list_all()
            if not data:
                yield event.plain_result("📭 暂无身份记录")
                return
            lines = ["📋 身份映射："]
            for i, r in enumerate(data, 1):
                ptag = (
                    f" [提示词:{r['prompt'][:20]}…]"
                    if r.get("prompt") else ""
                )
                lines.append(
                    f"  {i}. {r['user_id']}({r['nickname']}) → {r['identity']}{ptag}"
                )
            yield event.plain_result("\n".join(lines))

        # 查询
        elif sub in ("查询", "check"):
            if len(parts) < 2:
                yield event.plain_result("⚠️ /identity 查询 <QQ号>")
                return
            ok, r = self.im.check(parts[1])
            if ok and r:
                p = f"\n   提示词: {r['prompt']}" if r.get("prompt") else ""
                yield event.plain_result(
                    f"✅ {r['user_id']}({r['nickname']}) → {r['identity']}{p}"
                )
            else:
                yield event.plain_result(f"❌ 未找到 {parts[1]}")

        else:
            yield event.plain_result(f"❌ 未知子命令「{sub}」，发送 /identity 查看帮助")


HELP_TEXT = """🔐 身份认同插件 v2.0.2

/identity 添加 <QQ号> <昵称> <身份>
/identity 删除 <QQ号>
/identity 更新 <QQ号> <新昵称> <新身份>
/identity 提示词 <QQ号> [内容]
/identity 列表
/identity 查询 <QQ号>

额外配置（WebUI 插件配置页）：
- admin_ids: 管理员QQ号
- success_prompt: LLM注入提示词前缀
- auto_register_enabled: 是否自动注册新用户
"""
