"""持久化登录（新增模块）

问题
----
用户"过一会不用"就被踢回登录页。根因**不是**应用里的超时（那是 480 分钟 = 8 小时），
而是 Streamlit 的会话模型：`st.session_state` 只活在 WebSocket 连接期间。
连接一断（浏览器休眠、切标签、网络抖动、手机锁屏、服务端重启），
服务端 session 被销毁，`logged_in` 等键全部回到默认值 → 重连即显示登录页。

方案
----
把登录凭据**持久化到浏览器 cookie**，服务端在每次脚本运行时从 `st.context.cookies`
读回并校验签名：

1. 登录成功 → 签发带签名的令牌（itsdangerous，防篡改）→ 写入浏览器 cookie
2. 之后每次打开/刷新 → 从 cookie 读回 → 验签 + 查有效期 → 自动恢复登录态
3. 显式退出登录 → 清 cookie，立刻失效

写入通道的选择（实测结论，勿改）
--------------------------------
**读**：走 `st.context.cookies`（Streamlit 1.64+ 原生支持），无需前端脚本。

**写**：唯一可行的是 `components.html` 里的 iframe 执行
`document.cookie = '...; path=/; max-age=...'`。已实测：
1) iframe 的 JS **可以**写顶层域 cookie，刷新后 `st.context.cookies` 能读回（闭环成立）；
2) 其余通道全部被证伪 ——
   - `st.markdown(unsafe_allow_html=True)` 里的 `<img onerror>` / `<script>`：被清洗，不执行
   - `st.html("<script>")`：被清洗，不执行
   - iframe 的 `postMessage` 往父窗口传值：跨域隔离，收不到
   - `st.context.headers` 下发 `Set-Cookie`：`StreamlitHeaders` 只有 get/get_all/items/keys/
     to_dict/values，**没有 set_cookie**，响应里 Set-Cookie 数量为 0

副作用说明：`components.html` 每次执行会渲染一个高度为 0 的隐藏 iframe。
为不影响版式，只在"需要写入"的那一次渲染（登录成功 / 退出登录），
日常刷新时**不**渲染，避免每页多一个无用 iframe。

安全设计
--------
- 令牌用 `URLSafeTimedSerializer` 签名，改动一个字符即验签失败
- 有效期默认 7 天，过期自动失效并要求重新登录
- 只存"用户名 + 角色 + user_id"，**不存密码**
- 退出登录立即清 cookie
- 密钥从 `AUTH_SECRET` 环境变量读取；未设置时用基于库路径的稳定派生值
  （同一部署下重启不失效，但换部署即失效）
- 令牌不写入 URL、不写入日志

超时语义（2026-10 修订）
------------------------
`_login_ts` 由"**绝对**登录时刻"改为"**最近一次活动时间**"，超时判定改为
**闲置超时**：只要持续使用就永不掉线，连续闲置超过 `SESSION_TIMEOUT`
（默认 480 分钟）才要求重登。修复了"时间长就掉线"（见 `check_session_timeout`
的文档字符串）。同时把 app.py 里那段语义不一致、且会用 `_login_ts` 默认值 0
误判超时的重复检查去掉。
"""
from __future__ import annotations

import hashlib
import os
import time

import streamlit as st

# ============================================================================
# 配置
# ============================================================================
COOKIE_NAME = "lt_profit_auth"
DEFAULT_DAYS = 7

# 应用内超时（与 cookie 有效期独立）：默认 480 分钟
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT_MIN", "480")) * 60

# 登录保持天数（cookie 有效期）
try:
    COOKIE_DAYS = int(os.getenv("LOGIN_KEEP_DAYS", str(DEFAULT_DAYS)))
except ValueError:
    COOKIE_DAYS = DEFAULT_DAYS
COOKIE_MAX_AGE = max(1, COOKIE_DAYS) * 24 * 3600

# 弱密钥告警只打印一次的模块级标记（避免每次令牌读取都刷一行日志）
_warned_missing_secret = False


def _secret() -> str:
    """签名密钥。优先环境变量；否则用库文件路径派生一个稳定值。"""
    s = os.getenv("AUTH_SECRET")
    if s:
        return s
    global _warned_missing_secret
    if not _warned_missing_secret:
        print("[WARN] 未设置 AUTH_SECRET，登录令牌使用派生密钥，建议在生产 Secrets 中显式设置。")
        _warned_missing_secret = True
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        base = "lt-profit"
    return "lt-profit::" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def _ser():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(_secret(), salt="lt-profit-login")


