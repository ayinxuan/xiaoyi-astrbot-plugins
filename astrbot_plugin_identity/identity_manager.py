"""
identity_manager.py —— 身份数据管理器
纯数据层，与 Star/事件系统无关。
所有身份数据以 QQ 号（user_id）为唯一主键，存储在本地 JSON 文件。
"""
import json
import os
import shutil
import time
from threading import Lock


DATA_FILENAME = "identity_mapping.json"
DEFAULT_IDENTITY = "普通用户"
MAX_BACKUPS = 5


class IdentityManager:
    """身份数据管理（纯数据层）"""

    def __init__(self, data_dir: str):
        self._data_dir = os.path.abspath(data_dir)
        os.makedirs(self._data_dir, exist_ok=True)
        self._file = os.path.join(self._data_dir, DATA_FILENAME)
        self._lock = Lock()
        self._records: dict[str, dict] = {}  # {user_id: {user_id, nickname, identity, prompt}}
        self._load()

    # ──────── 内部读写 ────────

    def _load(self):
        if not os.path.exists(self._file):
            self._records = {}
            return
        with open(self._file, "r", encoding="utf-8") as f:
            self._records = json.load(f) or {}
        # 确保每个值都是 dict
        self._records = {
            k: v for k, v in self._records.items() if isinstance(v, dict)
        }

    def _save(self):
        self._backup()
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._records, f, ensure_ascii=False, indent=2)

    def _backup(self):
        if not os.path.exists(self._file):
            return
        stamp = int(time.time())
        bak = f"{self._file}.{stamp}.bak"
        try:
            shutil.copy2(self._file, bak)
        except Exception:
            pass
        # 清理旧备份
        pattern = f"{DATA_FILENAME}."
        files = [
            f for f in os.listdir(self._data_dir)
            if f.startswith(pattern) and f.endswith(".bak")
        ]
        files.sort(reverse=True)
        for old in files[MAX_BACKUPS:]:
            try:
                os.remove(os.path.join(self._data_dir, old))
            except Exception:
                pass

    # ──────── 公开 API ────────

    @property
    def storage_path(self) -> str:
        return self._file

    def check(self, user_id: str) -> tuple[bool, dict | None]:
        """按QQ号查询，返回 (是否命中, 记录dict)"""
        user_id = str(user_id)
        rec = self._records.get(user_id)
        if rec:
            return True, dict(rec)  # 返回副本防篡改
        return False, None

    def list_all(self) -> list[dict]:
        """返回全部记录列表（副本）"""
        result = []
        for uid, rec in self._records.items():
            r = dict(rec)
            r.setdefault("user_id", uid)
            result.append(r)
        return sorted(result, key=lambda x: x["user_id"])

    def add(
        self,
        user_id: str,
        nickname: str = "",
        identity: str = DEFAULT_IDENTITY,
        prompt: str = "",
    ) -> bool:
        """添加记录。已存在则返回 False"""
        user_id = str(user_id)
        with self._lock:
            if user_id in self._records:
                return False
            self._records[user_id] = {
                "user_id": user_id,
                "nickname": nickname or "",
                "identity": identity,
                "prompt": prompt or "",
            }
            self._save()
            return True

    def delete(self, user_id: str) -> bool:
        user_id = str(user_id)
        with self._lock:
            if user_id not in self._records:
                return False
            del self._records[user_id]
            self._save()
            return True

    def update(
        self,
        user_id: str,
        nickname: str = "",
        identity: str = "",
        prompt: str | None = None,
    ) -> bool:
        """更新一条记录。不可变字段留空表示不更改；prompt 传 None 表示不更改"""
        user_id = str(user_id)
        with self._lock:
            rec = self._records.get(user_id)
            if not rec:
                return False
            if nickname:
                rec["nickname"] = nickname
            if identity:
                rec["identity"] = identity
            if prompt is not None:
                rec["prompt"] = prompt
            self._save()
            return True

    def set_prompt(self, user_id: str, prompt: str) -> bool:
        return self.update(user_id, prompt=prompt)

    def auto_register(self, user_id: str, nickname: str) -> dict:
        """新用户自动注册为「普通用户」，返回记录 dict"""
        user_id = str(user_id)
        with self._lock:
            if user_id in self._records:
                rec = dict(self._records[user_id])
                if rec.get("nickname", "") != nickname:
                    rec["nickname"] = nickname
                    self._records[user_id]["nickname"] = nickname
                    self._save()
                return rec
            rec = {
                "user_id": user_id,
                "nickname": nickname,
                "identity": DEFAULT_IDENTITY,
                "prompt": "",
            }
            self._records[user_id] = rec
            self._save()
            return dict(rec)

    # ──────── 页面 API 专用 ────────

    def page_list(self) -> list[dict]:
        return self.list_all()

    def page_add(self, data: dict) -> dict:
        uid = str(data.get("user_id", ""))
        ident = data.get("identity", DEFAULT_IDENTITY)
        nick = data.get("nickname", "")
        prompt = data.get("prompt", "")
        if not uid:
            raise ValueError("user_id 不能为空")
        with self._lock:
            existed = uid in self._records
            self._records[uid] = {
                "user_id": uid,
                "nickname": nick,
                "identity": ident,
                "prompt": prompt,
            }
            self._save()
        return {"ok": True, "existed": existed}

    def page_update(self, data: dict) -> dict:
        uid = str(data.get("user_id", ""))
        if not uid or uid not in self._records:
            raise ValueError("记录不存在")
        with self._lock:
            rec = self._records[uid]
            if "identity" in data:
                rec["identity"] = data["identity"]
            if "nickname" in data:
                rec["nickname"] = data["nickname"]
            if "prompt" in data:
                rec["prompt"] = data["prompt"]
            self._save()
        return {"ok": True}

    def page_delete(self, user_id: str) -> dict:
        ok = self.delete(str(user_id))
        if not ok:
            raise ValueError("记录不存在")
        return {"ok": True}

    def stats(self) -> dict:
        return {
            "total": len(self._records),
            "path": self._file,
            "exists": os.path.exists(self._file),
        }