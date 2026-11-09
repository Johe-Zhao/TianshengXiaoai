# -*- coding: utf-8 -*-
"""
小米凭据守护
============
**这是"小米账号莫名被退出"的根因修复。**

miservice 的 `MiTokenStore.save_token()` 有一个会静默把用户踢下线的行为：

    async def save_token(self, token=None):
        if token:  ...写文件...
        elif path.isfile(self.token_path):
            remove(self.token_path)        # ← 无参数调用 = 把整个凭据文件删掉

而它在两处都这样"无参数"调用：
  · `MiAccount.login()` 失败时        （miaccount.py:141）
  · 接口返回 401 时                    （miaccount.py:288）

问题在于 `~/.mi.token` 里混存了两类东西：

  ┌ 短期 serviceToken（micoapi / xiaomiio）—— 会过期，属正常
  └ 长期 passToken + userId + deviceId    —— 有效期以月计，是"免扫码续期"的唯一凭据

文件一删，长期凭据跟着消失，程序再也无法静默续期。于是就出现：
serviceToken 一过期 → 文件被删 → 界面「未登录」→ 用户必须重新扫码。
本程序的健康探测每 30 秒跑一次，也就意味着**一次 401 就永久掉线**。

修复策略：把 `save_token()` 的"清空"语义改成"降级"
  · 绝不删除凭据文件
  · 只剔除已过期的 serviceToken 条目，保留 passToken / userId / deviceId
  · 每次写入都刷新 `~/.mi.token.bak` 兜底备份
  · 主文件缺失时自动从备份恢复

这样 serviceToken 过期后，下一次请求会拿 passToken 静默换新，用户完全无感；
只有当 passToken 本身也过期（真正的"需要重新登录"）时，界面才会显示登录失效。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

TOKEN = Path.home() / ".mi.token"
TOKEN_BAK = Path.home() / ".mi.token.bak"

# 长期凭据：这几项必须保住，其余（micoapi / xiaomiio ...）都是会过期的短期 token
KEEP = ("deviceId", "passToken", "userId")

_APPLIED = False


# ---------------- 文件读写 ----------------
def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)          # 原子替换，避免写一半断电导致文件损坏


def backup() -> None:
    """把当前主凭据复制成兜底备份。"""
    try:
        if TOKEN.exists() and TOKEN.stat().st_size > 0:
            shutil.copy2(TOKEN, TOKEN_BAK)
    except Exception:
        pass


def restore() -> bool:
    """主凭据缺失时从备份恢复。返回是否真的恢复过。"""
    try:
        if TOKEN.exists() or not TOKEN_BAK.exists():
            return False
        shutil.copy2(TOKEN_BAK, TOKEN)
        print("[凭据守护] 主凭据缺失，已从备份恢复")
        return True
    except Exception:
        return False


def token_info() -> dict:
    """凭据现状（给界面/日志用）。"""
    data = _read(TOKEN)
    if not data:
        return {"present": False, "user_id": "", "services": [], "has_pass_token": False}
    return {
        "present": True,
        "user_id": str(data.get("userId") or ""),
        "services": [k for k in data if k not in KEEP],
        "has_pass_token": bool(data.get("passToken")),
    }


# ---------------- 降级（替代删除） ----------------
def _degrade(path: str) -> None:
    """把凭据文件降级为"只含长期凭据"，绝不删除。"""
    p = Path(path)
    data = _read(p)
    if not data:
        return

    slim = {k: data[k] for k in KEEP if k in data}
    if not slim.get("passToken"):
        # 连 passToken 都没有 —— 这个文件本身没有续期价值，
        # 但仍然不删，留给"重新登录"覆盖，避免误删用户数据。
        print("[凭据守护] 凭据缺少 passToken，保留原文件不删除")
        return

    if slim == data:
        return                      # 已经是纯长期凭据，无需处理

    dropped = [k for k in data if k not in KEEP]
    _write(p, slim)
    backup()
    print(
        f"[凭据守护] serviceToken 已失效（{','.join(dropped)}），"
        f"已保留长期凭据 userId={slim.get('userId')}，下次请求将自动静默续期"
    )


# ---------------- 打补丁 ----------------
def _patch_no_password_login() -> None:
    """禁止后台服务做「账号密码登录」——它会触发小米短信验证码骚扰。

    典型现场：开机自启的 xiaogpt 在 token 缺失时拿 config 里的账号密码登录，
    小米发短信验证码，而后台进程没有 stdin，读不到验证码（lost sys.stdin）后崩溃。
    用户手机上却白白收到一条验证码，反复几次还会招来风控。

    这里从 miservice 源头掐断：
      · 凭据文件没有长期凭据(passToken) → login() 直接拒绝，零网络请求
      · 即使走到二次验证 → 不发短信，提示去界面重新登录
    **保留** passToken 有效时的静默续期能力（serviceToken 过期自动换新，用户无感）。
    """
    from miservice.miaccount import MiAccount

    _orig_login = MiAccount.login

    async def login(self, sid):
        store = getattr(self, "token_store", None)
        path = getattr(store, "token_path", str(TOKEN))
        if not _read(Path(path)).get("passToken"):
            raise RuntimeError(
                "未登录（缺少长期凭据）：请在界面点「重新登录」，后台不做密码登录"
            )
        return await _orig_login(self, sid)

    async def _no_otp(self, sid, ntf):
        raise RuntimeError(
            "需要短信验证码：请在界面点「重新登录」完成扫码，后台不发送验证码"
        )

    MiAccount.login = login
    MiAccount._verify_otp = _no_otp


def apply() -> None:
    """幂等地给 miservice 打补丁（可被多个入口重复调用）。"""
    global _APPLIED

    restore()                       # 顺手做一次兜底恢复

    if _APPLIED:
        return
    try:
        from miservice.miaccount import MiTokenStore
    except Exception:
        return

    _orig_save = MiTokenStore.save_token

    async def save_token(self, token=None):
        # 有内容：正常落盘，并刷新备份
        if token:
            r = await _orig_save(self, token)
            try:
                backup()
            except Exception:
                pass
            return r

        # 无参数：miservice 想"清空凭据" —— 改为降级，保留长期凭据
        try:
            _degrade(str(self.token_path))
        except Exception as e:
            print(f"[凭据守护] 降级失败（已忽略，不删除文件）: {e}")
        return None

    MiTokenStore.save_token = save_token

    try:
        _patch_no_password_login()
    except Exception as e:
        print(f"[凭据守护] 密码登录拦截未启用: {e}")

    _APPLIED = True
    print("[凭据守护] 已启用：token 过期不删凭据 / 后台不做密码登录不发短信")


if __name__ == "__main__":
    apply()
    info = token_info()
    print("凭据状态:", info)