# ============================================================================
# 令牌签发 / 校验
# ============================================================================
def make_token(username: str, role: str, user_id) -> str:
    """签发登录令牌。只含身份标识，不含密码。"""
    return _ser().dumps({"u": username, "r": role, "i": user_id})


def read_token(tok: str | None):
    """校验令牌，返回 dict 或 None。签名不符 / 过期 / 为空都返回 None。"""
    if not tok:
        return None
    from itsdangerous import BadSignature, SignatureExpired
    try:
        data = _ser().loads(tok, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired, Exception):
        return None
    if not isinstance(data, dict) or not data.get("u"):
        return None
    return data


# ============================================================================
# Cookie 读写
# ============================================================================
def _cookie_kv() -> dict:
    try:
        return dict(st.context.cookies)
    except Exception:
        return {}


def get_cookie_token() -> str | None:
    return _cookie_kv().get(COOKIE_NAME)


def _write_cookie(value: str, max_age: int) -> bool:
    """把 cookie 写进浏览器（唯一的可行通道：iframe 内的 document.cookie）。

    见模块文档「写入通道的选择」——Streamlit 会清洗所有内联 <script>，
    只有 components.html 的 iframe 能真正执行 JS 并写顶层域 cookie。
    """
    try:
        import streamlit.components.v1 as components
    except Exception:
        return False
    # 值经 JSON 转义，避免引号/特殊字符破坏 JS 字面量
    import json
    val_js = json.dumps(value)
    name_js = json.dumps(COOKIE_NAME)
    js = f"""
    <script>
    (function() {{
      try {{
        document.cookie = {name_js} + "=" + encodeURIComponent({val_js})
          + "; path=/; max-age=" + {int(max_age)} + "; SameSite=Lax";
      }} catch (e) {{}}
    }})();
    </script>
    """
    try:
        components.html(js, height=0, width=0)
        return True
    except Exception:
        return False


def save_login(username: str, role: str, user_id, defer: bool = True) -> str:
    """签发登录令牌。返回令牌字符串（调用方负责在合适时机落盘）。

    调用时机：`check_password_hash` 通过之后（登录成功）。

    defer=True（默认）时**不在本函数内渲染 iframe**，而是把令牌放进
    `st.session_state._auth_pending`，由 `flush_pending_cookie()` 在页面渲染时
    统一落盘 —— 因为"登录成功"分支紧接着就会 `st.rerun()`，
    此时渲染的 iframe 会被 rerun 丢掉，写不进去。
    """
    tok = make_token(username, role, user_id)
    if defer:
        st.session_state._auth_pending = tok
        return tok
    _write_cookie(tok, COOKIE_MAX_AGE)
    return tok


def flush_pending_cookie() -> bool:
    """若存在待写入/待清除的令牌，则渲染 iframe 落盘。由 app.py 每轮调用。

    处理两类待办：
      1) `_auth_pending`      登录成功待写入的令牌
      2) `_auth_pending_clear` 退出登录待清除的 cookie
    返回是否执行了写入。
    """
    did = False
    # ① 待清除（退出登录走 defer 通道，避开 rerun 丢弃渲染的问题）
    if st.session_state.pop("_auth_pending_clear", None):
        did = _write_cookie("", 0) or did
    # ② 待写入（登录成功）
    tok = st.session_state.pop("_auth_pending", None)
    if tok:
        did = _write_cookie(tok, COOKIE_MAX_AGE) or did
    return did


def clear_login(defer: bool = False) -> bool:
    """清除 cookie（退出登录时调用）。

    除了渲染清 cookie 的隐藏 iframe，还会设置会话内的 `_auth_logged_out`
    抑制标记 —— 见 `try_restore_login` 的说明：退出当轮紧接着 rerun 时，
    浏览器可能还没执行完清 cookie 的脚本，若不抑制，下一轮会「用旧 cookie
    把用户又自动登回来」，表现为「点退出没反应」。
    """
    try:
        st.session_state["_auth_logged_out"] = True
    except Exception:
        pass
    if defer:
        st.session_state._auth_pending_clear = True
        return True
    return _write_cookie("", 0)


def flush_pending_clear() -> bool:
    """若存在待清除标记，则渲染 iframe 把 cookie 置为过期。"""
    if not st.session_state.pop("_auth_pending_clear", None):
        return False
    return _write_cookie("", 0)


