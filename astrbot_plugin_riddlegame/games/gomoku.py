# -*- coding: utf-8 -*-
"""
五子棋游戏逻辑
"""
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import random


class Stone(Enum):
    """棋子类型"""
    EMPTY = 0
    BLACK = 1  # 黑棋先手
    WHITE = 2  # 白棋后手


# 列标签（A-O，跳过 I）
COLUMN_LABELS = "ABCDEFGHJKLMNO"


@dataclass
class GomokuGame:
    """五子棋游戏状态"""
    board_size: int = 15  # 标准 15x15
    player_black: str = ""  # 黑方玩家 ID
    player_white: Optional[str] = None  # 白方玩家 ID
    board: List[Stone] = field(default_factory=list)
    current_turn: Stone = Stone.BLACK
    move_count: int = 0
    last_move: Optional[int] = None  # 最后落子位置
    is_finished: bool = False
    winner: Optional[Stone] = None
    win_line: Optional[List[int]] = None  # 获胜连线位置

    def __post_init__(self):
        """初始化棋盘"""
        if not self.board:
            self.board = [Stone.EMPTY] * (self.board_size * self.board_size)

    def _pos_to_xy(self, pos: int) -> Tuple[int, int]:
        """位置索引转坐标 (x=col, y=row)"""
        return pos % self.board_size, pos // self.board_size

    def _xy_to_pos(self, x: int, y: int) -> int:
        """坐标转位置索引"""
        return y * self.board_size + x

    def join(self, player_id: str) -> bool:
        """加入游戏（白方）"""
        if self.player_white is not None:
            return False
        if player_id == self.player_black:
            return False
        self.player_white = player_id
        return True

    def parse_coordinate(self, coord_str: str) -> Optional[Tuple[int, int]]:
        """
        解析坐标字符串
        支持格式：H8, h8, 8,8, 8-8
        返回 (x, y) 或 None
        """
        coord_str = coord_str.strip().upper()

        # 字母+数字格式 (H8)
        if len(coord_str) >= 2:
            col = coord_str[0]
            row_str = coord_str[1:]

            if col in COLUMN_LABELS[:self.board_size]:
                try:
                    row = int(row_str)
                    if 1 <= row <= self.board_size:
                        x = COLUMN_LABELS.index(col)
                        y = row - 1
                        return (x, y)
                except ValueError:
                    pass

        # 数字,数字格式（支持英文逗号、中文逗号、空格、全角空格、横线）
        for sep in [',', '，', ' ', '　', '-']:
            if sep in coord_str:
                parts = coord_str.split(sep)
                if len(parts) == 2:
                    try:
                        x = int(parts[0]) - 1
                        y = int(parts[1]) - 1
                        if 0 <= x < self.board_size and 0 <= y < self.board_size:
                            return (x, y)
                    except ValueError:
                        pass

        return None

    def _check_win(self, pos: int) -> Optional[List[int]]:
        """检查是否有五子连珠，返回连线位置或 None"""
        stone = self.board[pos]
        if stone == Stone.EMPTY:
            return None

        x, y = self._pos_to_xy(pos)

        # 四个方向：水平、垂直、对角线、反对角线
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]

        for dx, dy in directions:
            line = [pos]

            # 正方向
            for i in range(1, 5):
                nx, ny = x + dx * i, y + dy * i
                if 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                    npos = self._xy_to_pos(nx, ny)
                    if self.board[npos] == stone:
                        line.append(npos)
                    else:
                        break
                else:
                    break

            # 反方向
            for i in range(1, 5):
                nx, ny = x - dx * i, y - dy * i
                if 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                    npos = self._xy_to_pos(nx, ny)
                    if self.board[npos] == stone:
                        line.append(npos)
                    else:
                        break
                else:
                    break

            if len(line) >= 5:
                return line

        return None

    def ai_move(self) -> Optional[Tuple[int, int]]:
        """AI落子：评分法选择最优位置"""
        best_score = -1
        best_pos = None
        for i, s in enumerate(self.board):
            if s != Stone.EMPTY:
                continue
            x, y = i % self.board_size, i // self.board_size
            score = self._evaluate_position(x, y)
            if score > best_score:
                best_score = score
                best_pos = (x, y)
        return best_pos

    def _evaluate_position(self, x: int, y: int) -> int:
        """评估某个空位的分数"""
        from collections import defaultdict
        directions = [(1,0), (0,1), (1,1), (1,-1)]
        total = 0
        for dx, dy in directions:
            # 评估AI自己（白）的进攻分数
            ai_count = self._count_line(x, y, dx, dy, Stone.WHITE)
            # 评估对手（黑）的防守分数（需要防守）
            opp_count = self._count_line(x, y, dx, dy, Stone.BLACK)
            # 进攻权重稍高
            total += ai_count * 1.1 + opp_count
        return total

    def _count_line(self, x: int, y: int, dx: int, dy: int, stone: Stone) -> int:
        """统计沿某个方向的潜在连子数"""
        count = 1
        blocked = 0
        for d in (1, -1):
            nx, ny = x + dx*d, y + dy*d
            while 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                s = self.board[ny * self.board_size + nx]
                if s == stone:
                    count += 1
                elif s != Stone.EMPTY:
                    blocked += 1
                    break
                else:
                    break
                nx += dx*d
                ny += dy*d
        if blocked == 2:
            return 0
        if count >= 5:
            return 10000
        # 根据连子数和阻挡情况给分
        base = [0, 0, 10, 100, 1000, 10000]
        score = base[min(count, 5)]
        if blocked > 0:
            score //= 2
        return score

    def make_move(self, player_id: str, x: int, y: int) -> Tuple[bool, str]:
        """落子"""
        if self.is_finished:
            return False, "游戏已结束"

        if self.player_white is None:
            return False, "等待对手加入"

        # 检查是否轮到该玩家
        if self.current_turn == Stone.BLACK and player_id != self.player_black:
            return False, "现在是黑方回合"
        if self.current_turn == Stone.WHITE and player_id != self.player_white:
            return False, "现在是白方回合"

        # 检查坐标
        if not (0 <= x < self.board_size and 0 <= y < self.board_size):
            return False, f"坐标超出范围（1-{self.board_size}）"

        pos = self._xy_to_pos(x, y)

        if self.board[pos] != Stone.EMPTY:
            return False, "该位置已有棋子"

        # 落子
        self.board[pos] = self.current_turn
        self.last_move = pos
        self.move_count += 1

        # 检查是否获胜
        win_line = self._check_win(pos)
        if win_line:
            self.is_finished = True
            self.winner = self.current_turn
            self.win_line = win_line
            return True, "五子连珠！游戏结束"

        # 检查是否平局（棋盘满了）
        if self.move_count >= self.board_size * self.board_size:
            self.is_finished = True
            return True, "棋盘已满，平局！"

        # 切换回合
        self.current_turn = Stone.WHITE if self.current_turn == Stone.BLACK else Stone.BLACK

        return True, "落子成功"

    def surrender(self, player_id: str) -> Tuple[bool, str]:
        """认输"""
        if self.is_finished:
            return False, "游戏已结束"

        if player_id == self.player_black:
            self.winner = Stone.WHITE
            self.is_finished = True
            return True, "黑方认输"
        elif player_id == self.player_white:
            self.winner = Stone.BLACK
            self.is_finished = True
            return True, "白方认输"
        else:
            return False, "你不是游戏参与者"

    def get_coordinate_label(self, pos: int) -> str:
        """获取位置的坐标标签"""
        x, y = self._pos_to_xy(pos)
        return f"{COLUMN_LABELS[x]}{y + 1}"

    def render_board(self) -> str:
        """渲染棋盘为文本"""
        lines = []

        col_labels = "   " + " ".join(COLUMN_LABELS[:self.board_size])
        lines.append(col_labels)

        for y in range(self.board_size - 1, -1, -1):
            row_num = str(y + 1).rjust(2)
            row = []
            for x in range(self.board_size):
                pos = self._xy_to_pos(x, y)
                stone = self.board[pos]
                if stone == Stone.BLACK:
                    row.append("●")
                elif stone == Stone.WHITE:
                    row.append("○")
                else:
                    row.append("·")
            lines.append(f"{row_num} {' '.join(row)} {row_num}")

        lines.append(col_labels)
        return "\n".join(lines)

    def get_status_text(self, player_names: Dict[str, str] = None) -> str:
        """获取游戏状态文本"""
        if player_names is None:
            player_names = {}

        black_name = player_names.get(self.player_black, self.player_black[:8])
        white_name = player_names.get(
            self.player_white,
            self.player_white[:8] if self.player_white else "等待加入"
        )

        lines = [
            f"🎮 五子棋 ({self.board_size}×{self.board_size})",
            "━" * 20,
            "",
            self.render_board(),
            "",
        ]

        if self.is_finished:
            if self.winner == Stone.BLACK:
                lines.append(f"🏆 黑方 ({black_name}) 获胜！")
            elif self.winner == Stone.WHITE:
                lines.append(f"🏆 白方 ({white_name}) 获胜！")
            else:
                lines.append("🤝 平局！")
        else:
            if self.player_white is None:
                lines.append("⏳ 等待对手加入...")
                lines.append("发送 /加入五子棋 参与游戏")
            else:
                turn_name = black_name if self.current_turn == Stone.BLACK else white_name
                turn_symbol = "黑" if self.current_turn == Stone.BLACK else "白"
                lines.append(f"👉 轮到 {turn_symbol}方 ({turn_name})")
                lines.append("发送 /五子 H8 落子")

        lines.extend([
            "",
            "━" * 20,
            f"⚫ 黑: {black_name}",
            f"⚪ 白: {white_name}",
            f"📊 第 {self.move_count} 手",
        ])

        if self.last_move is not None:
            lines.append(f"📍 最后落子: {self.get_coordinate_label(self.last_move)}")

        return "\n".join(lines)


