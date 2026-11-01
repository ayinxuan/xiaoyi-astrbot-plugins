# -*- coding: utf-8 -*-
"""
AstrBot 小游戏合集插件 - 本地渲染版
去掉了军棋，加入测试对局命令
"""
from datetime import datetime, timedelta
from typing import Dict, Optional
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .games.tictactoe import TicTacToeManager, Player
from .games.go import GoManager, Stone, COLUMN_LABELS as GO_COLUMN_LABELS
from .games.xiangqi import XiangqiManager, Side, PIECE_CODES
from .games.gomoku import GomokuManager, Stone as GomokuStone, COLUMN_LABELS
from .services.image_renderer import GameRenderer

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("APScheduler 未安装，超时功能将不可用")


@register("game", "riddle", "QQ 群小游戏合集", "1.1.0")
class GamePlugin(Star):
    """小游戏合集插件"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config else {}

        # 初始化游戏管理器（去掉军棋）
        self.ttt = TicTacToeManager()
        self.go = GoManager()
        self.xiangqi = XiangqiManager()
        self.gomoku = GomokuManager()

        # 本地渲染器（不依赖API）
        self.renderer = GameRenderer()

        # 管理员配置
        self.admin_qq = str(self.config.get("admin_qq", ""))
        self.admin_mode = self.config.get("admin_mode", True)

        # 超时配置
        self._timeout_seconds = self.config.get("turn_timeout_seconds", 60)
        self._join_timeout_seconds = self.config.get("join_timeout_seconds", 60)

        # 管理员测试模式状态
        self._admin_testing: Dict[str, bool] = {}
        # 将军提示开关（默认开）
        self._check_check: bool = True
        # AI对局公开开关（默认关）
        self._ai_open: bool = False

        # 缓存
        self._player_names: dict = {}
        self._last_msg_ids: Dict[str, int] = {}
        self._group_umo: Dict[str, any] = {}
        self._group_bots: Dict[str, any] = {}

        # 调度器
        self._scheduler = None
        if HAS_APSCHEDULER:
            self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
            self._scheduler.start()

        logger.info(f"小游戏插件已加载 | 管理员: {self.admin_qq} | 超时: {self._timeout_seconds}s")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查是否是管理员"""
        if not self.admin_mode or not self.admin_qq:
            return False
        return str(event.get_sender_id()) == self.admin_qq

    def _get_session_id(self, event) -> str:
        """获取会话ID，支持群聊和私聊"""
        gid = event.get_group_id()
        if gid:
            return str(gid)
        return f"private_{event.get_sender_id()}"

    def _get_game_and_session(self, event, mgr):
        """获取当前会话中的游戏，自动匹配普通测试和AI测试"""
        sid = str(event.get_sender_id())
        for prefix in ["private_ai_", "private_"]:
            gid = f"{prefix}{sid}"
            game = mgr.get_game(gid)
            if game and not game.is_finished:
                return gid, game
        gid = event.get_group_id()
        if gid:
            gid = str(gid)
            game = mgr.get_game(gid)
            if game and not game.is_finished:
                return gid, game
        return None, None

    async def _recall_last_message(self, group_id: str, bot):
        msg_id = self._last_msg_ids.get(group_id)
        if msg_id and bot:
            try:
                await bot.api.call_action('delete_msg', message_id=msg_id)
            except Exception:
                pass
            self._last_msg_ids.pop(group_id, None)

    async def _send_image_and_save_id(self, group_id: str, img_path: str, bot) -> bool:
        if not bot:
            return False
        try:
            import base64
            with open(img_path, 'rb') as f:
                img_data = base64.b64encode(f.read()).decode('utf-8')
            result = await bot.api.call_action(
                'send_group_msg',
                group_id=int(group_id),
                message=[{"type": "image", "data": {"file": f"base64://{img_data}"}}]
            )
            if result and 'message_id' in result:
                self._last_msg_ids[group_id] = result['message_id']
            return True
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
            return False

    def _save_bot_client(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else f"private_{event.get_sender_id()}"
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    self._group_bots[group_id] = event.bot
                    self._group_umo[group_id] = event.unified_msg_origin
            except Exception:
                pass

    def _schedule_timeout(self, group_id: str, game_type: str):
        if not self._scheduler or not self._timeout_seconds:
            return
        job_id = f"timeout_{group_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        self._scheduler.add_job(
            self._handle_timeout, 'date',
            run_date=datetime.now() + timedelta(seconds=self._timeout_seconds),
            args=[group_id, game_type], id=job_id, replace_existing=True, misfire_grace_time=30
        )

    def _schedule_join_timeout(self, group_id: str, game_type: str):
        if not self._scheduler or not self._join_timeout_seconds:
            return
        job_id = f"join_timeout_{group_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        self._scheduler.add_job(
            self._handle_join_timeout, 'date',
            run_date=datetime.now() + timedelta(seconds=self._join_timeout_seconds),
            args=[group_id, game_type], id=job_id, replace_existing=True, misfire_grace_time=30
        )

    def _cancel_timeout(self, group_id: str):
        if not self._scheduler:
            return
        try:
            self._scheduler.remove_job(f"timeout_{group_id}")
        except Exception:
            pass

    def _cancel_join_timeout(self, group_id: str):
        if not self._scheduler:
            return
        try:
            self._scheduler.remove_job(f"join_timeout_{group_id}")
        except Exception:
            pass

    async def _handle_timeout(self, group_id: str, game_type: str):
        logger.info(f"游戏超时: {group_id}, 类型: {game_type}")
        game_map = {
            "ttt": (self.ttt.get_game(group_id), self.ttt),
            "go": (self.go.get_game(group_id), self.go),
            "xiangqi": (self.xiangqi.get_game(group_id), self.xiangqi),
            "gomoku": (self.gomoku.get_game(group_id), self.gomoku),
        }
        entry = game_map.get(game_type)
        if not entry or not entry[0] or entry[0].is_finished:
            return
        game, manager = entry
        if not hasattr(game, 'surrender'):
            return
        if game_type == "ttt":
            cp = game.player_x if game.current_turn == Player.X else game.player_o
        elif game_type == "go":
            cp = game.player_black if game.current_turn == Stone.BLACK else game.player_white
        elif game_type == "xiangqi":
            cp = game.player_red if game.current_turn == Side.RED else game.player_black
        else:
            cp = game.player_black if game.current_turn == GomokuStone.BLACK else game.player_white
        if cp:
            game.surrender(cp)
            await self._send_timeout_result(group_id, game_type, cp)

    async def _handle_join_timeout(self, group_id: str, game_type: str):
        logger.info(f"等待加入超时: {group_id}, 类型: {game_type}")
        bot = self._group_bots.get(group_id)
        umo = self._group_umo.get(group_id)

        mgr_map = {"ttt": self.ttt, "go": self.go, "xiangqi": self.xiangqi, "gomoku": self.gomoku}
        mgr = mgr_map.get(game_type)
        if not mgr:
            return
        game = mgr.get_game(group_id)
        should_close = False
        if game_type == "ttt" and game and not game.is_finished and game.player_o is None:
            mgr.end_game(group_id); should_close = True
        elif game_type == "go" and game and not game.is_finished and game.player_white is None:
            mgr.end_game(group_id); should_close = True
        elif game_type == "xiangqi" and game and not game.is_finished and game.player_black is None:
            mgr.end_game(group_id); should_close = True
        elif game_type == "gomoku" and game and not game.is_finished and game.player_white is None:
            mgr.end_game(group_id); should_close = True

        if should_close:
            await self._recall_last_message(group_id, bot)
            if umo:
                from astrbot.api.event import MessageChain
                await self.context.send_message(umo, MessageChain().message("⏰ 无人加入，游戏已自动取消"))
            self._last_msg_ids.pop(group_id, None)

    async def _send_timeout_result(self, group_id: str, game_type: str, timeout_player: str):
        umo = self._group_umo.get(group_id)
        bot = self._group_bots.get(group_id)
        if not umo:
            return
        await self._recall_last_message(group_id, bot)
        pname = self._player_names.get(timeout_player, timeout_player[:8])
        from astrbot.api.event import MessageChain
        await self.context.send_message(umo, MessageChain().message(f"⏰ ⏰ ⏰ {pname} 思考超时，自动认输！请发送 /结束游戏 或重新开始"))

    async def _render_and_send(self, group_id: str, game_type: str, game, event: AstrMessageEvent):
        bot = self._group_bots.get(group_id)
        await self._recall_last_message(group_id, bot)

        img_path = None
        if game_type == "ttt":
            board = ["X" if c == Player.X else ("O" if c == Player.O else "") for c in game.board]
            x_name = self._player_names.get(game.player_x, game.player_x[:8])
            o_name = self._player_names.get(game.player_o, game.player_o[:8] if game.player_o else "")
            subtitle = f"第 {sum(1 for c in board if c)} 步" if any(board) else "游戏开始"
            img_path = await self.renderer.render_tictactoe(
                board, x_name, o_name,
                "X" if game.current_turn == Player.X else "O",
                "X" if game.winner == Player.X else ("O" if game.winner == Player.O else None),
                game.is_finished, subtitle)
        elif game_type == "go":
            board = ["B" if c == Stone.BLACK else ("W" if c == Stone.WHITE else "") for c in game.board]
            bn = self._player_names.get(game.player_black, game.player_black[:8])
            wn = self._player_names.get(game.player_white, game.player_white[:8] if game.player_white else "")
            subtitle = f"第 {game.move_count} 手" if game.move_count > 0 else "游戏开始"
            img_path = await self.renderer.render_go(
                board, game.board_size, bn, wn,
                "B" if game.current_turn == Stone.BLACK else "W",
                game.captured_black, game.captured_white, game.move_count, game.last_move,
                "B" if game.winner == Stone.BLACK else ("W" if game.winner == Stone.WHITE else None),
                game.is_finished, subtitle)
        elif game_type == "xiangqi":
            board = [PIECE_CODES[p] for p in game.board]
            rn = self._player_names.get(game.player_red, game.player_red[:8])
            bn = self._player_names.get(game.player_black, game.player_black[:8] if game.player_black else "")
            subtitle = f"第 {game.move_count} 回合" if game.move_count > 0 else "游戏开始"
            img_path = await self.renderer.render_xiangqi(
                board, rn, bn,
                "R" if game.current_turn == Side.RED else "B",
                game.move_count, game.last_move, game.in_check,
                "R" if game.winner == Side.RED else ("B" if game.winner == Side.BLACK else None),
                game.is_finished, subtitle)
        elif game_type == "gomoku":
            board = ["B" if c == GomokuStone.BLACK else ("W" if c == GomokuStone.WHITE else "") for c in game.board]
            bn = self._player_names.get(game.player_black, game.player_black[:8])
            wn = self._player_names.get(game.player_white, game.player_white[:8] if game.player_white else "")
            subtitle = f"第 {game.move_count} 手" if game.move_count > 0 else "游戏开始"
            img_path = await self.renderer.render_gomoku(
                board, game.board_size, bn, wn,
                "B" if game.current_turn == GomokuStone.BLACK else "W",
                game.move_count, game.last_move, game.win_line,
                "B" if game.winner == GomokuStone.BLACK else ("W" if game.winner == GomokuStone.WHITE else None),
                game.is_finished, subtitle)

        if img_path and bot:
            sent = await self._send_image_and_save_id(group_id, img_path, bot)
            if not sent:
                yield event.image_result(img_path)
        elif img_path:
            yield event.image_result(img_path)
        else:
            yield event.plain_result("图片渲染失败")

        # 管理超时
        mgr_map = {"ttt": (self.ttt, "player_o"), "go": (self.go, "player_white"),
                    "xiangqi": (self.xiangqi, "player_black"), "gomoku": (self.gomoku, "player_white")}
        mgr, join_attr = mgr_map[game_type]
        if game.is_finished:
            self._cancel_timeout(group_id)
            self._cancel_join_timeout(group_id)
        elif hasattr(game, join_attr) and getattr(game, join_attr):
            self._cancel_join_timeout(group_id)
            self._schedule_timeout(group_id, game_type)
        else:
            self._schedule_join_timeout(group_id, game_type)

    async def _execute_handler(self, handler_gen, event: AstrMessageEvent):
        try:
            async for result in handler_gen:
                if result and hasattr(result, "chain") and result.chain:
                    await self.context.send_message(event.unified_msg_origin, result.chain)
        except Exception as e:
            logger.error(f"处理简化指令失败: {e}")

    # ========== 测试对局命令（仅管理员） ==========


    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())
        message = event.message_str.strip()
        sender_id = event.get_sender_id()

        # 井字棋简化
        ttt_game = self.ttt.get_game(group_id)
        if ttt_game and not ttt_game.is_finished:
            cp = ttt_game.player_x if ttt_game.current_turn == Player.X else ttt_game.player_o
            if cp == sender_id and message.isdigit() and 1 <= int(message) <= 9:
                await self._execute_handler(self.ttt_move(event, message), event)
                return

        # 五子棋简化
        gomoku_game = self.gomoku.get_game(group_id)
        if gomoku_game and not gomoku_game.is_finished:
            is_b = gomoku_game.current_turn == GomokuStone.BLACK
            cp = gomoku_game.player_black if is_b else gomoku_game.player_white
            if cp == sender_id:
                import re
                if re.match(r'^[A-Oa-o]\d{1,2}$', message) or re.match(r'^\d{1,2}[,\s]\d{1,2}$', message):
                    await self._execute_handler(self.gomoku_move(event, message), event)
                    return

        # 围棋简化
        go_game = self.go.get_game(group_id)
        if go_game and not go_game.is_finished:
            is_b = go_game.current_turn == Stone.BLACK
            cp = go_game.player_black if is_b else go_game.player_white
            if cp == sender_id:
                import re
                if re.match(r'^[A-Ta-t]\d{1,2}$', message):
                    await self._execute_handler(self.go_move(event, message), event)
                    return

        # 象棋简化
        xq_game = self.xiangqi.get_game(group_id)
        if xq_game and not xq_game.is_finished:
            is_r = xq_game.current_turn == Side.RED
            cp = xq_game.player_red if is_r else xq_game.player_black
            if cp == sender_id:
                if len(message) == 4 and message[0] in "车马相仕帅炮兵将士象馬車砲卒前后中":
                    await self._execute_handler(self.xiangqi_move(event, message), event)
                    return
                import re
                if re.match(r'^[A-Ia-i]\d\d?[-:>\s]?[A-Ia-i]\d\d?$', message):
                    await self._execute_handler(self.xiangqi_move(event, message), event)
                    return

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        """监听私聊消息，处理简化指令"""
        sender_id = event.get_sender_id()
        session = f"private_{sender_id}"
        session_ai = f"private_ai_{sender_id}"

        # 井字棋简化（同时检查自己VS自己和AI对局）
        ttt_game = self.ttt.get_game(session) or self.ttt.get_game(session_ai)
        if ttt_game and not ttt_game.is_finished:
            cp = ttt_game.player_x if ttt_game.current_turn == Player.X else ttt_game.player_o
            if cp == sender_id and event.message_str.strip().isdigit() and 1 <= int(event.message_str.strip()) <= 9:
                await self._execute_handler(self.ttt_move(event, event.message_str.strip()), event)
                return

        # 五子棋简化
        gomoku_game = self.gomoku.get_game(session) or self.gomoku.get_game(session_ai)
        if gomoku_game and not gomoku_game.is_finished:
            is_b = gomoku_game.current_turn == GomokuStone.BLACK
            cp = gomoku_game.player_black if is_b else gomoku_game.player_white
            if cp == sender_id:
                import re
                msg = event.message_str.strip()
                if re.match(r'^[A-Oa-o]\d{1,2}$', msg) or re.match(r'^\d{1,2}[,\s]\d{1,2}$', msg):
                    await self._execute_handler(self.gomoku_move(event, msg), event)
                    return

        # 围棋简化
        go_game = self.go.get_game(session) or self.go.get_game(session_ai)
        if go_game and not go_game.is_finished:
            is_b = go_game.current_turn == Stone.BLACK
            cp = go_game.player_black if is_b else go_game.player_white
            if cp == sender_id:
                import re
                msg = event.message_str.strip()
                if re.match(r'^[A-Ta-t]\d{1,2}$', msg):
                    await self._execute_handler(self.go_move(event, msg), event)
                    return

        # 象棋简化
        xq_game = self.xiangqi.get_game(session) or self.xiangqi.get_game(session_ai)
        if xq_game and not xq_game.is_finished:
            is_r = xq_game.current_turn == Side.RED
            cp = xq_game.player_red if is_r else xq_game.player_black
            if cp == sender_id:
                import re
                msg = event.message_str.strip()
                if len(msg) == 4 and msg[0] in "车马相仕帅炮兵将士象馬車砲卒前后中":
                    await self._execute_handler(self.xiangqi_move(event, msg), event)
                    return
                if re.match(r'^[A-Ia-i]\d\d?[-:>\s]?[A-Ia-i]\d\d?$', msg):
                    await self._execute_handler(self.xiangqi_move(event, msg), event)
                    return

    # ========== 测试模式 ==========
    @filter.command("测试开始", alias={"teststart", "开始测试"})
    async def test_mode_start(self, event: AstrMessageEvent):
        """进入测试模式 - 仅管理员可用。测试模式下私聊可用所有游戏命令。用法：/测试开始"""
        if not self._is_admin(event):
            yield event.plain_result("❌ 只有管理员才能使用此命令")
            return
        admin_id = str(event.get_sender_id())
        self._admin_testing[admin_id] = True
        yield event.plain_result("✅ 已进入测试模式！\n• 私聊中可使用 /五子棋、/围棋 等所有命令\n• 游戏将自动设置为自己VS自己\n• 发送 /测试结束 退出测试模式")

    @filter.command("测试结束", alias={"testend", "结束测试"})
    async def test_mode_end(self, event: AstrMessageEvent):
        """退出测试模式。用法：/测试结束"""
        admin_id = str(event.get_sender_id())
        if admin_id in self._admin_testing:
            del self._admin_testing[admin_id]
            yield event.plain_result("✅ 已退出测试模式，恢复群聊限制")
        else:
            yield event.plain_result("❌ 当前不在测试模式")

    # ========== AI对局（仅测试模式可用） ==========
    @filter.command("AI", alias={"ai"})
    async def ai_game(self, event: AstrMessageEvent, game_name: str = None):
        """AI对局。用法：/AI 五子棋 或 /AI五子棋"""
        sid = str(event.get_sender_id())
        if not self._ai_open:
            if not self._is_admin(event):
                yield event.plain_result("❌ 只有管理员才能使用此命令")
                return
            if not self._admin_testing.get(sid):
                yield event.plain_result("❌ AI对局仅在测试模式下可用，请先发送 /测试开始")
                return

        # 如果参数为空，从命令名中提取
        if not game_name:
            raw = event.message_str.strip()
            for prefix in ["/AI", "/ai", "/aigomoku", "/aittt", "/aigo", "/aixiangqi", "/ai井字棋", "/ai围棋", "/ai象棋", "/ai五子棋", "/AI井字棋", "/AI围棋", "/AI象棋", "/AI五子棋"]:
                if raw.startswith(prefix):
                    rest = raw[len(prefix):].strip()
                    if rest:
                        game_name = rest
                        break
            if not game_name:
                # 从命令名本身判断
                cmd = raw.lstrip("/").lower()
                if "井字棋" in cmd or "ttt" in cmd:
                    game_name = "井字棋"
                elif "围棋" in cmd or "go" in cmd:
                    game_name = "围棋"
                elif "象棋" in cmd or "xiangqi" in cmd:
                    game_name = "象棋"
                elif "五子棋" in cmd or "gomoku" in cmd:
                    game_name = "五子棋"
        if not game_name:
            yield event.plain_result("❌ 请指定游戏名：井字棋、围棋、象棋、五子棋\n例如：/AI 五子棋 或 /AI五子棋")
            return

        group_id = f"private_ai_{sid}"
        pid = sid
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        self._cancel_timeout(group_id); self._cancel_join_timeout(group_id)
        self.ttt.end_game(group_id); self.go.end_game(group_id)
        self.xiangqi.end_game(group_id); self.gomoku.end_game(group_id)
        self._last_msg_ids.pop(group_id, None)

        name = game_name.strip()
        if name in ("井字棋", "ttt", "tictactoe"):
            success, msg, game = self.ttt.create_game(group_id, pid)
            if not success:
                yield event.plain_result(f"❌ {msg}")
                return
            self.ttt.join_game(group_id, "ai_test_player")
            self._player_names["ai_test_player"] = "测试AI-O"
            yield event.plain_result("✅ AI对局已开始！你执X，AI执O")
            async for r in self._render_and_send(group_id, "ttt", game, event):
                yield r
        elif name in ("围棋", "go", "weiqi"):
            success, msg, game = self.go.create_game(group_id, pid, 9)
            if not success:
                yield event.plain_result(f"❌ {msg}")
                return
            self.go.join_game(group_id, "ai_test_player")
            self._player_names["ai_test_player"] = "测试AI-白"
            yield event.plain_result("✅ AI对局已开始！你执黑，AI执白（9路）")
            async for r in self._render_and_send(group_id, "go", game, event):
                yield r
        elif name in ("象棋", "xiangqi", "中国象棋"):
            success, msg, game = self.xiangqi.create_game(group_id, pid)
            if not success:
                yield event.plain_result(f"❌ {msg}")
                return
            self.xiangqi.join_game(group_id, "ai_test_player")
            self._player_names["ai_test_player"] = "测试AI-黑"
            yield event.plain_result("✅ AI对局已开始！你执红，AI执黑")
            async for r in self._render_and_send(group_id, "xiangqi", game, event):
                yield r
        elif name in ("五子棋", "gomoku", "wuziqi"):
            success, msg, game = self.gomoku.create_game(group_id, pid, 15)
            if not success:
                yield event.plain_result(f"❌ {msg}")
                return
            self.gomoku.join_game(group_id, "ai_test_player")
            self._player_names["ai_test_player"] = "测试AI-白"
            yield event.plain_result("✅ AI对局已开始！你执黑，AI执白")
            async for r in self._render_and_send(group_id, "gomoku", game, event):
                yield r
        else:
            yield event.plain_result("❌ 不支持的游 戏，支持：井字棋、围棋、象棋、五子棋")




    # ========== 将军提示开关 ==========
    @filter.command("将军提示", alias={"将军开关", "check"})
    async def toggle_check(self, event: AstrMessageEvent, option: str = None):
        """开关将军提示（仅管理员）。用法：/将军提示 开 或 /将军提示 关"""
        if not self._is_admin(event):
            yield event.plain_result("❌ 只有管理员才能使用此命令")
            return
        if not option:
            state = "开" if self._check_check else "关"
            yield event.plain_result(f"当前将军提示状态: {state}")
            return
        if option in ("开", "on", "1", "true"):
            self._check_check = True
            yield event.plain_result("✅ 将军提示已开启")
        elif option in ("关", "off", "0", "false"):
            self._check_check = False
            yield event.plain_result("✅ 将军提示已关闭，走棋不再检查将军")
        else:
            yield event.plain_result("❌ 请使用 开 或 关")

    # ========== AI对局开关 ==========
    @filter.command("AI对局", alias={"ai开关", "aiopen"})
    async def toggle_ai(self, event: AstrMessageEvent, option: str = None):
        """开关AI对局公开使用（仅管理员）。开：所有人可用AI对局，关：仅测试模式+管理员"""
        if not self._is_admin(event):
            yield event.plain_result("❌ 只有管理员才能使用此命令")
            return
        if not option:
            state = "开" if self._ai_open else "关"
            yield event.plain_result(f"当前AI对局公开状态: {state}")
            return
        if option in ("开", "on", "1", "true"):
            self._ai_open = True
            yield event.plain_result("✅ AI对局已公开，所有人均可使用 /AI 命令")
        elif option in ("关", "off", "0", "false"):
            self._ai_open = False
            yield event.plain_result("✅ AI对局已关闭，仅测试模式下管理员可用")
        else:
            yield event.plain_result("❌ 请使用 开 或 关")

    # ========== 井字棋 ==========
    @filter.command("井字棋", alias={"ttt", "tictactoe", "开始井字棋"})
    async def ttt_start(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            sid = str(event.get_sender_id())
            if self._admin_testing.get(sid):
                group_id = f"private_{sid}"
            else:
                yield event.plain_result("❌ 井字棋仅支持群聊中使用")
                return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if self._has_active_game(group_id):
            yield event.plain_result("❌ 当前群有进行中的游戏\n发送 /结束游戏 可强制结束")
            return
        success, msg, game = self.ttt.create_game(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}\n发送 /结束游戏 可强制结束当前游戏")
            return
        # 测试模式下自动设置自己VS自己
        if group_id.startswith("private_"):
            game.player_o = pid
        async for r in self._render_and_send(group_id, "ttt", game, event):
            yield r

    @filter.command("加入井字棋", alias={"jointtt", "加入游戏", "join"})
    async def ttt_join(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            group_id = self._get_session_id(event)
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        success, msg, game = self.ttt.join_game(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        async for r in self._render_and_send(group_id, "ttt", game, event):
            yield r

    @filter.command("下棋", alias={"move", "m"})
    async def ttt_move(self, event: AstrMessageEvent, pos: str = None):
        group_id, game = self._get_game_and_session(event, self.ttt)
        if not group_id:
            group_id = self._get_session_id(event)
        if not pos:
            yield event.plain_result("❌ 请指定位置（1-9），例如：/下棋 5")
            return
        try:
            position = int(pos)
        except ValueError:
            yield event.plain_result("❌ 位置必须是数字（1-9）")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if not game:
            game = self.ttt.get_game(group_id)
        success, msg, game = self.ttt.make_move(group_id, pid, position)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        if game.is_finished:
            if game.winner == Player.X:
                wid = game.player_x
            elif game.winner == Player.O:
                wid = game.player_o
            else:
                wid = None
            wn = self._player_names.get(wid, "未知") if wid else "平局"
            from astrbot.api.event import MessageChain
            await self.context.send_message(event.unified_msg_origin, MessageChain().message(f"🏆 游戏结束！{wn} 获胜！"))
        # AI对局
        if group_id.startswith("private_ai_") and not game.is_finished:
            from astrbot.api.event import MessageChain
            await self.context.send_message(event.unified_msg_origin, MessageChain().message("🤔 AI思考中..."))
            import asyncio
            try:
                ai_pos = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, game.ai_move),
                    timeout=60
                )
            except asyncio.TimeoutError:
                await self.context.send_message(event.unified_msg_origin, MessageChain().message("⏰ AI思考超时，对局结束"))
                self.ttt.end_game(group_id)
                return
                ai_pos = None
            if ai_pos:
                ai_ok, ai_msg = game.make_move("ai_test_player", ai_pos)
                if ai_ok:
                    msg += " AI落子"
                    if game.is_finished:
                        if game.winner == Player.X:
                            wid = game.player_x
                        elif game.winner == Player.O:
                            wid = game.player_o
                        else:
                            wid = None
                        wn = "AI" if wid == "ai_test_player" else (self._player_names.get(wid, "未知") if wid else "平局")
                        from astrbot.api.event import MessageChain
                        await self.context.send_message(event.unified_msg_origin, MessageChain().message(f"🏆 游戏结束！{wn} 获胜！"))
                    async for r in self._render_and_send(group_id, "ttt", game, event):
                        yield r
                    return
        async for r in self._render_and_send(group_id, "ttt", game, event):
            yield r

    @filter.command("认输", alias={"surrender", "投降"})
    async def game_surrender(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            group_id = self._get_session_id(event)
        group_id = str(group_id)
        sid = str(event.get_sender_id())
        ai_gid = f"private_ai_{sid}"
        pid = sid
        self._save_bot_client(event)
        for mgr, gt in [(self.ttt, "ttt"), (self.go, "go"), (self.xiangqi, "xiangqi"), (self.gomoku, "gomoku")]:
            game = mgr.get_game(group_id) or mgr.get_game(ai_gid)
            if game and not game.is_finished:
                success, msg, game = mgr.surrender(group_id, pid)
                if not success:
                    yield event.plain_result(f"❌ {msg}")
                    return
                self._cancel_timeout(group_id)
                async for r in self._render_and_send(group_id, gt, game, event):
                    yield r
                return
        yield event.plain_result("❌ 当前群没有进行中的游戏")

    @filter.command("棋盘", alias={"board", "查看棋盘"})
    async def show_board(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            group_id = self._get_session_id(event)
        group_id = str(group_id)
        self._save_bot_client(event)
        # 也检查AI session
        sid = str(event.get_sender_id())
        ai_gid = f"private_ai_{sid}"
        for mgr, gt in [(self.ttt, "ttt"), (self.go, "go"), (self.xiangqi, "xiangqi"), (self.gomoku, "gomoku")]:
            game = mgr.get_game(group_id) or mgr.get_game(ai_gid)
            if game and not game.is_finished:
                async for r in self._render_and_send(group_id, gt, game, event):
                    yield r
                return
        yield event.plain_result("❌ 当前群没有进行中的游戏\n发送 /井字棋、/围棋、/象棋、/五子棋 开始新游戏")

    @filter.command("结束游戏", alias={"endgame", "结束井字棋", "结束围棋", "结束象棋", "结束五子棋"})
    async def end_game(self, event: AstrMessageEvent):
        sid = str(event.get_sender_id())
        # 同时检查普通测试和AI测试session
        group_ids = []
        gid = event.get_group_id()
        if gid:
            group_ids.append(str(gid))
        group_ids.append(f"private_{sid}")
        group_ids.append(f"private_ai_{sid}")
        for gid in group_ids:
            self._cancel_timeout(gid)
            self._cancel_join_timeout(gid)
        ended = any([
            self.ttt.end_game(g) or self.go.end_game(g) or
            self.xiangqi.end_game(g) or self.gomoku.end_game(g)
            for g in group_ids
        ])
        for gid in group_ids:
            self._last_msg_ids.pop(gid, None)
        if ended:
            yield event.plain_result("✅ 游戏已结束")
        else:
            yield event.plain_result("❌ 当前群没有进行中的游戏")

    def _has_active_game(self, group_id: str) -> bool:
        for mgr in [self.ttt, self.go, self.xiangqi, self.gomoku]:
            game = mgr.get_game(group_id)
            if game and not game.is_finished:
                return True
        return False

    # ========== 围棋 ==========
    @filter.command("围棋", alias={"go", "weiqi"})
    async def go_start(self, event: AstrMessageEvent, size: str = "9"):
        group_id = event.get_group_id()
        if not group_id:
            sid = str(event.get_sender_id())
            if self._admin_testing.get(sid):
                group_id = f"private_{sid}"
            else:
                yield event.plain_result("❌ 围棋仅支持群聊中使用")
                return
        try:
            board_size = int(size)
        except ValueError:
            yield event.plain_result("❌ 棋盘大小必须是数字（9、13 或 19）")
            return
        if board_size not in [9, 13, 19]:
            yield event.plain_result("❌ 棋盘大小只支持 9、13、19")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if self._has_active_game(group_id):
            yield event.plain_result("❌ 当前群有进行中的游戏\n发送 /结束游戏 可强制结束")
            return
        success, msg, game = self.go.create_game(group_id, pid, board_size)
        if not success:
            yield event.plain_result(f"❌ {msg}\n发送 /结束游戏 可强制结束当前游戏")
            return
        # 测试模式下自动设置自己VS自己
        if group_id.startswith("private_"):
            game.player_white = pid
        async for r in self._render_and_send(group_id, "go", game, event):
            yield r

    @filter.command("加入围棋", alias={"joingo"})
    async def go_join(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            group_id = self._get_session_id(event)
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        success, msg, game = self.go.join_game(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        async for r in self._render_and_send(group_id, "go", game, event):
            yield r

    @filter.command("落子", alias={"play", "p"})
    async def go_move(self, event: AstrMessageEvent, coord: str = None):
        group_id, game = self._get_game_and_session(event, self.go)
        if not group_id:
            group_id = self._get_session_id(event)
        if not coord:
            yield event.plain_result("❌ 请指定坐标，例如：/落子 D4")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if not game:
            game = self.go.get_game(group_id)
        success, msg, game = self.go.make_move(group_id, pid, coord)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        if game.is_finished:
            if game.winner == Player.X:
                wid = game.player_x
            elif game.winner == Player.O:
                wid = game.player_o
            else:
                wid = None
            wn = self._player_names.get(wid, "未知") if wid else "平局"
            from astrbot.api.event import MessageChain
            await self.context.send_message(event.unified_msg_origin, MessageChain().message(f"🏆 游戏结束！{wn} 获胜！"))
        # AI对局
        if group_id.startswith("private_ai_") and not game.is_finished:
            import random
            empty = [i for i, s in enumerate(game.board) if s == Stone.EMPTY]
            if empty:
                ai_pos = random.choice(empty)
                ai_x, ai_y = ai_pos % game.board_size, ai_pos // game.board_size
                ai_ok, ai_msg = game.make_move("ai_test_player", ai_x, ai_y)
                if ai_ok:
                    msg += f" AI落子({GO_COLUMN_LABELS[ai_x]}{ai_y+1})"
        async for r in self._render_and_send(group_id, "go", game, event):
            yield r

    @filter.command("虚着", alias={"pass", "跳过"})
    async def go_pass(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 仅支持群聊中使用")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._save_bot_client(event)
        success, msg, game = self.go.pass_turn(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        if game.is_finished:
            self._cancel_timeout(group_id)
            score_b, score_w, details = game.count_territory()
            yield event.plain_result(
                f"🏁 双方连续虚着，游戏结束！\n"
                f"📊 计分结果（中国规则 · 贴{game.komi}目）：\n"
                f"⚫ 黑方: 棋子{details['black_stones']} + 地盘{details['black_territory']} = {score_b}\n"
                f"⚪ 白方: 棋子{details['white_stones']} + 地盘{details['white_territory']} + 贴目{game.komi} = {score_w}"
            )
        async for r in self._render_and_send(group_id, "go", game, event):
            yield r

    @filter.command("点目", alias={"score", "形势"})
    async def go_score(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 仅支持群聊中使用")
            return
        group_id = str(group_id)
        game = self.go.get_game(group_id)
        if not game:
            yield event.plain_result("❌ 当前群没有进行中的围棋游戏")
            return
        score_b, score_w, details = game.count_territory()
        diff = abs(score_b - score_w)
        leading = "黑方" if score_b > score_w else "白方"
        yield event.plain_result(
            f"📊 当前形势（中国规则 · 贴{game.komi}目）\n"
            f"━" * 20 + "\n"
            f"⚫ 黑方: 棋子{details['black_stones']} + 地盘{details['black_territory']} = {score_b}\n"
            f"⚪ 白方: 棋子{details['white_stones']} + 地盘{details['white_territory']} + 贴目{game.komi} = {score_w}\n"
            f"━" * 20 + "\n"
            f"👉 {leading}领先 {diff} 目\n"
            f"⚠️ 仅供参考，未扣除死子"
        )

    @filter.command("悔棋", alias={"undo", "撤回"})
    async def go_undo(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 仅支持群聊中使用")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._save_bot_client(event)
        success, msg, game = self.go.undo(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        yield event.plain_result("✅ 悔棋成功")
        async for r in self._render_and_send(group_id, "go", game, event):
            yield r

    @filter.command("请求计分", alias={"requestscore", "申请计分", "同意计分"})
    async def go_request_score(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 仅支持群聊中使用")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._save_bot_client(event)
        success, msg, game = self.go.request_score(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        if msg == "request_pending":
            pname = self._player_names.get(pid, pid[:8])
            yield event.plain_result(f"📋 {pname} 请求计分结束游戏\n对方发送 /同意计分 同意，或 /拒绝计分 拒绝")
        elif msg == "agreed":
            self._cancel_timeout(group_id)
            score_b, score_w, details = game.count_territory()
            yield event.plain_result(
                f"🏁 双方同意计分，游戏结束！\n"
                f"📊 计分结果（中国规则 · 贴{game.komi}目）：\n"
                f"⚫ 黑方: 棋子{details['black_stones']} + 地盘{details['black_territory']} = {score_b}\n"
                f"⚪ 白方: 棋子{details['white_stones']} + 地盘{details['white_territory']} + 贴目{game.komi} = {score_w}"
            )
            async for r in self._render_and_send(group_id, "go", game, event):
                yield r

    @filter.command("拒绝计分", alias={"rejectscore", "继续游戏"})
    async def go_reject_score(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 仅支持群聊中使用")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        success, msg, game = self.go.reject_score(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        yield event.plain_result(f"✅ {msg}")

    # ========== 象棋 ==========
    @filter.command("象棋", alias={"xiangqi", "中国象棋"})
    async def xiangqi_start(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            sid = str(event.get_sender_id())
            if self._admin_testing.get(sid):
                group_id = f"private_{sid}"
            else:
                yield event.plain_result("❌ 象棋仅支持群聊中使用")
                return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if self._has_active_game(group_id):
            yield event.plain_result("❌ 当前群有进行中的游戏\n发送 /结束游戏 可强制结束")
            return
        success, msg, game = self.xiangqi.create_game(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}\n发送 /结束游戏 可强制结束当前游戏")
            return
        # 测试模式下自动设置自己VS自己
        if group_id.startswith("private_"):
            game.player_black = pid
        async for r in self._render_and_send(group_id, "xiangqi", game, event):
            yield r

    @filter.command("加入象棋", alias={"joinxiangqi"})
    async def xiangqi_join(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            group_id = self._get_session_id(event)
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        success, msg, game = self.xiangqi.join_game(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        async for r in self._render_and_send(group_id, "xiangqi", game, event):
            yield r

    @filter.command("走棋", alias={"xmove", "xm"})
    async def xiangqi_move(self, event: AstrMessageEvent, move: str = None):
        group_id, game = self._get_game_and_session(event, self.xiangqi)
        if not group_id:
            group_id = self._get_session_id(event)
        if not move:
            yield event.plain_result("❌ 请指定走法，例如：/走 a1,a2")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if not game:
            game = self.xiangqi.get_game(group_id)
        force = group_id.startswith("private_") or not self._check_check
        success, msg, game = self.xiangqi.make_move(group_id, pid, move, force=force)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        if game.is_finished:
            if game.winner == Side.RED:
                wid = game.player_red
            elif game.winner == Side.BLACK:
                wid = game.player_black
            else:
                wid = None
            wn = self._player_names.get(wid, "未知") if wid else "平局"
            from astrbot.api.event import MessageChain
            await self.context.send_message(event.unified_msg_origin, MessageChain().message(f"🏆 游戏结束！{wn} 获胜！"))
        # AI对局
        if group_id.startswith("private_ai_") and not game.is_finished:
            from astrbot.api.event import MessageChain
            await self.context.send_message(event.unified_msg_origin, MessageChain().message("🤔 AI思考中..."))
            import asyncio
            try:
                ai_move_res = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, game.ai_move),
                    timeout=60
                )
            except asyncio.TimeoutError:
                await self.context.send_message(event.unified_msg_origin, MessageChain().message("⏰ AI思考超时，对局结束"))
                self.xiangqi.end_game(group_id)
                return
                ai_move_res = None
            if ai_move_res:
                from_p, to_p = ai_move_res
                ai_ok, ai_msg = game.make_move("ai_test_player", from_p, to_p, force=True)
                if ai_ok:
                    msg += " AI落子"
                    if game.is_finished:
                        if game.winner == Side.RED:
                            wid = game.player_red
                        elif game.winner == Side.BLACK:
                            wid = game.player_black
                        else:
                            wid = None
                        wn = "AI" if wid == "ai_test_player" else (self._player_names.get(wid, "未知") if wid else "平局")
                        from astrbot.api.event import MessageChain
                        await self.context.send_message(event.unified_msg_origin, MessageChain().message(f"🏆 游戏结束！{wn} 获胜！"))
                    async for r in self._render_and_send(group_id, "xiangqi", game, event):
                        yield r
                    return
        async for r in self._render_and_send(group_id, "xiangqi", game, event):
            yield r

    # ========== 帮助 ==========
    @filter.command("游戏帮助", alias={"gamehelp", "游戏", "小游戏"})
    async def game_help(self, event: AstrMessageEvent):
        img_path = await self.renderer.render_game_help(subtitle="Game Plugin v1.1")
        if img_path:
            yield event.image_result(img_path)
        else:
            yield event.plain_result(
                "🎮 小游戏合集\n"
                "━" * 20 + "\n"
                "\n"
                "🔴 井字棋: /井字棋 · /下棋 <1-9>\n"
                "⚫ 围棋: /围棋 · /落子 <坐标> · /虚着\n"
                "🀄 象棋: /象棋 · /走 a1,a2\n"
                "⬛ 五子棋: /五子棋 · /五子 <坐标>\n"
                "\n"
                "通用: /走 <坐标> · /棋盘 · /认输 · /结束游戏\n"
                "管理员: /测试开始 · /测试结束 · /AI <游戏名>\n"
                "       /将军提示 开/关 · /AI对局 开/关"
            )

    # ========== 通用下棋命令 ==========
    @filter.command("走", alias={"play", "坐标"})
    async def universal_move(self, event: AstrMessageEvent, coord: str = None):
        """通用下棋命令，自动识别当前游戏类型。用法：/下棋 H8"""
        if not coord:
            yield event.plain_result("❌ 请指定坐标，例如：/下棋 H8")
            return

        # 按顺序检查各游戏
        for mgr, gt, move_handler in [
            (self.ttt, "ttt", self.ttt_move),
            (self.go, "go", self.go_move),
            (self.xiangqi, "xiangqi", self.xiangqi_move),
            (self.gomoku, "gomoku", self.gomoku_move),
        ]:
            gid, game = self._get_game_and_session(event, mgr)
            if gid and game and not game.is_finished:
                async for r in move_handler(event, coord):
                    yield r
                return

        yield event.plain_result("❌ 当前没有进行中的游戏")

    # ========== 五子棋 ==========
    @filter.command("五子棋", alias={"gomoku", "wuziqi"})
    async def gomoku_start(self, event: AstrMessageEvent, size: str = "15"):
        group_id = event.get_group_id()
        if not group_id:
            sid = str(event.get_sender_id())
            if self._admin_testing.get(sid):
                group_id = f"private_{sid}"
            else:
                yield event.plain_result("❌ 五子棋仅支持群聊中使用")
                return
        try:
            board_size = int(size)
        except ValueError:
            yield event.plain_result("❌ 棋盘大小必须是数字（13、15 或 19）")
            return
        if board_size not in [13, 15, 19]:
            yield event.plain_result("❌ 棋盘大小只支持 13、15、19")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if self._has_active_game(group_id):
            yield event.plain_result("❌ 当前群有进行中的游戏\n发送 /结束游戏 可强制结束")
            return
        success, msg, game = self.gomoku.create_game(group_id, pid, board_size)
        if not success:
            yield event.plain_result(f"❌ {msg}\n发送 /结束游戏 可强制结束当前游戏")
            return
        # 测试模式下自动设置自己VS自己
        if group_id.startswith("private_"):
            game.player_white = pid
        async for r in self._render_and_send(group_id, "gomoku", game, event):
            yield r

    @filter.command("加入五子棋", alias={"joingomoku"})
    async def gomoku_join(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            group_id = self._get_session_id(event)
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        success, msg, game = self.gomoku.join_game(group_id, pid)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        async for r in self._render_and_send(group_id, "gomoku", game, event):
            yield r

    @filter.command("五子", alias={"gmove", "gm"})
    async def gomoku_move(self, event: AstrMessageEvent, coord: str = None):
        group_id, game = self._get_game_and_session(event, self.gomoku)
        if not group_id:
            group_id = self._get_session_id(event)
        if not coord:
            yield event.plain_result("❌ 请指定坐标，例如：/五子 H8 或 /五子 8,8")
            return
        group_id = str(group_id)
        pid = str(event.get_sender_id())
        self._player_names[pid] = event.get_sender_name()
        self._save_bot_client(event)
        if not game:
            game = self.gomoku.get_game(group_id)
        success, msg, game = self.gomoku.make_move(group_id, pid, coord)
        if not success:
            yield event.plain_result(f"❌ {msg}")
            return
        if game.is_finished:
            if game.winner == GomokuStone.BLACK:
                wid = game.player_black
            elif game.winner == GomokuStone.WHITE:
                wid = game.player_white
            else:
                wid = None
            wn = self._player_names.get(wid, "未知") if wid else "平局"
            from astrbot.api.event import MessageChain
            await self.context.send_message(event.unified_msg_origin, MessageChain().message(f"🏆 游戏结束！{wn} 获胜！"))
        # AI对局：管理员走完后AI自动落子
        if group_id.startswith("private_ai_") and not game.is_finished:
            ai_xy = game.ai_move()
            if not ai_xy:
                import random
                empty = [i for i, s in enumerate(game.board) if s == Stone.EMPTY]
                if empty:
                    ai_p = random.choice(empty)
                    ai_xy = (ai_p % game.board_size, ai_p // game.board_size)
            if ai_xy:
                ai_x, ai_y = ai_xy
                ai_ok, ai_msg = game.make_move("ai_test_player", ai_x, ai_y)
                if ai_ok:
                    msg += f" AI落子({GO_COLUMN_LABELS[ai_x]}{ai_y+1})"
                    if game.is_finished:
                        if game.winner == GomokuStone.BLACK:
                            wid = game.player_black
                        elif game.winner == GomokuStone.WHITE:
                            wid = game.player_white
                        else:
                            wid = None
                        wn = "AI" if wid == "ai_test_player" else (self._player_names.get(wid, "未知") if wid else "平局")
                        from astrbot.api.event import MessageChain
                        await self.context.send_message(event.unified_msg_origin, MessageChain().message(f"🏆 游戏结束！{wn} 获胜！"))
                    async for r in self._render_and_send(group_id, "gomoku", game, event):
                        yield r
                    return
        async for r in self._render_and_send(group_id, "gomoku", game, event):
            yield r

    async def terminate(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
        logger.info("小游戏插件已卸载")
