import json
import os
import random
import time
from datetime import datetime

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import llm_tool
from astrbot.api.message_components import Plain, Image
from astrbot.api.event.filter import command

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ========== 境界设定 ==========
REALMS = [
    {"name": "练气", "levels": 10, "exp_base": 100},
    {"name": "筑基", "levels": 3, "exp_base": 1000},
    {"name": "金丹", "levels": 3, "exp_base": 5000},
    {"name": "元婴", "levels": 3, "exp_base": 20000},
    {"name": "化神", "levels": 3, "exp_base": 80000},
    {"name": "炼虚", "levels": 3, "exp_base": 300000},
    {"name": "合体", "levels": 3, "exp_base": 1000000},
    {"name": "大乘", "levels": 3, "exp_base": 5000000},
    {"name": "渡劫", "levels": 3, "exp_base": 20000000},
    {"name": "真仙", "levels": 1, "exp_base": 99999999},
]

def get_realm_info(realm_idx, level):
    """获取境界信息"""
    if realm_idx >= len(REALMS):
        return None
    realm = REALMS[realm_idx]
    return {
        "name": realm["name"],
        "level": level,
        "max_level": realm["levels"],
        "exp_needed": realm["exp_base"] * (level + 1),
        "full_name": f"{realm['name']}{'期' if realm['name'] == '练气' else '层'}第{level}层" if realm['name'] == '练气' else f"{realm['name']}{'后期' if level == 3 else '中期' if level == 2 else '初期'}",
    }

def calc_power(realm_idx, level):
    """计算战力"""
    return (realm_idx + 1) * 100 + (level + 1) * 20

# ========== 怪物设定 ==========
MONSTERS = [
    {"name": "野狼", "power": 50, "exp": 20, "灵石": 10, "desc": "一只普通的野狼"},
    {"name": "山魈", "power": 120, "exp": 50, "灵石": 25, "desc": "山中精怪"},
    {"name": "黑熊精", "power": 200, "exp": 100, "灵石": 50, "desc": "修炼成精的黑熊"},
    {"name": "血蝠", "power": 350, "exp": 180, "灵石": 80, "desc": "吸血蝙蝠群"},
    {"name": "石傀儡", "power": 500, "exp": 300, "灵石": 150, "desc": "由灵石驱动的傀儡"},
    {"name": "幽冥狼", "power": 800, "exp": 500, "灵石": 250, "desc": "来自幽冥界的妖狼"},
    {"name": "赤焰虎", "power": 1200, "exp": 800, "灵石": 400, "desc": "掌控火焰的妖虎"},
    {"name": "冰蛟", "power": 2000, "exp": 1500, "灵石": 800, "desc": "寒冰蛟龙"},
    {"name": "天雷鹰", "power": 3500, "exp": 3000, "灵石": 1500, "desc": "掌控雷电的巨鹰"},
    {"name": "幽冥鬼王", "power": 5000, "exp": 5000, "灵石": 3000, "desc": "幽冥界的霸主"},
]

# ========== 地图 ==========
MAPS = [
    {"name": "青牛镇", "req_realm": 0, "monsters": [0, 1], "desc": "新手小镇，周围有野狼出没"},
    {"name": "落霞山", "req_realm": 1, "monsters": [1, 2], "desc": "山峦起伏，精怪众多"},
    {"name": "血色禁地", "req_realm": 2, "monsters": [2, 3, 4], "desc": "上古战场，凶险万分"},
    {"name": "幽冥谷", "req_realm": 3, "monsters": [4, 5], "desc": "阴气森森的山谷"},
    {"name": "火焰山", "req_realm": 4, "monsters": [5, 6], "desc": "遍地岩浆，赤地千里"},
    {"name": "冰封雪域", "req_realm": 5, "monsters": [6, 7], "desc": "万年冰川，冰蛟盘踞"},
    {"name": "雷音天", "req_realm": 6, "monsters": [7, 8], "desc": "九霄天雷不断"},
    {"name": "阴阳界", "req_realm": 7, "monsters": [8, 9], "desc": "人间与幽冥的交界"},
]