class GomokuManager:
    """五子棋游戏管理器"""

    def __init__(self):
        self._games: Dict[str, GomokuGame] = {}

    def get_game(self, group_id: str) -> Optional[GomokuGame]:
        """获取群内的游戏"""
        return self._games.get(group_id)

    def create_game(
        self, group_id: str, player_id: str, board_size: int = 15
    ) -> Tuple[bool, str, Optional[GomokuGame]]:
        """创建新游戏"""
        if board_size not in [13, 15, 19]:
            return False, "棋盘大小只支持 13、15、19", None

        existing = self._games.get(group_id)
        if existing and not existing.is_finished:
            return False, "当前群已有进行中的游戏", existing

        game = GomokuGame(board_size=board_size, player_black=player_id)
        self._games[group_id] = game
        return True, "游戏创建成功", game

    def join_game(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GomokuGame]]:
        """加入游戏"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的游戏", None

        if game.is_finished:
            return False, "游戏已结束，请发起新游戏", None

        if game.player_white is not None:
            return False, "游戏已满员", game

        if not game.join(player_id):
            return False, "你已经在游戏中了", game

        return True, "加入成功，游戏开始！黑方先行", game

    def make_move(
        self, group_id: str, player_id: str, coord_str: str
    ) -> Tuple[bool, str, Optional[GomokuGame]]:
        """落子"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的游戏", None

        coord = game.parse_coordinate(coord_str)
        if coord is None:
            return False, "坐标格式错误，请使用如 H8 或 8,8 的格式", None

        x, y = coord
        success, msg = game.make_move(player_id, x, y)
        return success, msg, game

    def surrender(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GomokuGame]]:
        """认输"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的游戏", None

        success, msg = game.surrender(player_id)
        return success, msg, game

    def end_game(self, group_id: str) -> bool:
        """强制结束游戏"""
        if group_id in self._games:
            del self._games[group_id]
            return True
        return False