# ============================================================================
# 登录态恢复
# ============================================================================
def try_restore_login() -> bool:
    """若 cookie 里有有效令牌且当前未登录，则恢复登录态。返回是否恢复成功。

    必须在渲染任何页面**之前**调用。

    抑制逻辑
    --------
    若本会话刚执行过「退出登录」（session_state 里存在 `_auth_logged_out`），
    则**不再**从 cookie 恢复 —— 直到后续某一轮确认浏览器里的 cookie 已消失，
    才解除抑制（见 `flush_pending_cookie`）。否则会出现"点了退出、马上又被
    自动登回来"的假退出（cookie 清除是异步 JS，rerun 会跑在它前面）。
    """
    if st.session_state.get("logged_in"):
        return False
    if st.session_state.get("_auth_logged_out"):
        # 已置退出标记：仅当 cookie 确实已不在时解除抑制，避免被旧 cookie 复活
        if get_cookie_token():
            return False
        st.session_state["_auth_logged_out"] = False
        return False
    data = read_token(get_cookie_token())
    if not data:
        return False
    st.session_state.logged_in = True
    st.session_state.username = data.get("u")
    st.session_state.role = data.get("r")
    st.session_state.user_id = data.get("i")
    st.session_state._login_ts = time.time()
    st.session_state._restored = True   # 标记本次是自动恢复（用于提示/日志）
    return True


def check_session_timeout(touch: bool = True) -> bool:
    """应用内**闲置**超时检查（语义：闲置超过 SESSION_TIMEOUT 才登出）。

    与旧实现的区别（这是本次 Bug 的关键修复点）
    ------------------------------------------
    旧实现把 `_login_ts` 当成"**绝对**登录时刻"：只要登录后累计超过
    480 分钟（8 小时），无论用户是否一直在操作都会被打回登录页——
    这正是用户抱怨的"时间长就掉线"。现在改为"**闲置**超时"：
    - `_login_ts` 改为"最近一次活动时间"，每次脚本运行（用户交互会触发）
      都会刷新它，因此只要一直在用就永不过期；
    - 只有连续闲置超过 SESSION_TIMEOUT 才要求重登。

    安全兜底（防止误判把有效 session 踢掉）
    ----------------------------------------
    若 `_login_ts` 缺失/为 0（例如老会话、异常中断导致 session_state 不完整），
    不再直接判定超时，而是**补当前时间**——否则 `time.time() - 0` 会是一个
    天文数字，导致刚恢复登录就被立即登出并清 cookie（旧 app.py:6391 正是如此）。

    返回是否已登出。
    """
    if not st.session_state.get("logged_in"):
        return False
    ts = st.session_state.get("_login_ts", 0) or 0
    if ts and (time.time() - ts) > SESSION_TIMEOUT:
        # 真·闲置超时：登出并清 cookie
        st.session_state.logged_in = False
        st.session_state._login_ts = 0
        clear_login()
        return True
    # 未超时：刷新活动时间（闲置计时器归零）。touch=False 用于不希望
    # 刷新的场景（当前未使用，保留扩展位）。
    if touch:
        st.session_state._login_ts = time.time()
    return False


# ============================================================================
# 当前页面持久化（刷新后停留在原页面）
# ============================================================================
# 设计说明
# --------
# 侧边栏菜单是 `st.radio`，其选中值只存在于 session_state；浏览器刷新
# 会重建 session_state → radio 回到第一项（默认「🏠 工作台」）。
# 最轻量的持久化通道是 **URL query param**（`st.query_params`）：
#   1) 刷新后 query string 会保留，无需 cookie/新依赖；
#   2) 与登录态解耦，未登录时不读取（避免泄露内页）；
#   3) 分享/收藏链接还能直达页面，体验更好。
# 菜单名含 emoji，`st.query_params` 会自动做 percent-encoding，读回时自动解码，
# 无需手工 urllib.parse。为避免污染正常页面的 URL，仅在值变化时才写入。
PAGE_QP = "p"


def save_current_page(menu: str) -> None:
    """把当前页面名写进 URL query param（值未变时不写，减少无谓 rerun）。"""
    if not menu:
        return
    try:
        qp = st.query_params
        if qp.get(PAGE_QP) != menu:
            qp[PAGE_QP] = menu
    except Exception:
        # query_params 在极少数环境下可能不可用；失败不致命（最坏退化为首页）
        pass


def restore_page(menu_list) -> str | None:
    """从 query param 读回页面名，校验合法性后返回；非法/缺失返回 None。

    `menu_list` 之外的值一律丢弃 —— 保证即便手改了 URL，也只能落到合法菜单项，
    不会越权跳到不在该角色菜单里的页面（权限校验在调用方按 role 过滤 menu_list）。
    """
    try:
        raw = st.query_params.get(PAGE_QP)
    except Exception:
        return None
    if not raw:
        return None
    # 兼容 query_params 返回 list 的实现（?p=a&p=b）
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    if not raw:
        return None
    return raw if raw in menu_list else None
