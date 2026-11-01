# -*- coding: utf-8 -*-
"""
围棋游戏逻辑
"""
from typing import Optional, Dict, List, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum


class Stone(Enum):
    """棋子类型"""
    EMPTY = 0
    BLACK = 1  # 黑棋先手
    WHITE = 2  # 白棋后手


# 标准围棋坐标列名（跳过 I 避免与 1 混淆）
COLUMN_LABELS = "ABCDEFGHJKLMNOPQRST"


@dataclass
class GoGame:
    """围棋游戏状态"""
    board_size: int  # 9, 13, 或 19
    player_black: str  # 黑方玩家 ID
    player_white: Optional[str] = None  # 白方玩家 ID
    board: List[Stone] = field(default_factory=list)
    current_turn: Stone = Stone.BLACK
    captured_black: int = 0  # 黑方被提子数
    captured_white: int = 0  # 白方被提子数
    move_count: int = 0
    last_move: Optional[int] = None  # 最后落子位置
    ko_point: Optional[int] = None  # 劫点（禁入点）
    is_finished: bool = False
    winner: Optional[Stone] = None
    consecutive_passes: int = 0  # 连续虚着次数
    score_black: float = 0  # 黑方得分
    score_white: float = 0  # 白方得分
    komi: float = 6.5  # 贴目（白方补偿）
    board_history: List[Tuple[int, ...]] = field(default_factory=list)  # 棋盘历史（超级劫）
    last_board_state: Optional[List[Stone]] = None  # 上一步棋盘状态（悔棋用）
    last_captured: int = 0  # 上一步提子数
    last_player: Optional[Stone] = None  # 上一步执子方
    can_undo: bool = False  # 是否可以悔棋
    score_request_by: Optional[str] = None  # 请求计分的玩家

    def __post_init__(self):
        """初始化棋盘"""
        if not self.board:
            self.board = [Stone.EMPTY] * (self.board_size * self.board_size)

    def _pos_to_xy(self, pos: int) -> Tuple[int, int]:
        """位置索引转坐标"""
        return pos % self.board_size, pos // self.board_size

    def _xy_to_pos(self, x: int, y: int) -> int:
        """坐标转位置索引"""
        return y * self.board_size + x

    def _get_neighbors(self, pos: int) -> List[int]:
        """获取相邻位置"""
        x, y = self._pos_to_xy(pos)
        neighbors = []
        if x > 0:
            neighbors.append(self._xy_to_pos(x - 1, y))
        if x < self.board_size - 1:
            neighbors.append(self._xy_to_pos(x + 1, y))
        if y > 0:
            neighbors.append(self._xy_to_pos(x, y - 1))
        if y < self.board_size - 1:
            neighbors.append(self._xy_to_pos(x, y + 1))
        return neighbors

    def _find_group(self, pos: int) -> Set[int]:
        """找到与指定位置连通的同色棋子组"""
        stone = self.board[pos]
        if stone == Stone.EMPTY:
            return set()

        group = set()
        to_check = [pos]

        while to_check:
            current = to_check.pop()
            if current in group:
                continue
            if self.board[current] == stone:
                group.add(current)
                for neighbor in self._get_neighbors(current):
                    if neighbor not in group:
                        to_check.append(neighbor)

        return group

    def _count_liberties(self, group: Set[int]) -> int:
        """计算棋子组的气数"""
        liberties = set()
        for pos in group:
            for neighbor in self._get_neighbors(pos):
                if self.board[neighbor] == Stone.EMPTY:
                    liberties.add(neighbor)
        return len(liberties)

    def _capture_stones(self, group: Set[int]) -> int:
        """提走棋子组，返回提子数量"""
        count = len(group)
        for pos in group:
            self.board[pos] = Stone.EMPTY
        return count

    def _get_opponent(self, stone: Stone) -> Stone:
        """获取对手棋子颜色"""
        return Stone.WHITE if stone == Stone.BLACK else Stone.BLACK

    def _get_board_hash(self) -> Tuple[int, ...]:
        """获取当前棋盘状态的哈希（用于检测同型）"""
        return tuple(s.value for s in self.board)

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
        支持格式：D4, d4, 4,4, 4-4
        返回 (x, y) 或 None
        """
        coord_str = coord_str.strip().upper()

        # 尝试解析字母+数字格式 (D4)
        if len(coord_str) >= 2:
            col = coord_str[0]
            row_str = coord_str[1:]

            if col in COLUMN_LABELS[:self.board_size]:
                try:
                    row = int(row_str)
                    if 1 <= row <= self.board_size:
                        x = COLUMN_LABELS.index(col)
                        y = row - 1  # 转换为 0-indexed
                        return (x, y)
                except ValueError:
                    pass

        # 尝试解析数字,数字格式 (4,4 或 4-4)
        for sep in [',', '，', '-', ' ', '　']:
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

    def make_move(self, player_id: str, x: int, y: int) -> Tuple[bool, str]:
        """
        落子

        Args:
            player_id: 玩家 ID
            x: 列坐标 (0-indexed)
            y: 行坐标 (0-indexed)

        Returns:
            (是否成功, 消息)
        """
        if self.is_finished:
            return False, "游戏已结束"

        if self.player_white is None:
            return False, "等待对手加入"

        # 检查是否轮到该玩家
        if self.current_turn == Stone.BLACK and player_id != self.player_black:
            return False, "现在是黑方回合"
        if self.current_turn == Stone.WHITE and player_id != self.player_white:
            return False, "现在是白方回合"

        # 检查坐标有效性
        if not (0 <= x < self.board_size and 0 <= y < self.board_size):
            return False, f"坐标超出范围（1-{self.board_size}）"

        pos = self._xy_to_pos(x, y)

        # 检查位置是否为空
        if self.board[pos] != Stone.EMPTY:
            return False, "该位置已有棋子"

        # 保存当前状态用于悔棋
        saved_board = self.board.copy()
        saved_captured_black = self.captured_black
        saved_captured_white = self.captured_white

        # 尝试落子
        stone = self.current_turn
        opponent = self._get_opponent(stone)
        self.board[pos] = stone

        # 检查并提走对方死子
        captured = 0
        for neighbor in self._get_neighbors(pos):
            if self.board[neighbor] == opponent:
                group = self._find_group(neighbor)
                if self._count_liberties(group) == 0:
                    captured += self._capture_stones(group)

        # 检查自杀（落子后自己没气）
        my_group = self._find_group(pos)
        if self._count_liberties(my_group) == 0:
            # 回滚落子
            self.board = saved_board
            return False, "禁止自杀（落子后无气）"

        # 检查超级劫（全局同型禁止）
        new_hash = self._get_board_hash()
        if new_hash in self.board_history:
            # 回滚落子
            self.board = saved_board
            return False, "禁止全局同型重复（超级劫规则）"

        # 更新提子计数
        if stone == Stone.BLACK:
            self.captured_white += captured
        else:
            self.captured_black += captured

        # 记录历史和悔棋信息
        self.board_history.append(new_hash)
        self.last_board_state = saved_board
        self.last_captured = captured
        self.last_player = stone
        self.can_undo = True

        # 更新游戏状态
        self.last_move = pos
        self.move_count += 1
        self.consecutive_passes = 0
        self.ko_point = None  # 超级劫规则下不再需要单独的劫点
        self.score_request_by = None  # 清除计分请求
        self.current_turn = opponent

        return True, "落子成功"

    def pass_turn(self, player_id: str) -> Tuple[bool, str]:
        """
        虚着（PASS）

        Args:
            player_id: 玩家 ID

        Returns:
            (是否成功, 消息)
        """
        if self.is_finished:
            return False, "游戏已结束"

        if self.player_white is None:
            return False, "等待对手加入"

        # 检查是否轮到该玩家
        if self.current_turn == Stone.BLACK and player_id != self.player_black:
            return False, "现在是黑方回合"
        if self.current_turn == Stone.WHITE and player_id != self.player_white:
            return False, "现在是白方回合"

        self.consecutive_passes += 1
        self.ko_point = None  # 虚着后清除劫点
        self.can_undo = False  # 虚着后不能悔棋
        self.score_request_by = None  # 清除计分请求
        self.current_turn = self._get_opponent(self.current_turn)

        # 双方连续虚着，游戏结束，计分
        if self.consecutive_passes >= 2:
            self.is_finished = True
            self._finish_with_score()
            return True, "双方连续虚着，游戏结束"

        return True, "虚着"

    def undo(self, player_id: str) -> Tuple[bool, str]:
        """
        悔棋（只能悔最后一步，且需要对手同意或是自己刚下的）

        Args:
            player_id: 请求悔棋的玩家 ID

        Returns:
            (是否成功, 消息)
        """
        if self.is_finished:
            return False, "游戏已结束"

        if not self.can_undo or self.last_board_state is None:
            return False, "当前无法悔棋"

        # 只有刚落子的玩家可以悔棋
        if self.last_player == Stone.BLACK and player_id != self.player_black:
            return False, "只有刚落子的玩家可以请求悔棋"
        if self.last_player == Stone.WHITE and player_id != self.player_white:
            return False, "只有刚落子的玩家可以请求悔棋"

        # 恢复棋盘
        self.board = self.last_board_state.copy()

        # 恢复提子计数
        if self.last_player == Stone.BLACK:
            self.captured_white -= self.last_captured
        else:
            self.captured_black -= self.last_captured

        # 移除历史记录
        if self.board_history:
            self.board_history.pop()

        # 恢复状态
        self.current_turn = self.last_player
        self.move_count -= 1
        self.last_move = None
        self.can_undo = False
        self.last_board_state = None

        return True, "悔棋成功"

    def request_score(self, player_id: str) -> Tuple[bool, str]:
        """
        请求计分结束游戏

        Args:
            player_id: 请求计分的玩家 ID

        Returns:
            (是否成功, 消息)
        """
        if self.is_finished:
            return False, "游戏已结束"

        if player_id not in [self.player_black, self.player_white]:
            return False, "你不是游戏参与者"

        if self.score_request_by is None:
            self.score_request_by = player_id
            return True, "request_pending"
        elif self.score_request_by == player_id:
            return False, "你已经请求过计分了，等待对方确认"
        else:
            # 对方同意，结束游戏
            self.is_finished = True
            self._finish_with_score()
            return True, "agreed"

    def reject_score(self, player_id: str) -> Tuple[bool, str]:
        """
        拒绝计分请求

        Args:
            player_id: 拒绝计分的玩家 ID

        Returns:
            (是否成功, 消息)
        """
        if self.score_request_by is None:
            return False, "没有待处理的计分请求"

        if player_id == self.score_request_by:
            return False, "你不能拒绝自己的请求"

        self.score_request_by = None
        return True, "已拒绝计分请求，游戏继续"

    def count_territory(self) -> Tuple[float, float, Dict[str, int]]:
        """
        数子计分（中国规则）
        黑方得分 = 黑子数 + 黑方地盘
        白方得分 = 白子数 + 白方地盘 + 贴目

        Returns:
            (黑方得分, 白方得分, 详情字典)
        """
        visited = set()
        black_territory = 0
        white_territory = 0
        black_stones = 0
        white_stones = 0

        # 统计棋子数
        for pos in range(self.board_size * self.board_size):
            if self.board[pos] == Stone.BLACK:
                black_stones += 1
            elif self.board[pos] == Stone.WHITE:
                white_stones += 1

        # 用 BFS 找出每个空白连通区域，判断归属
        for pos in range(self.board_size * self.board_size):
            if pos in visited or self.board[pos] != Stone.EMPTY:
                continue

            # BFS 找空白区域
            region = set()
            borders = set()  # 与该区域相邻的棋子颜色
            queue = [pos]

            while queue:
                curr = queue.pop()
                if curr in region:
                    continue
                if self.board[curr] == Stone.EMPTY:
                    region.add(curr)
                    visited.add(curr)
                    for neighbor in self._get_neighbors(curr):
                        if neighbor not in region:
                            queue.append(neighbor)
                else:
                    borders.add(self.board[curr])

            # 判断归属：如果只被一方棋子包围则为该方地盘
            if borders == {Stone.BLACK}:
                black_territory += len(region)
            elif borders == {Stone.WHITE}:
                white_territory += len(region)
            # 两方都有则为公共区域，不计分

        score_b = float(black_stones + black_territory)
        score_w = float(white_stones + white_territory) + self.komi

        details = {
            "black_stones": black_stones,
            "white_stones": white_stones,
            "black_territory": black_territory,
            "white_territory": white_territory,
            "komi": self.komi,
        }

        return score_b, score_w, details

    def _finish_with_score(self):
        """计分并结束游戏"""
        score_b, score_w, _ = self.count_territory()
        self.score_black = score_b
        self.score_white = score_w
        if score_b > score_w:
            self.winner = Stone.BLACK
        elif score_w > score_b:
            self.winner = Stone.WHITE
        # 平局时 winner 为 None

    def ai_move(self) -> Optional[Tuple[int, int]]:
        """AI走棋：角落>边>中间，优先吃子"""
        best_score = -1
        best_pos = None
        weights = {}
        for i, s in enumerate(self.board):
            if s != Stone.EMPTY:
                continue
            x, y = i % self.board_size, i // self.board_size
            # 计算分数
            score = 0
            # 角落优先
            if (x <= 1 or x >= self.board_size-2) and (y <= 1 or y >= self.board_size-2):
                score += 8
            elif x <= 1 or x >= self.board_size-2 or y <= 1 or y >= self.board_size-2:
                score += 5
            else:
                score += 2
            # 如果能吃子（提子）加分
            test_board = list(self.board)
            test_board[i] = self.current_turn
            captured = self._count_captured(test_board, i, self.current_turn)
            score += captured * 20
            weights[(x, y)] = score
        if weights:
            best_pos = max(weights, key=weights.get)
        return best_pos

    def _count_captured(self, board, pos, stone) -> int:
        """统计落子后能提多少子"""
        opp = Stone.WHITE if stone == Stone.BLACK else Stone.BLACK
        dirs = [(1,0),(-1,0),(0,1),(0,-1)]
        x, y = pos % self.board_size, pos // self.board_size
        captured = 0
        for dx, dy in dirs:
            nx, ny = x+dx, y+dy
            if 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                npos = ny * self.board_size + nx
                if board[npos] == opp:
                    if self._is_dead_group(board, npos, opp):
                        captured += 1
        return captured

    def _is_dead_group(self, board, pos, stone) -> bool:
        """检查一组棋子是否被包围（无气）"""
        visited = set()
        stack = [pos]
        has_liberty = False
        dirs = [(1,0),(-1,0),(0,1),(0,-1)]
        while stack and not has_liberty:
            p = stack.pop()
            if p in visited:
                continue
            visited.add(p)
            x, y = p % self.board_size, p // self.board_size
            for dx, dy in dirs:
                nx, ny = x+dx, y+dy
                if 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                    np = ny * self.board_size + nx
                    if board[np] == Stone.EMPTY:
                        has_liberty = True
                        break
                    elif board[np] == stone and np not in visited:
                        stack.append(np)
        return not has_liberty

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

        # 列标签
        col_labels = "  " + " ".join(COLUMN_LABELS[:self.board_size])
        lines.append(col_labels)

        # 棋盘行（从上到下，即 y 从大到小）
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
                    # 星位点
                    if self._is_star_point(x, y):
                        row.append("·")
                    else:
                        row.append("·")
            lines.append(f"{row_num} {' '.join(row)} {row_num}")

        lines.append(col_labels)
        return "\n".join(lines)

    def _is_star_point(self, x: int, y: int) -> bool:
        """判断是否是星位"""
        if self.board_size == 9:
            star_points = [(2, 2), (6, 2), (4, 4), (2, 6), (6, 6)]
        elif self.board_size == 13:
            star_points = [(3, 3), (9, 3), (6, 6), (3, 9), (9, 9)]
        elif self.board_size == 19:
            star_points = [
                (3, 3), (9, 3), (15, 3),
                (3, 9), (9, 9), (15, 9),
                (3, 15), (9, 15), (15, 15)
            ]
        else:
            return False
        return (x, y) in star_points

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
            f"🎮 围棋 ({self.board_size}×{self.board_size})",
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
                lines.append("🤝 游戏结束")
            if self.score_black > 0 or self.score_white > 0:
                lines.append(f"📊 黑 {self.score_black} : 白 {self.score_white}（贴{self.komi}目）")
        else:
            if self.player_white is None:
                lines.append("⏳ 等待对手加入...")
                lines.append("发送 /加入围棋 参与游戏")
            else:
                turn_name = black_name if self.current_turn == Stone.BLACK else white_name
                turn_symbol = "黑" if self.current_turn == Stone.BLACK else "白"
                lines.append(f"👉 轮到 {turn_symbol}方 ({turn_name})")
                lines.append("发送 /落子 <坐标> 落子，如 /落子 D4")

        lines.extend([
            "",
            "━" * 20,
            f"⚫ 黑: {black_name} (提{self.captured_white}子)",
            f"⚪ 白: {white_name} (提{self.captured_black}子)",
            f"📊 第 {self.move_count} 手",
        ])

        if self.last_move is not None:
            lines.append(f"📍 最后落子: {self.get_coordinate_label(self.last_move)}")

        return "\n".join(lines)


class GoManager:
    """围棋游戏管理器"""

    def __init__(self):
        self._games: Dict[str, GoGame] = {}

    def get_game(self, group_id: str) -> Optional[GoGame]:
        """获取群内的游戏"""
        return self._games.get(group_id)

    def create_game(
        self, group_id: str, player_id: str, board_size: int = 9
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """创建新游戏"""
        if board_size not in [9, 13, 19]:
            return False, "棋盘大小只支持 9、13、19", None

        existing = self._games.get(group_id)
        if existing and not existing.is_finished:
            return False, "当前群已有进行中的游戏", existing

        game = GoGame(board_size=board_size, player_black=player_id)
        self._games[group_id] = game
        return True, "游戏创建成功", game

    def join_game(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GoGame]]:
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
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """落子"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的游戏", None

        # 解析坐标
        coord = game.parse_coordinate(coord_str)
        if coord is None:
            return False, f"坐标格式错误，请使用如 D4 或 4,4 的格式", None

        x, y = coord
        success, msg = game.make_move(player_id, x, y)
        return success, msg, game

    def pass_turn(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """虚着"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的游戏", None

        success, msg = game.pass_turn(player_id)
        return success, msg, game

    def surrender(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """认输"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的游戏", None

        success, msg = game.surrender(player_id)
        return success, msg, game

    def count_score(
        self, group_id: str
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """点目（查看当前形势）"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的围棋游戏", None

        return True, "", game

    def undo(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """悔棋"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的围棋游戏", None

        success, msg = game.undo(player_id)
        return success, msg, game

    def request_score(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """请求计分"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的围棋游戏", None

        success, msg = game.request_score(player_id)
        return success, msg, game

    def reject_score(
        self, group_id: str, player_id: str
    ) -> Tuple[bool, str, Optional[GoGame]]:
        """拒绝计分"""
        game = self._games.get(group_id)
        if not game:
            return False, "当前群没有进行中的围棋游戏", None

        success, msg = game.reject_score(player_id)
        return success, msg, game

    def end_game(self, group_id: str) -> bool:
        """强制结束游戏"""
        if group_id in self._games:
            del self._games[group_id]
            return True
        return False
