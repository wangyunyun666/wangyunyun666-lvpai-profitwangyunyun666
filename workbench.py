# -*- coding: utf-8 -*-
"""工作台测试版 —— 在真实利润系统之上加一个工作台首页。

设计原则（重要）：
  本文件**不修改**任何原有业务代码，只做三件事：
    1. 顶部加一段"跳转通道"（query_params），约 8 行
    2. 菜单列表最前面插入一项 "🏠 工作台"
    3. 新增 workbench_page() 与对应 elif 分支
  原 23 个页面函数、原 23 个 elif 分支**一律不碰**。

数据来源：直接查真实数据库（SQLite），指标复用原系统的口径。
"""
from __future__ import annotations

import os
import sys

# 让本文件能 import 同目录下的原系统模块
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import streamlit as st

# ============================================================================
# 跳转通道（新增）—— 必须在任何 widget 创建之前执行
# ============================================================================
# 背景：Streamlit 的 st.radio 是"前端持有状态"的 widget，
# 在按钮回调里直接改 st.session_state[key] 会被前端值覆盖（实测验证过）。
# 唯一可靠的方式：把目标写进 query_params，下一轮脚本开头读取后再 clear()。
_WB_PENDING = "_wb_goto"


def _wb_handle_goto(menu_list):
    """处理工作台发起的跳转请求。必须在 radio 创建之前调用。"""
    qp = st.query_params
    target = qp.get(_WB_PENDING)
    if target:
        if target in menu_list:
            st.session_state["nav_menu"] = target
        try:
            qp.clear()
        except Exception:
            pass


def _wb_go(target: str):
    """按钮回调：把目标页面写进 query_params。"""
    st.query_params[_WB_PENDING] = target


# ============================================================================
# 样式（新增）—— 只在工作台页生效，不影响其他页面
# ============================================================================
_WB_CSS = """
<style>
.wb-hero { background: linear-gradient(120deg,#667eea 0%,#764ba2 100%);
  border-radius:16px; padding:24px 28px; color:#fff; margin-bottom:20px;
  box-shadow:0 6px 20px rgba(102,126,234,.25); }
.wb-hero h2 { margin:0 0 6px 0; font-size:1.45rem; color:#fff; font-weight:700; }
.wb-hero p { margin:0; opacity:.92; font-size:.88rem; }
.wb-sec { font-size:1.02rem; font-weight:700; color:#2c3e50; margin:24px 0 12px; }
.wb-tile { background:#fff; border-radius:12px; padding:15px 17px; border:1px solid #eef1f5;
  box-shadow:0 2px 8px rgba(0,0,0,.04); margin-bottom:6px; }
.wb-tile .ico { font-size:1.35rem; line-height:1.2; }
.wb-tile .nm { font-weight:600; color:#2c3e50; font-size:.9rem; margin-top:5px; }
.wb-tile .ds { color:#8b95a5; font-size:.76rem; margin-top:2px; }
.wb-alert { background:#fff8e1; border-left:5px solid #ffc107; padding:12px 15px;
  border-radius:9px; margin-bottom:9px; font-size:.86rem; color:#6b5b2a; line-height:1.6; }
.wb-ok { background:#eafaf1; border-left:5px solid #27ae60; padding:12px 15px;
  border-radius:9px; margin-bottom:9px; font-size:.86rem; color:#1e6b45; line-height:1.6; }
.wb-log { background:#fff; border-radius:12px; padding:16px 18px; border:1px solid #eef1f5;
  box-shadow:0 2px 8px rgba(0,0,0,.04); font-size:.83rem; color:#5a6675; line-height:2.05; }
.wb-log b { color:#2c3e50; }
/* 指标卡样式，避免长金额被截断 */
div[data-testid="stMetric"] { background:#fff; border-radius:14px; padding:16px 18px;
  box-shadow:0 2px 10px rgba(0,0,0,.05); border-left:4px solid #667eea; }
div[data-testid="stMetric"] div[data-testid="stMetricValue"] { font-size:1.5rem; }
</style>
"""