# ========== 商店物品 ==========
SHOP_ITEMS = [
    {"name": "培元丹", "price": 50, "effect": "exp", "value": 100, "desc": "增加100修为"},
    {"name": "聚气散", "price": 200, "effect": "exp", "value": 500, "desc": "增加500修为"},
    {"name": "筑基丹", "price": 1000, "effect": "exp", "value": 3000, "desc": "增加3000修为"},
    {"name": "凝液丹", "price": 5000, "effect": "exp", "value": 15000, "desc": "增加15000修为"},
    {"name": "化晶丹", "price": 20000, "effect": "exp", "value": 60000, "desc": "增加60000修为"},
    {"name": "小还丹", "price": 100, "effect": "hp", "value": 50, "desc": "恢复50气血"},
    {"name": "大还丹", "price": 500, "effect": "hp", "value": 200, "desc": "恢复200气血"},
    {"name": "灵髓", "price": 10000, "effect": "exp", "value": 30000, "desc": "增加30000修为"},
]


def load_player_data(user_id):
    """加载玩家数据"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{user_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_player_data(user_id, data):
    """保存玩家数据"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{user_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def new_player(name, user_id):
    """创建新角色"""
    return {
        "name": name,
        "user_id": user_id,
        "realm_idx": 0,
        "realm_level": 1,
        "exp": 0,
        "灵石": 100,
        "hp": 100,
        "max_hp": 100,
        "power": 100,
        "bag": {},
        "kill_count": 0,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_fight": 0,
    }


class FanRenPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ========== 工具方法 ==========
    def _get_status_text(self, p):
        """获取玩家状态文本"""
        realm_info = get_realm_info(p["realm_idx"], p["realm_level"])
        if not realm_info:
            return "已达到最高境界！"
        
        next_exp = realm_info["exp_needed"]
        exp_bar = "█" * min(20, int(p["exp"] / next_exp * 20)) if next_exp > 0 else "████████████████████"
        exp_bar = exp_bar.ljust(20, "░")
        
        text = (
            f"━━━ 修仙之境 ━━━\n"
            f"道号：{p['name']}\n"
            f"境界：{realm_info['full_name']}\n"
            f"修为：{p['exp']}/{next_exp}\n"
            f"进度：{exp_bar}\n"
            f"气血：{p['hp']}/{p['max_hp']}\n"
            f"战力：{p['power']}\n"
            f"灵石：{p['灵石']} 💎\n"
            f"斩妖：{p['kill_count']} 只\n"
            f"━━━━━━━━━━━━"
        )
        return text

    def _gain_exp(self, p, amount):
        """增加修为，自动突破"""
        p["exp"] += amount
        max_loop = 100
        loop_count = 0
        breakthroughs = []
        
        while loop_count < max_loop:
            loop_count += 1
            realm_info = get_realm_info(p["realm_idx"], p["realm_level"])
            if not realm_info:
                break
            if p["exp"] >= realm_info["exp_needed"]:
                p["exp"] -= realm_info["exp_needed"]
                p["realm_level"] += 1
                
                if p["realm_level"] > realm_info["max_level"]:
                    p["realm_level"] = 1
                    p["realm_idx"] += 1
                    if p["realm_idx"] >= len(REALMS):
                        p["realm_idx"] = len(REALMS) - 1
                        p["realm_level"] = REALMS[-1]["levels"]
                        p["exp"] = 0
                        breakthroughs.append("🎉 飞升真仙！天地同庆！")
                        break
                    new_realm = REALMS[p["realm_idx"]]
                    p["max_hp"] += 200
                    p["hp"] = p["max_hp"]
                    p["power"] = calc_power(p["realm_idx"], p["realm_level"])
                    breakthroughs.append(f"✨ 突破！踏入{new_realm['name']}境！")
                else:
                    p["max_hp"] += 50
                    p["hp"] = p["max_hp"]
                    p["power"] = calc_power(p["realm_idx"], p["realm_level"])
                    ri = get_realm_info(p["realm_idx"], p["realm_level"])
                    breakthroughs.append(f"🌟 突破！{ri['full_name']}！")
            else:
                break
        return breakthroughs


