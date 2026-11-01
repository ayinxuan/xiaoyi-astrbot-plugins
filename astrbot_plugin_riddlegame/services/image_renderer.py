# -*- coding: utf-8 -*-
"""本地PIL图片渲染 - 不依赖外部API服务"""
import os
import tempfile
from typing import Optional, List, Tuple
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger

class GameRenderer:
    """本地游戏图片渲染器"""

    def __init__(self, service_url: str = ""):
        self.service_url = service_url  # 保留参数但不使用
        self._font = None
        self._font_bold = None
        self._font_small = None

    def _get_font(self, size: int = 24) -> ImageFont.FreeTypeFont:
        """获取字体，支持中文字符"""
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyhbd.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                return ImageFont.truetype(fp, size, encoding="utf-8")
        return ImageFont.load_default()

    def _get_font_bold(self, size: int = 28) -> ImageFont.FreeTypeFont:
        return self._get_font(size)

    def _get_font_small(self, size: int = 18) -> ImageFont.FreeTypeFont:
        return self._get_font(size)

    def _save_img(self, img: Image.Image, prefix: str) -> str:
        """保存图片到临时目录"""
        temp_dir = tempfile.gettempdir()
        img_path = os.path.join(temp_dir, f"{prefix}_{os.getpid()}.png")
        img.save(img_path, "PNG")
        logger.info(f"图片已保存: {img_path}")
        return img_path

    def _draw_text_centered(self, draw: ImageDraw.Draw, x: int, y: int, text: str, font,
                            fill: Tuple[int, int, int] = (0, 0, 0)):
        """在指定位置居中绘制文字"""
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((x - tw // 2, y - th // 2 - 2), text, font=font, fill=fill)

    # ========== 井字棋 ==========
    async def render_tictactoe(self, board: List[str], player_x_name: str,
                                player_o_name: Optional[str], current_turn: str,
                                winner: Optional[str], is_finished: bool,
                                subtitle: str = "") -> Optional[str]:
        cell = 140
        margin = 40
        w = margin * 2 + cell * 3
        h = margin * 2 + cell * 3 + 60
        img = Image.new("RGB", (w, h), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        font = self._get_font(36)
        font_info = self._get_font(24)

        # 标题
        title = "游戏结束" if is_finished else ("X 方回合" if current_turn == "X" else "O 方回合")
        self._draw_text_centered(draw, w // 2, 20, title, font, (80, 80, 80))

        # 画网格
        for i in range(4):
            x = margin + i * cell
            draw.line([(x, margin), (x, margin + cell * 3)], fill=(0, 0, 0), width=3)
            y = margin + i * cell
            draw.line([(margin, y), (margin + cell * 3, y)], fill=(0, 0, 0), width=3)

        # 画棋子
        font_piece = self._get_font(80)
        for i, piece in enumerate(board):
            row, col = divmod(i, 3)
            cx = margin + col * cell + cell // 2
            cy = margin + row * cell + cell // 2
            if piece == "X":
                draw.line([(cx - 30, cy - 30), (cx + 30, cy + 30)], fill=(255, 50, 50), width=6)
                draw.line([(cx + 30, cy - 30), (cx - 30, cy + 30)], fill=(255, 50, 50), width=6)
            elif piece == "O":
                draw.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], outline=(50, 50, 255), width=6)

        # 玩家信息
        info_y = margin + cell * 3 + 10
        x_name = player_x_name[:6]
        o_name = player_o_name[:6] if player_o_name else "等待中"
        draw.text((margin, info_y), f"X: {x_name}", font=font_info, fill=(255, 50, 50))
        draw.text((margin + cell * 2, info_y), f"O: {o_name}", font=font_info, fill=(50, 50, 255))
        # subtitle已去除

        return self._save_img(img, "tictactoe")

    # ========== 五子棋 ==========
    async def render_gomoku(self, board: List[str], board_size: int,
                             black_player_name: str, white_player_name: Optional[str],
                             current_turn: str, move_count: int = 0,
                             last_move: Optional[int] = None,
                             win_line: Optional[List[int]] = None,
                             winner: Optional[str] = None,
                             is_finished: bool = False,
                             subtitle: str = "") -> Optional[str]:
        cell = 44
        margin = 60
        size = board_size
        grid_w = cell * (size - 1)
        w = margin * 2 + grid_w + 40
        h = margin * 2 + grid_w + 70
        img = Image.new("RGB", (w, h), (230, 190, 130))
        draw = ImageDraw.Draw(img)
        font = self._get_font(18)
        font_title = self._get_font(22)

        # 标题
        if is_finished:
            title = f"🏆 五子棋 - 游戏结束"
        else:
            title = f"五子棋 - {subtitle}" if subtitle else "五子棋"
        self._draw_text_centered(draw, w // 2, 10, title, font_title, (60, 40, 20))

        # 画外边框（加粗）
        draw.rectangle([margin-2, margin-2, margin+grid_w+2, margin+grid_w+2], outline=(0, 0, 0), width=3)

        # 画网格
        for i in range(size):
            x = margin + i * cell
            draw.line([(x, margin), (x, margin + grid_w)], fill=(0, 0, 0), width=1)
            y = margin + i * cell
            draw.line([(margin, y), (margin + grid_w, y)], fill=(0, 0, 0), width=1)

        # 画星位（天元和四个角星）
        star_positions = []
        if size == 15:
            star_positions = [(3,3), (3,11), (7,7), (11,3), (11,11)]
        elif size == 13:
            star_positions = [(3,3), (3,9), (6,6), (9,3), (9,9)]
        elif size == 19:
            star_positions = [(3,3), (3,9), (3,15), (9,3), (9,9), (9,15), (15,3), (15,9), (15,15)]
        for r, c in star_positions:
            sx = margin + c * cell
            sy = margin + r * cell
            draw.ellipse([sx-5, sy-5, sx+5, sy+5], fill=(0, 0, 0))

        # 坐标标签（数字）
        font_small = self._get_font(15)
        for i in range(size):
            # 底部坐标（右对齐）
            tx = margin + i * cell
            draw.text((tx - 7, margin + grid_w + 22), str(i + 1), font=font_small, fill=(60, 40, 20))
            # 左侧坐标（拉远）
            draw.text((margin - 36, margin + i * cell - 9), str(i + 1), font=font_small, fill=(60, 40, 20))

        # 画棋子
        r_star = cell // 2 - 4
        for i, piece in enumerate(board):
            if not piece:
                continue
            row, col = divmod(i, size)
            cx = margin + col * cell
            cy = margin + row * cell
            if piece == "B":
                draw.ellipse([cx - r_star, cy - r_star, cx + r_star, cy + r_star], fill=(0, 0, 0))
            elif piece == "W":
                draw.ellipse([cx - r_star, cy - r_star, cx + r_star, cy + r_star], fill=(255, 255, 255),
                             outline=(0, 0, 0), width=1)

        # 最后落子标记
        if last_move is not None:
            row, col = divmod(last_move, size)
            cx = margin + col * cell
            cy = margin + row * cell
            draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(255, 0, 0))

        # 玩家信息
        info_y = margin + grid_w + 40
        turn_text = "● 黑方" if current_turn == "B" else "○ 白方"
        draw.text((margin, info_y), f"{turn_text}: {black_player_name[:6]} vs {white_player_name[:6] if white_player_name else '等待'}", font=font, fill=(60, 40, 20))

        if win_line:
            # 画获胜连线
            for idx in win_line:
                row, col = divmod(idx, size)
                cx = margin + col * cell
                cy = margin + row * cell
                draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(255, 215, 0), width=3)

        return self._save_img(img, "gomoku")

    # ========== 围棋 ==========
    async def render_go(self, board: List[str], board_size: int,
                         black_player_name: str, white_player_name: Optional[str],
                         current_turn: str,
                         captured_black: int = 0, captured_white: int = 0,
                         move_count: int = 0, last_move: Optional[int] = None,
                         winner: Optional[str] = None,
                         is_finished: bool = False,
                         subtitle: str = "") -> Optional[str]:
        cell = 40
        margin = 52
        size = board_size
        grid_w = cell * (size - 1)
        w = margin * 2 + grid_w + 40
        h = margin * 2 + grid_w + 60
        img = Image.new("RGB", (w, h), (220, 180, 120))
        draw = ImageDraw.Draw(img)
        font = self._get_font(16)
        font_title = self._get_font(22)

        if is_finished:
            title = f"🏆 围棋 {size}x{size} - 游戏结束"
        else:
            title = f"围棋 {size}x{size}" + (f" - {subtitle}" if subtitle else "")
        self._draw_text_centered(draw, w // 2, 10, title, font_title, (60, 40, 20))

        # 画网格
        for i in range(size):
            x = margin + i * cell
            draw.line([(x, margin), (x, margin + grid_w)], fill=(0, 0, 0), width=1)
            y = margin + i * cell
            draw.line([(margin, y), (margin + grid_w, y)], fill=(0, 0, 0), width=1)

        # 星位
        star_points = []
        if size == 9:
            star_points = [(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)]
        elif size == 13:
            star_points = [(3, 3), (3, 9), (9, 3), (9, 9), (6, 6)]
        elif size == 19:
            star_points = [(3, 3), (3, 9), (3, 15), (9, 3), (9, 9), (9, 15), (15, 3), (15, 9), (15, 15)]
        for r, c in star_points:
            cx = margin + c * cell
            cy = margin + r * cell
            draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(0, 0, 0))

        # 坐标（数字，加大间距）
        font_small = self._get_font(14)
        for i in range(size):
            tx = margin + i * cell
            draw.text((tx - 6, margin + grid_w + 20), str(i + 1), font=font_small, fill=(60, 40, 20))
            draw.text((margin - 32, margin + i * cell - 8), str(i + 1), font=font_small, fill=(60, 40, 20))

        # 棋子
        r_star = cell // 2 - 3
        for i, piece in enumerate(board):
            if not piece:
                continue
            row, col = divmod(i, size)
            cx = margin + col * cell
            cy = margin + row * cell
            if piece == "B":
                draw.ellipse([cx - r_star, cy - r_star, cx + r_star, cy + r_star], fill=(0, 0, 0))
            elif piece == "W":
                draw.ellipse([cx - r_star, cy - r_star, cx + r_star, cy + r_star], fill=(255, 255, 255),
                             outline=(0, 0, 0), width=1)

        if last_move is not None:
            row, col = divmod(last_move, size)
            cx = margin + col * cell
            cy = margin + row * cell
            draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 0, 0))

        info_y = margin + grid_w + 40
        cap_text = f"提子: ●{captured_black} ○{captured_white}"
        turn_mark = "●" if current_turn == "B" else "○"
        draw.text((margin, info_y), f"{turn_mark} {black_player_name[:6]} vs {white_player_name[:6] if white_player_name else '等待'} | {cap_text} | 第{move_count}手",
                  font=font, fill=(60, 40, 20))

        return self._save_img(img, "go")

    # ========== 象棋 ==========
    async def render_xiangqi(self, board: List[str], red_player_name: str,
                               black_player_name: Optional[str], current_turn: str,
                               move_count: int = 0,
                               last_move: Optional[Tuple[int, int]] = None,
                               in_check: bool = False, winner: Optional[str] = None,
                               is_finished: bool = False,
                               subtitle: str = "") -> Optional[str]:
        cell = 52
        margin = 50
        cols, rows = 9, 10
        grid_w = cell * (cols - 1)
        grid_h = cell * (rows - 1)
        w = margin * 2 + grid_w + 30
        h = margin * 2 + grid_h + 70
        img = Image.new("RGB", (w, h), (240, 200, 140))
        draw = ImageDraw.Draw(img)
        font = self._get_font(18)
        font_title = self._get_font(22)
        font_piece = self._get_font(26)

        if is_finished:
            title = f"🏆 象棋 - 游戏结束"
        else:
            title = f"象棋 - {subtitle}" if subtitle else "象棋"
        self._draw_text_centered(draw, w // 2, 10, title, font_title, (60, 40, 20))

        # 数字坐标轴（底部列坐标+左侧行坐标）
        font_small = self._get_font(14)
        for i in range(cols):
            # 底部坐标（字母轴A-I）
            tx = margin + i * cell
            draw.text((tx - 5, margin + grid_h + 18), chr(65 + i), font=font_small, fill=(60, 40, 20))
        for i in range(rows):
            # 左侧坐标
            draw.text((margin - 30, margin + i * cell - 8), str(i + 1), font=font_small, fill=(60, 40, 20))

        # 画网格线
        for i in range(rows):
            y = margin + i * cell
            draw.line([(margin, y), (margin + grid_w, y)], fill=(0, 0, 0), width=1)
        for i in range(cols):
            x = margin + i * cell
            draw.line([(x, margin), (x, margin + grid_h)], fill=(0, 0, 0), width=1)

        # 楚河汉界（放一条格子中间）
        river_y = margin + 4 * cell
        cx = margin + grid_w // 2
        self._draw_text_centered(draw, cx, river_y - 10, "楚河         汉界", self._get_font(22), (100, 60, 30))

        # 斜线（九宫格）
        for left_x, top_y in [(margin + 3 * cell, margin), (margin + 3 * cell, margin + 7 * cell)]:
            draw.line([(left_x, top_y), (left_x + 2 * cell, top_y + 2 * cell)], fill=(0, 0, 0), width=1)
            draw.line([(left_x + 2 * cell, top_y), (left_x, top_y + 2 * cell)], fill=(0, 0, 0), width=1)

        # 棋子（匹配 xiangqi.py 中的 PIECE_CODES）
        piece_map = {
            "RK": "帅", "RA": "仕", "RE": "相", "RH": "马", "RC": "车", "RN": "炮", "RS": "兵",
            "BK": "将", "BA": "士", "BE": "象", "BH": "马", "BC": "车", "BN": "炮", "BS": "卒",
        }
        for i, code in enumerate(board):
            if not code:
                continue
            row, col = divmod(i, cols)
            cx = margin + col * cell
            cy = margin + row * cell
            piece_char = piece_map.get(code, code)
            is_red = code.startswith("R")
            bg = (255, 230, 200) if is_red else (200, 200, 200)
            fg = (200, 30, 30) if is_red else (30, 30, 30)
            draw.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], fill=bg, outline=(0, 0, 0), width=2)
            self._draw_text_centered(draw, cx, cy, piece_char, font_piece, fg)

        info_y = margin + grid_h + 40
        turn_text = "红方" if current_turn == "R" else "黑方"
        check_text = " 将军!" if in_check else ""
        draw.text((margin, info_y), f"{turn_text}回合 | {red_player_name[:6]} vs {black_player_name[:6] if black_player_name else '等待'}{check_text}",
                  font=font, fill=(60, 40, 20))

        return self._save_img(img, "xiangqi")

    # ========== 游戏帮助 ==========
    async def render_game_help(self, subtitle: str = "") -> Optional[str]:
        w, h = 500, 480
        img = Image.new("RGB", (w, h), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        font_title = self._get_font(32)
        font = self._get_font(22)

        lines = [
            ("🎮 小游戏合集", font_title, (50, 50, 50)),
            ("", font, (0, 0, 0)),
            ("🔴 井字棋: /井字棋 · /下棋 <1-9>", font, (200, 50, 50)),
            ("⚫ 围棋: /围棋 · /落子 <坐标> · /虚着", font, (50, 50, 50)),
            ("🀄 象棋: /象棋 · /走 e1-e2", font, (50, 50, 50)),
            ("⬛ 五子棋: /五子棋 · /五子 <坐标>", font, (50, 50, 50)),
            ("", font, (0, 0, 0)),
            ("通用: /走 <坐标> · /棋盘 · /认输 · /结束游戏", font, (50, 50, 50)),
            ("管理员: /测试开始 · /测试结束 · /AI <游戏名>", font, (200, 50, 50)),
            ("       /将军提示 开/关 · /AI对局 开/关", font, (200, 100, 100)),
        ]
        y = 30
        for text, fnt, color in lines:
            if text:
                draw.text((30, y), text, font=fnt, fill=color)
            y += 40

        return self._save_img(img, "gamehelp")