# ============================================================================
# 数据查询（新增）—— 只读，复用原系统口径
# ============================================================================
def _wb_query_month(period: str) -> dict:
    """查询某月（YYYY-MM）的工作台指标。只读，不改任何数据。"""
    from database import SessionLocal, Order, func
    from datetime import date
    import calendar

    y, m = map(int, period.split('-'))
    start = date(y, m, 1)
    end = date(y, m, calendar.monthrange(y, m)[1])
    prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
    prev_start = date(prev_y, prev_m, 1)
    prev_end = date(prev_y, prev_m, calendar.monthrange(prev_y, prev_m)[1])

    db = SessionLocal()
    try:
        def cnt(s, e):
            return db.query(func.count(Order.id)).filter(
                Order.selection_date >= s, Order.selection_date <= e).scalar() or 0

        cur_cnt = cnt(start, end)
        prev_cnt = cnt(prev_start, prev_end)

        # 订单金额（收入口径：套系金额 + 二销 - 退款）
        def income(s, e):
            r = db.query(
                func.coalesce(func.sum(Order.set_price), 0),
                func.coalesce(func.sum(Order.second_sales), 0),
                func.coalesce(func.sum(Order.refund), 0),
            ).filter(Order.selection_date >= s, Order.selection_date <= e).first()
            return float(r[0] or 0) + float(r[1] or 0) - float(r[2] or 0)

        # 直接成本
        from database import ActualDirectCost
        def direct(s, e):
            return float(db.query(func.coalesce(func.sum(ActualDirectCost.amount), 0))
                         .filter(ActualDirectCost.order_id.in_(
                             db.query(Order.order_id).filter(
                                 Order.selection_date >= s, Order.selection_date <= e)))
                         .scalar() or 0)

        # 拍摄费用覆盖率（用于待办提醒）
        from database import ActualDirectCost as ADC
        cur_ids = [o[0] for o in db.query(Order.order_id).filter(
            Order.selection_date >= start, Order.selection_date <= end).all()]
        shoot_rows = []
        if cur_ids:
            shoot_rows = db.query(ADC).filter(
                ADC.cost_item == '拍摄费用', ADC.order_id.in_(cur_ids)).all()
        hotel_orders = 0
        for rec in shoot_rows:
            if rec.remark and '酒店成本' in rec.remark:
                hotel_orders += 1

        return {
            'period': period,
            'cur_cnt': cur_cnt,
            'prev_cnt': prev_cnt,
            'cur_income': income(start, end),
            'prev_income': income(prev_start, prev_end),
            'cur_direct': direct(start, end),
            'hotel_orders': hotel_orders,
            'hotel_cover': (hotel_orders / cur_cnt * 100) if cur_cnt else 0,
            'shoot_records': len(shoot_rows),
        }
    finally:
        db.close()


def _wb_recent_logs(limit: int = 5) -> list:
    """最近操作日志（只读）。"""
    try:
        from database import SessionLocal, OperationLog
        db = SessionLocal()
        try:
            rows = (db.query(OperationLog)
                    .order_by(OperationLog.id.desc()).limit(limit).all())
            out = []
            for r in rows:
                ts = r.timestamp.strftime('%m-%d %H:%M') if r.timestamp else ''
                out.append(f"<b>{ts}</b>　{r.username or ''}　{r.action or ''}")
            return out
        finally:
            db.close()
    except Exception:
        return []


# ============================================================================
# 工作台页面（新增）
# ============================================================================
_WB_TILES = [
    ("📊", "生成利润表", "按月份生成利润表", "📊 生成利润表"),
    ("📁", "利润表历史", "查看历史快照", "📁 利润表历史"),
    ("💰", "导入实际成本", "拍摄账单等成本", "💰 导入实际直接成本"),
    ("👥", "员工工资管理", "工资清单导入", "👥 员工工资管理"),
]