@command("修仙注册")
async def cmd_register(self, event: AstrMessageEvent):
    """修仙注册 <道号> — 创建修仙角色"""
    text = event.message_str.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        yield event.plain_result("用法：修仙注册 <你的道号>\n例：修仙注册 韩立")
        return
    
    user_id = event.get_sender_id()
    if load_player_data(user_id):
        yield event.plain_result("你已经踏入修仙路了，不可重修！")
        return
    
    name = parts[1].strip()
    if len(name) > 10:
        yield event.plain_result("道号太长了，最多10个字！")
        return
    
    p = new_player(name, user_id)
    save_player_data(user_id, p)
    
    yield event.plain_result(
        f"🌄 天地初开，灵气复苏！\n"
        f"道友「{name}」踏入修仙之路！\n"
        f"赐你100灵石作为盘缠，好生修炼！\n\n"
        f"可用命令：\n"
        f"  修仙状态 — 查看自身信息\n"
        f"  修仙修炼 — 吐纳修炼\n"
        f"  修仙打怪 — 斩妖除魔\n"
        f"  修仙探索 — 探索地图\n"
        f"  修仙背包 — 查看背包\n"
        f"  修仙商店 — 购买物品\n"
        f"  修仙使用 <物品> — 使用物品\n"
        f"  修仙地图 — 查看地图"
    )


@command("修仙状态")
async def cmd_status(self, event: AstrMessageEvent):
    """修仙状态 — 查看角色信息"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    yield event.plain_result(self._get_status_text(p))


@command("修仙修炼")
async def cmd_cultivate(self, event: AstrMessageEvent):
    """修仙修炼 — 吐纳修炼增加修为"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    if p["realm_idx"] >= len(REALMS) - 1 and p["realm_level"] >= REALMS[-1]["levels"]:
        yield event.plain_result("你已达真仙之境，与天地同寿，无需修炼！")
        return
    
    # 修炼获得修为
    base_gain = 10 + p["realm_idx"] * 20 + p["realm_level"] * 5
    gain = int(base_gain * (0.8 + random.random() * 0.4))
    
    # 概率暴击
    is_crit = random.random() < 0.1
    if is_crit:
        gain = int(gain * 3)
    
    breakthroughs = self._gain_exp(p, gain)
    save_player_data(user_id, p)
    
    crit_text = " 💥 顿悟！修为暴增！" if is_crit else ""
    result = f"🧘 吐纳修炼...获得 {gain} 修为！{crit_text}"
    
    if breakthroughs:
        result += "\n" + "\n".join(breakthroughs)
    
    yield event.plain_result(result)