def workbench_page():
    """工作台首页。"""
    import calendar
    st.markdown(_WB_CSS, unsafe_allow_html=True)

    username = st.session_state.get('username', '用户')

    # ---- 期间选择 ----
    from database import SessionLocal, Order
    db = SessionLocal()
    try:
        periods = set()
        for (sd,) in db.query(Order.selection_date).all():
            if not sd:
                continue
            # selection_date 可能是 date/datetime（SQLAlchemy 已解析），也可能是 str
            if hasattr(sd, 'strftime'):
                periods.add(sd.strftime('%Y-%m'))
            else:
                periods.add(str(sd)[:7])
        periods = sorted(periods, reverse=True)
    finally:
        db.close()

    c_sel, _ = st.columns([1, 3])
    with c_sel:
        period = st.selectbox("查看月份", periods, key='wb_period') if periods else None

    st.markdown(
        f"<div class='wb-hero'><h2>你好，{username} 👋</h2>"
        f"<p>当前查看：{period or '—'} · 全部业务</p></div>",
        unsafe_allow_html=True)

    if not period:
        st.info("暂无数据。")
        return

    try:
        d = _wb_query_month(period)
    except Exception as e:
        st.warning(f"数据读取失败：{e}")
        return

    def fmt(v):
        return f"¥{v/10000:,.2f}万" if abs(v) >= 10000 else f"¥{v:,.0f}"

    # ---- 指标卡 ----
    def pct(cur, prev):
        """环比百分比文案；上月为 0 时不显示。"""
        if not prev:
            return None
        return f"{(cur - prev) / abs(prev) * 100:+.1f}% 环比"

    c = st.columns(4)
    c[0].metric("💰 订单收入", fmt(d['cur_income']),
                pct(d['cur_income'], d['prev_income']),
                delta_color="inverse")
    c[1].metric("📉 直接成本", fmt(d['cur_direct']))
    c[2].metric("📦 订单总数", f"{d['cur_cnt']:,}", pct(d['cur_cnt'], d['prev_cnt']))
    c[3].metric("🏨 酒店覆盖率", f"{d['hotel_cover']:.1f}%",
                f"{d['hotel_orders']} 单含酒店")

    # ---- 常用入口 ----
    st.markdown("<div class='wb-sec'>⚡ 常用入口</div>", unsafe_allow_html=True)
    tile = ("<div class='wb-tile'><div class='ico'>{}</div>"
            "<div class='nm'>{}</div><div class='ds'>{}</div></div>")
    cols = st.columns(4)
    for i, (ico, nm, ds, target) in enumerate(_WB_TILES):
        with cols[i]:
            st.markdown(tile.format(ico, nm, ds), unsafe_allow_html=True)
            st.button("进入 →", key=f"wb_go_{i}", use_container_width=True,
                      on_click=_wb_go, args=(target,))

    # ---- 待办提醒 + 最近操作 ----
    left, right = st.columns([1, 1])

    with left:
        st.markdown("<div class='wb-sec'>🔔 待办提醒</div>", unsafe_allow_html=True)
        if d['hotel_cover'] < 18:
            st.markdown(
                f"<div class='wb-alert'>⚠️ <b>酒店成本覆盖率偏低</b><br>"
                f"{period} 仅有 {d['hotel_orders']} 单（{d['hotel_cover']:.1f}%）"
                f"产生了酒店成本，建议核实是否有漏录。</div>",
                unsafe_allow_html=True)
        else:
            st.markdown(
                f"<div class='wb-ok'>✅ 酒店成本覆盖率 {d['hotel_cover']:.1f}%，"
                f"处于正常水平。</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='wb-ok'>✅ 拍摄费用已录入 {d['shoot_records']:,} 条。</div>",
            unsafe_allow_html=True)

    with right:
        st.markdown("<div class='wb-sec'>📌 最近操作</div>", unsafe_allow_html=True)
        logs = _wb_recent_logs()
        if logs:
            st.markdown("<div class='wb-log'>" + "<br>".join(logs) + "</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown("<div class='wb-log'>暂无操作记录</div>",
                        unsafe_allow_html=True)

    st.divider()
    st.caption("这是工作台测试版。左侧菜单原有功能全部保留，可随时切换使用。")