@command("修仙打怪")
async def cmd_fight(self, event: AstrMessageEvent):
    """修仙打怪 — 斩妖除魔"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    # 冷却判断（5秒）
    now = time.time()
    if now - p.get("last_fight", 0) < 5:
        yield event.plain_result("刚战斗完，休息一下吧！（5秒冷却）")
        return
    
    # 根据境界选怪
    max_monster_idx = min(p["realm_idx"] + 1, len(MONSTERS) - 1)
    monster = random.choice(MONSTERS[:max_monster_idx + 1])
    
    # 战斗计算
    player_power = p["power"]
    monster_power = monster["power"]
    
    # 胜负判定
    win_rate = player_power / (player_power + monster_power)
    is_win = random.random() < win_rate
    
    if is_win:
        exp_gain = int(monster["exp"] * (0.8 + random.random() * 0.4))
        灵石_gain = int(monster["灵石"] * (0.8 + random.random() * 0.4))
        p["灵石"] += 灵石_gain
        p["kill_count"] += 1
        
        # 概率掉落物品
        drop_msg = ""
        if random.random() < 0.15:
            items = ["培元丹", "小还丹"]
            item = random.choice(items)
            p["bag"][item] = p["bag"].get(item, 0) + 1
            drop_msg = f"\n🎁 拾取到 {item} ×1！"
        
        breakthroughs = self._gain_exp(p, exp_gain)
        p["last_fight"] = now
        save_player_data(user_id, p)
        
        result = (
            f"⚔️ 遭遇 {monster['name']}！\n"
            f"{monster['desc']}\n"
            f"你的战力 {player_power} vs {monster['name']} {monster_power}\n"
            f"━━━━━━━━━━━━\n"
            f"🏆 胜利！斩杀 {monster['name']}！\n"
            f"✨ 获得 {exp_gain} 修为\n"
            f"💎 获得 {灵石_gain} 灵石{drop_msg}"
        )
        if breakthroughs:
            result += "\n" + "\n".join(breakthroughs)
    else:
        hp_loss = int(monster_power * (0.3 + random.random() * 0.3))
        p["hp"] -= hp_loss
        if p["hp"] <= 0:
            p["hp"] = int(p["max_hp"] * 0.3)  # 复活回到30%血
            p["last_fight"] = now
            save_player_data(user_id, p)
            yield event.plain_result(
                f"⚔️ 遭遇 {monster['name']}！\n"
                f"💀 不敌 {monster['name']}，身负重伤！\n"
                f"损失 {hp_loss} 气血，耗尽法力遁走…\n"
                f"❤️ 疗伤后恢复至 {p['hp']} 气血"
            )
            return
        
        p["last_fight"] = now
        save_player_data(user_id, p)
        yield event.plain_result(
            f"⚔️ 遭遇 {monster['name']}！\n"
            f"你的战力 {player_power} vs {monster['name']} {monster_power}\n"
            f"━━━━━━━━━━━━\n"
            f"😤 苦战不敌，损失 {hp_loss} 气血！\n"
            f"❤️ 当前气血 {p['hp']}/{p['max_hp']}"
        )


@command("修仙探索")
async def cmd_explore(self, event: AstrMessageEvent):
    """修仙探索 <地图名> — 探索地图"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    text = event.message_str.strip()
    parts = text.split(maxsplit=1)
    
    if len(parts) < 2:
        # 显示当前可去的地图
        maps_text = "🗺️ 可探索地图：\n"
        for i, m in enumerate(MAPS):
            status = "✅" if p["realm_idx"] >= m["req_realm"] else "🔒"
            maps_text += f"{status} {m['name']} — {m['desc']}\n"
        yield event.plain_result(maps_text)
        return
    
    map_name = parts[1].strip()
    target_map = None
    for m in MAPS:
        if m["name"] == map_name:
            target_map = m
            break
    
    if not target_map:
        yield event.plain_result(f"未知地图「{map_name}」，可用地图：{'、'.join([m['name'] for m in MAPS])}")
        return
    
    if p["realm_idx"] < target_map["req_realm"]:
        ri = get_realm_info(target_map["req_realm"], 1)
        yield event.plain_result(f"🔒 需要 {ri['full_name']} 以上境界才能探索 {map_name}！")
        return
    
    # 探索获得机缘
    灵石_gain = random.randint(10, 50 + p["realm_idx"] * 30)
    p["灵石"] += 灵石_gain
    
    event_text = random.choice([
        f"在路边发现一株灵草，采摘后卖了{灵石_gain}灵石",
        f"遇到一位散修赠送了你{灵石_gain}灵石",
        f"发现一处灵脉，采集到{灵石_gain}灵石",
        f"击败几个山贼，缴获{灵石_gain}灵石",
        f"在古修洞府中找到{灵石_gain}灵石",
    ])
    
    # 概率获得物品
    item_msg = ""
    if random.random() < 0.2:
        items = ["培元丹", "聚气散", "小还丹"]
        item = random.choice(items)
        p["bag"][item] = p["bag"].get(item, 0) + 1
        item_msg = f"\n🎁 机缘巧合，获得 {item} ×1！"
    
    save_player_data(user_id, p)
    yield event.plain_result(f"🗺️ 探索 {map_name}...\n{event_text}\n💎 获得 {灵石_gain} 灵石{item_msg}")


@command("修仙背包")
async def cmd_bag(self, event: AstrMessageEvent):
    """修仙背包 — 查看背包物品"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    if not p["bag"]:
        yield event.plain_result("🎒 背包空空如也，去探索或打怪获取物品吧！")
        return
    
    bag_text = "🎒 我的背包：\n"
    for item, count in p["bag"].items():
        # 找物品描述
        desc = ""
        for shop in SHOP_ITEMS:
            if shop["name"] == item:
                desc = shop["desc"]
                break
        bag_text += f"  {item} ×{count} — {desc}\n"
    bag_text += "\n使用：修仙使用 <物品名>"
    
    yield event.plain_result(bag_text)


@command("修仙商店")
async def cmd_shop(self, event: AstrMessageEvent):
    """修仙商店 — 查看可购买的物品"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    shop_text = "🏪 灵药阁：\n"
    for i, item in enumerate(SHOP_ITEMS, 1):
        shop_text += f"{i}. {item['name']} — {item['price']}灵石 — {item['desc']}\n"
    shop_text += "\n购买：修仙购买 <物品名>"
    
    yield event.plain_result(shop_text)


@command("修仙购买")
async def cmd_buy(self, event: AstrMessageEvent):
    """修仙购买 <物品名> — 购买物品"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    text = event.message_str.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        yield event.plain_result("用法：修仙购买 <物品名>\n如：修仙购买 培元丹")
        return
    
    item_name = parts[1].strip()
    target_item = None
    for item in SHOP_ITEMS:
        if item["name"] == item_name:
            target_item = item
            break
    
    if not target_item:
        yield event.plain_result(f"没有「{item_name}」这个物品，去看看修仙商店有啥吧！")
        return
    
    if p["灵石"] < target_item["price"]:
        yield event.plain_result(f"灵石不够！{target_item['name']}需要{target_item['price']}灵石，你只有{p['灵石']}灵石")
        return
    
    p["灵石"] -= target_item["price"]
    p["bag"][target_item["name"]] = p["bag"].get(target_item["name"], 0) + 1
    save_player_data(user_id, p)
    
    yield event.plain_result(f"🛒 购买成功！获得 {target_item['name']} ×1，花费 {target_item['price']} 灵石")


@command("修仙使用")
async def cmd_use(self, event: AstrMessageEvent):
    """修仙使用 <物品名> — 使用背包中的物品"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    text = event.message_str.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        yield event.plain_result("用法：修仙使用 <物品名>\n如：修仙使用 培元丹")
        return
    
    item_name = parts[1].strip()
    if p["bag"].get(item_name, 0) <= 0:
        yield event.plain_result(f"背包里没有「{item_name}」！")
        return
    
    target_item = None
    for item in SHOP_ITEMS:
        if item["name"] == item_name:
            target_item = item
            break
    
    if not target_item:
        yield event.plain_result(f"未知物品「{item_name}」")
        return
    
    p["bag"][item_name] -= 1
    if p["bag"][item_name] <= 0:
        del p["bag"][item_name]
    
    result = f"💊 使用 {item_name}..."
    
    if target_item["effect"] == "exp":
        breakthroughs = self._gain_exp(p, target_item["value"])
        result += f"\n✨ 修为增加 {target_item['value']}！"
        if breakthroughs:
            result += "\n" + "\n".join(breakthroughs)
    elif target_item["effect"] == "hp":
        heal = target_item["value"]
        old_hp = p["hp"]
        p["hp"] = min(p["max_hp"], p["hp"] + heal)
        actual_heal = p["hp"] - old_hp
        result += f"\n❤️ 恢复 {actual_heal} 气血（{p['hp']}/{p['max_hp']}）"
    
    save_player_data(user_id, p)
    yield event.plain_result(result)


@command("修仙地图")
async def cmd_map(self, event: AstrMessageEvent):
    """修仙地图 — 查看地图和怪物信息"""
    user_id = event.get_sender_id()
    p = load_player_data(user_id)
    if not p:
        yield event.plain_result("你尚未踏入修仙路，请先「修仙注册」！")
        return
    
    map_text = "🗺️ 修仙界地图：\n"
    for i, m in enumerate(MAPS):
        status = "✅" if p["realm_idx"] >= m["req_realm"] else "🔒"
        ri = get_realm_info(m["req_realm"], 1)
        req = ri['full_name'] if ri else "未知"
        monsters_in = "、".join([MONSTERS[idx]["name"] for idx in m["monsters"]])
        map_text += f"{status} {m['name']}（需{req}）\n   👾 {monsters_in}\n"
    
    yield event.plain_result(map_text)


@command("修仙帮助")
async def cmd_help(self, event: AstrMessageEvent):
    """修仙帮助 — 查看所有命令"""
    yield event.plain_result(
        "📜 凡人修仙 · 命令大全\n"
        "━━━━━━━━━━━━━━━━\n"
        "修仙注册 <道号> — 创建角色\n"
        "修仙状态 — 查看信息\n"
        "修仙修炼 — 吐纳修炼\n"
        "修仙打怪 — 斩妖除魔\n"
        "修仙探索 <地图> — 探索地图\n"
        "修仙地图 — 查看地图\n"
        "修仙背包 — 查看背包\n"
        "修仙商店 — 灵药阁\n"
        "修仙购买 <物品> — 购买\n"
        "修仙使用 <物品> — 使用\n"
        "修仙帮助 — 显示本帮助\n"
        "━━━━━━━━━━━━━━━━"
    )
