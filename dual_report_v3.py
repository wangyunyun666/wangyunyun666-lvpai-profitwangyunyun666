# -*- coding: utf-8 -*-
"""月度经营利润汇报 · 报告模板 v3（动态切换版）

相对 v2 的三点变化：
  1. 【动态】顶部口径开关，点一下整份报表在「实际口径 / 分摊口径 / 两口径对照」间切换；
  2. 【新增】同比（去年同月 / 去年同期累计）、环比（上月）；
  3. 【新增】均价行——套系均价、二销均价、单均收入、单均成本、单均利润。

用法::

    from dual_report_v3 import render
    html = render(cur_a, cur_b, prev_a, prev_b, yoy_a, yoy_b, meta)

参数
----
cur_a / cur_b   : 本期逐套系 DataFrame（实际口径 / 分摊口径），列名同 profit_engine
prev_a / prev_b : 环比上一期逐套系 DataFrame，没有就传 None
yoy_a / yoy_b   : 同比聚合 dict（来自 hist_2025.yoy_pack()），没有就传 None
meta            : {'span','biz','prev_label','yoy_label','gen'}

本模块只做展示，不改动任何计算口径。
"""
import html as _html
import re

import pandas as pd

# (列名, 展示名, 类别)   D = 直接成本, I = 间接成本
FEE = [
    ('拍摄费用', '拍摄费用', 'D'),
    ('推广费用（实际）', '推广费用', 'D'),
    ('交付费用（主持）', '交付费用（主持）', 'D'),
    ('交付费用（场地）', '交付费用（场地）', 'D'),
    ('交付费用（搭建）', '交付费用（搭建）', 'D'),
    ('鲜花费用', '鲜花费用', 'D'),
    ('微电影拍摄费用', '微电影拍摄费用', 'D'),
    ('微电影剪辑费用', '微电影剪辑费用', 'D'),
    ('二销选片费', '二销选片费', 'D'),
    ('像素蛋糕修图费', '像素蛋糕修图费', 'D'),
    ('后期修片费(一销)', '后期修片费(一销)', 'D'),
    ('后期修片费(二销)', '后期修片费(二销)', 'D'),
    ('工厂费用（一销）', '工厂费用（一销）', 'D'),
    ('工厂费用（二销）', '工厂费用（二销）', 'D'),
    ('人工成本', '人工成本', 'D'),
    ('房租、水电、办公费等', '房租、水电、办公费等', 'I'),
    ('税费及手续费', '税费及手续费', 'I'),
    ('样片研发', '样片研发', 'I'),
    ('场地铺设费', '场地铺设费', 'I'),
    ('舆情处理', '舆情处理', 'I'),
]
CAT_TXT = {'D': '直接成本', 'I': '间接成本'}

# 成本项 → 数据来源说明（每项一句话，汇报时照着念）
# 【账单类】= 导入账单的实际发生额；【计算类】= 系统按订单信息/标准成本算出来的
FEE_SRC = {
    '拍摄费用':
        '【账单】来源：「📸 拍摄费用账单」。系统从账单明细解析出酒店、摄化、景点、'
        '上山补助等（新疆账单也走这一页），取实际发生额。',
    '推广费用':
        '【录入+计算】来源：「📊 月度基础数据」录入的推广费、「⚙️ 推广费分摊设置」的婚礼占比。'
        '实际口径＝按业务线分摊各平台实际投放额；分摊口径＝婚礼占比分摊；'
        '新疆按固定 2450 元/单计。',
    '交付费用（主持）':
        '【账单】来源：「💒 交付费用」表的「主持费」列，取实际发生额。',
    '交付费用（场地）':
        '【账单】来源：「💒 交付费用」表的「场地费」列 + 「🏟️ 自租场地消耗」导入的消耗额。',
    '交付费用（搭建）':
        '【账单】来源：「💒 交付费用」表的「搭建费」列，取实际发生额。',
    '鲜花费用':
        '【账单】来源：「💒 交付费用」表的「鲜花费」列，取实际发生额。',
    '微电影拍摄费用':
        '【账单】来源：「🎬 微电影拍摄账单」，取供应商「价格合计」（缺则主机+辅机）。',
    '微电影剪辑费用':
        '【账单优先，缺失算】有「✂️ 微电影剪辑账单」记录时用实际值；'
        '没记录的订单回退套系标准成本库里的标准费用。',
    '二销选片费':
        '【账单优先，缺失算】有「🛒 二销选片账单」记录时用实际结算额；'
        '没记录的订单按「二销金额 × 45%」估算。',
    '像素蛋糕修图费':
        '【计算】由「📥 导入收入数据」的照片张数算出：照片数 × 0.55 元 × 费率'
        '（2026-04-01 起选片按 8%，此前按 10%）。非账单导入。',
    '后期修片费(一销)':
        '【实际优先，缺则按标准】有「🖼️ 修片账单」实际修片费时，按一销张数占比拆分；'
        '无实际发生时按标准单价 × 一销张数（2026-04-01 起按新价目 9~15 元，'
        '此前按套系标准成本库的「修片单价」，缺省旅拍 9 元 / 婚礼 10 元）。',
    '后期修片费(二销)':
        '【实际优先，缺则按标准】同上：有实际修片费取拆分后的二销部分；'
        '无实际时按标准单价 × 加修张数。',
    '工厂费用（一销）':
        '【实际优先，缺则樱桃云，再兜底标准】①有「🏭 工厂账单」实际发生额时取实际；'
        '②无实际用「樱桃云产品成本」；③都没有回退套系标准成本。'
        '樱桃云不分一销/二销，故与套系标准「工厂费用」比较：标准以内归一销。',
    '工厂费用（二销）':
        '【实际优先，缺则樱桃云，再兜底标准】同上：总额超出套系标准「工厂费用」'
        '的部分归二销（标准以内已归一销）。若后期樱桃云能区分一销/二销，'
        '可与标准对比误差后再择优采用。',
    '人工成本':
        '【计算】由「👥 员工工资管理」导入的各部门工资总额算出人均，再分摊到订单：'
        '实际口径 ÷ 选片订单数，分摊口径 ÷ 下单订单数。',
    '房租、水电、办公费等':
        '【录入】来源：「📈 录入间接成本」，按月录入（可批量导多期）。按业务线订单数分摊。',
    '税费及手续费':
        '【录入】来源：「📈 录入间接成本」，按月录入。银行结息等收入以负数冲减本项。按业务线订单数分摊。',
    '样片研发':
        '【录入+计算】「📈 录入间接成本」录入总额；新疆的样片研发按「新疆拍样费用」'
        '单独归集后分摊，其余按业务线订单数分摊。',
    '场地铺设费':
        '【录入】来源：「📈 录入间接成本」，按月录入。按业务线订单数分摊。',
    '舆情处理':
        '【录入】来源：「📈 录入间接成本」，按月录入。按业务线订单数分摊。',
}

MODE_NAME = {'a': '实际口径', 'b': '分摊口径'}


# ================================================================ 小工具
def _num(df, name):
    if df is None or name not in getattr(df, 'columns', []):
        return pd.Series([0.0])
    return pd.to_numeric(df[name], errors='coerce').fillna(0.0)


def _tot(df, name):
    return float(_num(df, name).sum())


def _e(s):
    return _html.escape(str(s))


def _w(v):
    """万元，2 位小数。"""
    if v is None:
        return '—'
    return f"{float(v) / 10000:,.2f}"


def _y(v):
    """元，2 位小数。"""
    if v is None:
        return '—'
    return f"{float(v):,.2f}"


def _q(v):
    if v is None:
        return '—'
    return f"{float(v):,.0f}"


def _p(v, base):
    b = float(base or 0)
    if v is None or not b:
        return '—'
    return f"{float(v) / b * 100:.2f}%"


def _cls(v):
    return 'pos' if float(v or 0) >= 0 else 'neg'


def _div(a, b):
    if a is None or not b:
        return None
    return float(a) / float(b)


def _tag(txt, kind):
    return f"<span class='tg tg-{kind}'>{_e(txt)}</span>"


# ================================================================ 聚合
def agg_df(df):
    """把逐套系 DataFrame 汇总成一份「利润表指标」（2026 当期数据，字段齐全）。"""
    if df is None or len(df) == 0:
        return None
    o = {}
    o['单数'] = float(_num(df, '套系数量').sum())
    o['收入'] = _tot(df, '总收入')
    o['套系金额'] = _tot(df, '套系金额')
    o['二销金额'] = _tot(df, '二销金额')
    o['退款'] = _tot(df, '客诉退款金额')
    o['推广'] = _tot(df, '推广费用（实际）')
    o['人工'] = _tot(df, '人工成本')
    d_all = sum(_tot(df, c) for c, _, k in FEE if k == 'D')
    i_all = sum(_tot(df, c) for c, _, k in FEE if k == 'I')
    o['直接小计'] = d_all
    o['间接'] = i_all
    o['其他直接'] = d_all - o['推广'] - o['人工']
    o['总成本'] = d_all + i_all
    o['利润'] = o['收入'] - o['总成本']
    o['利润率'] = _div(o['利润'], o['收入']) * 100 if o['收入'] else 0.0
    o['套系均价'] = _div(o['套系金额'], o['单数'])
    o['二销均价'] = _div(o['二销金额'], o['单数'])
    o['单均收入'] = _div(o['收入'], o['单数'])
    o['单均成本'] = _div(o['总成本'], o['单数'])
    o['单均利润'] = _div(o['利润'], o['单数'])
    o['利润未分摊'] = None
    o['fees'] = [(disp, cat, _tot(df, c)) for c, disp, cat in FEE]
    o['销量'] = _num(df, '套系数量').sum()
    return o


def agg_hist(rec):
    """把 2025 历史汇总行（只有 收入/单数/推广/人工/净利润）转成指标 dict。

    2025 表没有 13 项直接费用与 5 项间接费用的细分，因此这些字段为 None，
    同比列会显示「—」，其余可比字段（收入、单数、均价、利润、利润率）正常给出。
    """
    if not rec:
        return None
    o = {}
    inc, qty = float(rec.get('收入') or 0), float(rec.get('单数') or 0)
    net, pro, sal = float(rec.get('净利润') or 0), float(rec.get('推广') or 0), float(rec.get('人工') or 0)
    o['单数'] = qty
    o['收入'] = inc
    o['推广'] = pro
    o['人工'] = sal
    o['总成本'] = inc - net
    o['利润'] = net
    o['净利润'] = net
    o['利润率'] = _div(net, inc) * 100 if inc else 0.0
    o['单均收入'] = _div(inc, qty)
    o['单均成本'] = _div(inc - net, qty)
    o['单均利润'] = _div(net, qty)
    for k in ('套系金额', '二销金额', '套系均价', '二销均价', '直接小计', '间接', '其他直接', '利润未分摊'):
        o[k] = None
    o['fees'] = []
    return o


# ================================================================ 同比 / 环比格
def _delta(cur, base, kind='money'):
    """生成「同比 / 环比」单元格。

    kind: money 万元 / price 元 / qty 单 —— 显示百分比变化
          pt 利润率 —— 显示百分点变化
    颜色统一为「↑ 红、↓ 绿、持平 灰」，只表示数值升降。
    """
    if cur is None or base is None:
        return "<td class='dl na'>—</td>"
    cur, base = float(cur), float(base)
    if abs(base) < 1e-9:
        return "<td class='dl na'>—</td>"
    if kind == 'pt':
        d = cur - base
        if abs(d) < 0.005:
            return "<td class='dl fl'>→ 0.00pt</td>"
        ar, c = ('↑', 'up') if d > 0 else ('↓', 'dn')
        return f"<td class='dl {c}'>{ar} {d:+.2f}pt</td>"
    d = cur - base
    pct = d / abs(base) * 100
    if abs(pct) < 0.05:
        return "<td class='dl fl'>→ 0.0%</td>"
    ar, c = ('↑', 'up') if d > 0 else ('↓', 'dn')
    return f"<td class='dl {c}'>{ar} {pct:+.1f}%</td>"


def _why_txt(disp, v, pv, qc, qp, cat):
    """生成成本项「环比原因」注释：把金额环比拆成 单量 × 单均 两个效应。

    - 直接成本(D)：金额 ≈ 单量 × 单均，拆开后指出主因/次因；
    - 间接成本(I)：按月期间费用，总额不随单量，只提示摊薄效应；
    - 上期无发生额 / 本期无发生额 单独说明。
    纯文字说明，不参与任何数字计算，改错也不影响报表数值。
    """
    try:
        if pv is None or v is None:
            return "上期无发生额（新增科目或上月未录入）"
        v, pv = float(v), float(pv)
        if v < 0 or pv < 0:
            # 净额为负＝结息等收入冲减（如银行结息记在「税费及手续费」冲减手续费），
            # 此时百分比变化无意义，直接用文字说明两期净额。
            if v < 0 <= pv:
                return (f"本期净额为负（结息等收入冲减 {_y(abs(v))} 元），"
                        f"较上月少支出 {_y(pv - v)} 元")
            if pv < 0 <= v:
                return f"上月为净收入冲减（{_y(abs(pv))} 元），本期转为正成本 {_y(v)} 元"
            return f"两期均为净收入冲减：{_y(abs(pv))} → {_y(abs(v))} 元"
        if pv <= 0:
            return "上期无发生额（新增科目或上月未录入）"
        if v <= 0:
            return "本期无发生额"
        tot = v / pv - 1.0
        if abs(tot) < 0.0005:
            return "与上月基本持平"
        qc = float(qc or 0)
        qp = float(qp or 0)
        has_q = qc > 0 and qp > 0
        if cat == 'I':
            if has_q:
                qa, qb = v / qc, pv / qp
                dq = qc / qp - 1.0
                if abs(dq) < 0.005:
                    return f"期间费用（不随单量），总额 {tot:+.1%}；单量基本持平"
                return (f"期间费用（不随单量），总额 {tot:+.1%}；"
                        f"单量 {dq:+.1%} → 单均摊 {_y(qb)}→{_y(qa)} 元/单")
            return f"期间费用（不随单量），总额 {tot:+.1%}"
        if not has_q:
            return f"总额 {tot:+.1%}（无单量基数可拆）"
        qa, qb = v / qc, pv / qp
        dq, da = qc / qp - 1.0, qa / qb - 1.0
        if abs(dq) < 0.005 and abs(da) < 0.005:
            return "单量与单均基本持平"
        if abs(da) < 0.005:
            return f"主因单量 {dq:+.1%}（{_q(qp)}→{_q(qc)} 单），单均基本持平"
        if abs(dq) < 0.005:
            return f"主因单均 {da:+.1%}（{_y(qb)}→{_y(qa)} 元/单），单量基本持平"
        if abs(dq) >= abs(da):
            return (f"主因单量 {dq:+.1%}（{_q(qp)}→{_q(qc)} 单）；"
                    f"次因单均 {da:+.1%}（{_y(qb)}→{_y(qa)} 元/单）")
        return (f"主因单均 {da:+.1%}（{_y(qb)}→{_y(qa)} 元/单）；"
                f"次因单量 {dq:+.1%}（{_q(qp)}→{_q(qc)} 单）")
    except Exception:
        return "—"


def _dcell_money(cur, base, kind='money'):
    """给「金额/单价」行用：同时给出基准数与变化率两列。"""
    if base is None:
        return "<td class='na'>—</td><td class='dl na'>—</td>"
    if kind == 'money':
        b = _w(base)
    elif kind == 'price':
        b = _y(base)
    elif kind == 'pt':
        b = f"{float(base):.2f}%"
    else:
        b = _q(base)
    return f"<td class='base'>{b}</td>" + _delta(cur, base, kind)


# ================================================================ 主函数
def render(cur_a, cur_b, prev_a, prev_b, yoy_a, yoy_b, meta, extra=None, segments=None):
    """渲染经营利润汇报。

    Args:
        segments: 业务线分段。给定时每段各自出一份完整报表（并列展示），
            形如 [{'key':'旅拍','name':'旅拍','pick':fn,'extra':{...}}, ...]；
            为 None 时退化为原来的单份报表（全部业务合并）。
    """
    if segments:
        return _render_segments(cur_a, cur_b, prev_a, prev_b, yoy_a, yoy_b,
                                meta, extra, segments)
    return _TPL.replace('/*__BODY__*/',
                        _render_inner(cur_a, cur_b, prev_a, prev_b, yoy_a, yoy_b,
                                      meta, extra))


def _render_inner(cur_a, cur_b, prev_a, prev_b, yoy_a, yoy_b, meta, extra=None):
    """生成报表「主体 HTML 片段」（不含 <html>/<style>/<script> 外壳）。

    ⚠️ 分业务线导出时必须用本函数，不能用 render()：
    早前 _render_segments 直接拼 render() 的返回值，等于把「整份文档」塞进
    「整份文档」，导出的 HTML 里出现 1 个外层 + N 个内层的 DOCTYPE/<head>/<style>/
    <script>，文件因此膨胀约 4 倍，复制进 Word / 邮件预览时格式易乱。
    """
    span = meta.get('span', '')
    biz = meta.get('biz', '全部')
    pl = meta.get('prev_label') or '上期'
    yl = meta.get('yoy_label') or '上年同期'
    gen = meta.get('gen') or ''
    has_prev = prev_a is not None or prev_b is not None
    has_yoy = bool(yoy_a or yoy_b)

    S = {
        'a': {'df': cur_a, 'cur': agg_df(cur_a), 'prev': agg_df(prev_a), 'yoy': yoy_a},
        'b': {'df': cur_b, 'cur': agg_df(cur_b), 'prev': agg_df(prev_b), 'yoy': yoy_b},
    }
    if S['a']['cur'] is None and S['b']['cur'] is not None:
        S['a'] = S['b']
    has_b = S['b']['cur'] is not None
    if not has_b:
        S['b'] = None

    A, B = S['a']['cur'], S['b']['cur']
    # 套系级两口径合并（红黑榜 / 建议用）
    mm = _merge_sets(cur_a, cur_b)

    # ---------- 页头
    q_a, q_b = A['单数'], B['单数']
    head = (
        "<div class='hd'>"
        f"<h1>{_e(span)} 经营利润汇报</h1>"
        f"<div class='sub'>动态口径切换 · 含同比 / 环比 / 均价</div>"
        f"<div class='meta'>业务范围：{_e(biz)}　·　拍摄订单 {q_a:,.0f} 单"
        f"　·　生成时间：{_e(gen)}</div>"
        "<div class='sw'>"
        "<button class='sw-b on' data-m='a'>实际口径</button>"
        + ("<button class='sw-b' data-m='b'>分摊口径</button>" if has_b else "")
        + ("<button class='sw-b' data-m='both'>两口径对照</button>" if has_b else "")
        + "</div>"
        + ("</div>" if has_b else
           "<div class='meta sw-warn'>⚠ 本期只导出了实际口径报表，分摊口径需在系统里再导一份"
           "（费用口径选「分摊口径」）。</div></div>")
    )

    # ---------- 各口径视图
    extra = extra if isinstance(extra, dict) else ({'a': extra, 'b': extra} if extra else {})
    body = head
    for k in ('a', 'b'):
        s = S[k]
        body += f"<div class='v v-{k}'>"
        if s is None:
            body += _notice('本期没有这份口径的报表，导出后即可同样展示。')
        else:
            body += _view_one(k, s, mm, span, biz, pl, yl, has_prev, has_yoy,
                              extra.get(k))
        body += "</div>"
    body += "<div class='v v-both'>"
    body += _view_both(A, B, mm, span, biz) if has_b else \
        _notice('两口径对照需要同一期导出「实际口径」与「分摊口径」两份报表。')
    body += "</div>"
    body += _foot(span, biz, gen, has_yoy, has_prev, pl, yl, meta.get('yoy_src'))
    return body


def _notice(txt):
    return (f"<div class='notice-card'><b>提示</b>　{_e(txt)}</div>")


# ------------------------------------------------------- 业务线分段（旅拍/婚礼/新疆）
def _seg_pick(df, seg):
    """按分段定义从一份报表里切出该业务线的子集（各段互斥，合计＝全量）。

    分段约定：
      kind='all'      → 全量；
      kind='equal'    → 业务类型等于 value，且**不含** exclude_like 关键字；
      kind='set_like' → 套系名包含 value（如「新疆」，跨旅拍 / 婚礼两条业务线）。
    """
    if df is None or len(df) == 0:
        return df
    out = df
    if seg.get('kind') == 'set_like':
        out = out[out['套系'].astype(str).str.contains(seg['value'], na=False)]
    elif seg.get('kind') == 'equal':
        out = out[out['业务类型'].astype(str) == seg['value']]
    if seg.get('exclude_like'):
        out = out[~out['套系'].astype(str).str.contains(seg['exclude_like'], na=False)]
    return out.copy()


def _render_segments(cur_a, cur_b, prev_a, prev_b, yoy_a, yoy_b, meta, extra, segments):
    """每段一份完整报表，纵向并列；段内仍可用右上角开关切口径。"""
    span, biz = meta.get('span', ''), meta.get('biz', '')
    pl = meta.get('prev_label') or '上期'
    yl = meta.get('yoy_label') or '上年同期'
    gen = meta.get('gen') or ''
    is_split = len(segments) > 1

    parts = [
        "<div class='hd'>"
        f"<h1>{_e(span)} 经营利润汇报</h1>"
        f"<div class='sub'>分业务线对照 · 含同比 / 环比 / 均价</div>"
        f"<div class='meta'>业务范围：{_e(biz)}　·　"
        f"拆分口径：{' / '.join(str(s.get('name', s.get('key'))) for s in segments)}"
        f"　·　生成时间：{_e(gen)}</div>"
        "</div>"]

    for i, seg in enumerate(segments):
        pick = seg.get('pick') or (lambda d, s=seg: _seg_pick(d, s))
        ca, cb = pick(cur_a), pick(cur_b)
        pa, pb = pick(prev_a), pick(prev_b)
        ya = seg.get('yoy_a', yoy_a)
        yb = seg.get('yoy_b', yoy_b)
        ex = seg.get('extra', extra)

        has_any = any(x is not None and len(x) > 0 for x in (ca, cb))
        meta2 = dict(meta)
        meta2['biz'] = seg.get('name', seg.get('key', ''))
        meta2['gen'] = gen
        if not has_any:
            parts.append(
                f"<div class='seg' id='seg-{_e(seg.get('key', i))}'>"
                f"<div class='seg-hd'>{_e(seg.get('name', seg.get('key', '')))}</div>"
                + _notice('本期该业务线没有订单数据，报表为空。') + "</div>")
            continue

        inner = (f"<div class='seg' id='seg-{_e(seg.get('key', i))}'>"
                 f"<div class='seg-hd'>{_e(seg.get('name', seg.get('key', '')))}</div>")
        inner += _render_inner(ca, cb, pa, pb, ya, yb, meta2, extra=ex)
        inner += "</div>"
        parts.append(inner)

    body = ''.join(parts)
    if is_split:
        body += ("<div class='rt-bar' id='rtBar'>"
                 + ''.join(f"<a href='#seg-{_e(s.get('key', i))}'>{_e(s.get('name', s.get('key', '')))}</a>"
                           for i, s in enumerate(segments))
                 + "</div>")
    return _TPL.replace('/*__BODY__*/', body)


# ---------------------------------------------------------------- 单口径视图
def _view_one(k, s, mm, span, biz, pl, yl, has_prev, has_yoy, extra=None):
    cur, prev, pack = s['cur'], s['prev'], (s['yoy'] or {})
    yoy = agg_hist(pack.get('all'))   # 整体同比基准
    name = MODE_NAME[k]
    n1 = int(cur['单数'])
    n_sets = int(s['df'].pipe(_setcnt)) if s['df'] is not None and len(s['df']) else 0

    # ===== 摘要
    inc, prof, rate = cur['收入'], cur['利润'], cur['利润率']
    yoy_txt = ''
    if has_yoy and yoy and yoy.get('利润') is not None:
        d = prof - yoy['利润']
        pct = d / abs(yoy['利润']) * 100 if yoy['利润'] else 0
        yoy_txt = (f"同比去年：收入 {'增' if inc >= yoy['收入'] else '减'} "
                   f"{abs(inc - yoy['收入']) / 10000:,.2f} 万，"
                   f"利润 {'增' if d >= 0 else '减'} <b>{abs(d) / 10000:,.2f} 万</b>（{pct:+.1f}%）。")
    prev_txt = ''
    if has_prev and prev:
        d = prof - prev['利润']
        pct = d / abs(prev['利润']) * 100 if prev['利润'] else 0
        prev_txt = (f"环比{pl}：收入 {'增' if inc >= prev['收入'] else '减'} "
                    f"{abs(inc - prev['收入']) / 10000:,.2f} 万，"
                    f"利润 {'增' if d >= 0 else '减'} <b>{abs(d) / 10000:,.2f} 万</b>（{pct:+.1f}%）。")

    summ = (
        "<div class='sum'>"
        f"<div class='sum-t'>{_e(span)} · {_e(biz)} · {name} · 一句话结论</div>"
        "<ul class='sum-l'>"
        f"<li>共 <b>{n_sets}</b> 个套系、<b>{n1:,}</b> 单，总收入 <b>{_w(inc)} 万元</b>，"
        f"单均收入 <b>{_y(cur['单均收入'])} 元</b>。</li>"
        f"<li>净利润 <b>{_w(prof)} 万元</b>，利润率 <b>{rate:.2f}%</b>，"
        f"单均利润 <b>{_y(cur['单均利润'])} 元</b>。</li>"
        f"<li>{yoy_txt}{prev_txt}</li>"
        f"<li>成本合计 <b>{_w(cur['总成本'])} 万元</b>（占收入 {_p(cur['总成本'], inc)}），"
        f"其中推广费 {_w(cur['推广'])} 万（{_p(cur['推广'], inc)}）、"
        f"人工成本 {_w(cur['人工'])} 万（{_p(cur['人工'], inc)}）。</li>"
        "</ul></div>"
    )

    # ===== KPI（含同比 / 环比）
    def kpi(title, val, unit, sub, cur_v, prev_v, yoy_v, kind='money'):
        pv = prev_v if has_prev else None
        yv = yoy_v if has_yoy else None
        cells = []
        for lab, base in ((f'同比 {yl}', yv), (f'环比 {pl}', pv)):
            if base is None:
                cells.append(f"<div class='kpi-d'><span class='kdl'>{_e(lab)}</span>"
                             "<span class='kdd na'>—</span></div>")
                continue
            cells.append(f"<div class='kpi-d'><span class='kdl'>{_e(lab)}</span>"
                         + _delta(cur_v, base, kind).replace("<td class='dl ", "<span class='kdd ")
                         .replace("</td>", "</span>") + "</div>")
        return (f"<div class='kpi'><div class='k'>{_e(title)}</div>"
                f"<div class='v'>{val}<span class='u'>{unit}</span></div>"
                f"<div class='s'>{sub}</div>{''.join(cells)}</div>")

    kpis = (
        "<div class='kpis'>"
        + kpi('总收入', _w(inc), '万', f"单均 {_y(cur['单均收入'])} 元",
              inc, prev['收入'] if prev else None, yoy['收入'] if yoy else None)
        + kpi('拍摄订单', _q(n1), '单', f"{n_sets} 个套系",
              n1, prev['单数'] if prev else None, yoy['单数'] if yoy else None, 'qty')
        + kpi('净利润', _w(prof), '万', f"利润率 {rate:.2f}%",
              prof, prev['利润'] if prev else None, yoy['利润'] if yoy else None)
        + kpi('单均利润（均价）', _y(cur['单均利润']), '元', '利润 ÷ 订单数',
              cur['单均利润'], prev['单均利润'] if prev else None,
              yoy['单均利润'] if yoy else None, 'price')
        + "</div>"
    )

    # ===== 一、利润表
    def row(label, key, kind='money', cls='', indent=False):
        v = cur.get(key)
        base_yoy = yoy.get(key) if yoy else None
        base_prev = prev.get(key) if prev else None
        if kind == 'pt':
            c1 = f"<td>{v:.2f}%</td>" if v is not None else "<td class='na'>—</td>"
        elif kind == 'price':
            c1 = f"<td>{_y(v)}</td>"
        elif kind == 'qty':
            c1 = f"<td>{_q(v)}</td>"
        else:
            c1 = f"<td>{_w(v)}</td>"
        pc = _p(v, inc) if kind == 'money' else '—'
        out = (f"<tr class='{cls}'><td class='l{' ind' if indent else ''}'>{label}</td>"
               + c1 + f"<td class='pc'>{pc}</td>")
        if show_yoy:
            out += _dcell_money(v, base_yoy, kind)
        if show_prev:
            out += _dcell_money(v, base_prev, kind)
        return out + "</tr>"

    show_yoy, show_prev = yoy is not None, prev is not None
    ncol = 3 + (2 if show_yoy else 0) + (2 if show_prev else 0)

    # ---- 可折叠明细行：其他直接费用 / 间接成本
    def _detail_trs(g, cat, exclude_disp=()):
        """生成某类费用的明细行（默认隐藏，点汇总行展开）。"""
        pv_map = {d2: v2 for d2, c2, v2 in prev['fees']} if prev else {}
        out = []
        for disp, c2, v in cur['fees']:
            if c2 != cat or disp in exclude_disp or abs(v) < 0.005:
                continue
            out.append(
                f"<tr class='xrow' data-g='{g}'><td class='l ind2'>└ {_e(disp)}</td>"
                f"<td>{_w(v)}</td><td class='pc'>{_p(v, inc)}</td>"
                + ("<td class='na'>—</td><td class='dl na'>—</td>" if show_yoy else "")
                + (_dcell_money(v, pv_map.get(disp), 'money') if show_prev else "")
                + "</tr>")
        if not out:
            out.append(
                f"<tr class='xrow' data-g='{g}'><td class='l ind2'>└ （无发生额）</td>"
                "<td>—</td><td class='pc'>—</td>"
                + ("<td class='na'>—</td><td class='dl na'>—</td>" if show_yoy else "")
                + (_dcell_money(None, None, 'money') if show_prev else "") + "</tr>")
        return ''.join(out)

    def row_t(label, key, g):
        """可点击展开/收起的汇总行。"""
        out = row(label, key, cls='', indent=True)
        return out.replace(
            "<td class='l ind'>",
            f"<td class='l ind togg' data-g='{g}' onclick='_xt(this)' "
            f"title='点击展开/收起明细'><span class='tw'>▸</span> ", 1)

    _dir_detail = _detail_trs('d', 'D', exclude_disp=('推广费用', '人工成本'))
    _ind_detail = _detail_trs('i', 'I')

    t1 = (
        "<div class='tb'><table><thead><tr>"
        "<th class='l'>项目</th><th>本期</th><th>占收入</th>"
        + (f"<th>{yl}</th><th>同比</th>" if show_yoy else "")
        + (f"<th>{pl}</th><th>环比</th>" if show_prev else "")
        + "</tr></thead><tbody>"
        + row('总收入 <span class="un">万元</span>', '收入')
        + row('拍摄订单数 <span class="un">单</span>', '单数', 'qty')
        + f"<tr class='dim'><td class='l' colspan='{ncol}'>均价（元 / 单）</td></tr>"
        + row('套系均价', '套系均价', 'price', indent=True)
        + row('二销均价', '二销均价', 'price', indent=True)
        + row('单均收入（客单价）', '单均收入', 'price', indent=True)
        + row('单均成本', '单均成本', 'price', indent=True)
        + row('单均利润', '单均利润', 'price', indent=True)
        + f"<tr class='dim'><td class='l' colspan='{ncol}'>减：成本</td></tr>"
        + row('推广费用', '推广', 'money', indent=True)
        + row('人工成本', '人工', 'money', indent=True)
        + row_t('其他直接费用（13 项）', '其他直接', 'd')
        + _dir_detail
        + row('直接成本小计', '直接小计', 'money', cls='sub')
        + row_t('间接成本（5 项）', '间接', 'i')
        + _ind_detail
        + row('成本合计', '总成本', 'money', cls='sub')
        + f"<tr class='dim'><td class='l' colspan='{ncol}'>等于：利润</td></tr>"
        + row('净利润', '利润', 'money', cls='key')
        + row('利润率', '利润率', 'pt')
        + "</tbody></table></div>"
    )
    s1 = ("<div class='sec'><span>一</span>利润表（万元 · 含均价与同比环比）</div>"
          f"<div class='hint2'>同比基准：{_e(yl)}　环比基准：{_e(pl)}　"
          "箭头颜色仅表示数值升降（红＝上升，绿＝下降），不代表好坏。</div>" + t1)

    # ===== 二、业务线总览
    def by_biz(df):
        if df is None or len(df) == 0 or '业务类型' not in df.columns:
            return {}
        g = pd.DataFrame()
        g['业务类型'] = df['业务类型'].fillna('').astype(str).values
        g['单数'] = _num(df, '套系数量').values
        g['收入'] = _num(df, '总收入').values
        for c, _, _ in FEE:
            g[c] = _num(df, c).values
        out = {}
        for k2, sub in g.groupby('业务类型'):
            q = float(sub['单数'].sum())
            inc2 = float(sub['收入'].sum())
            cost = sum(float(sub[c].sum()) for c, _, _ in FEE)
            p = inc2 - cost
            out[str(k2)] = {'单数': q, '收入': inc2, '利润': p,
                            '利润率': (p / inc2 * 100) if inc2 else 0.0,
                            '单均收入': (inc2 / q) if q else None,
                            '单均利润': (p / q) if q else None}
        return out

    ba = by_biz(s['df'])
    rows = []
    order = [x for x in ('旅拍', '婚礼') if x in ba] + [x for x in ba if x not in ('旅拍', '婚礼')]
    for k2 in order:
        r = ba[k2]
        yr = agg_hist(pack.get(k2)) if pack else None
        q = r['单数']
        rows.append(
            "<tr>"
            f"<td class='l'>{_e(k2)}</td><td>{_q(q)}</td><td>{_w(r['收入'])}</td>"
            f"<td>{_y(r['单均收入'])}</td>"
            f"<td class='{_cls(r['利润'])}'>{_w(r['利润'])}</td>"
            f"<td>{r['利润率']:.2f}%</td><td>{_y(r['单均利润'])}</td>"
            + (_delta(r['收入'], (yr or {}).get('收入'), 'money')
               + _delta(r['利润'], (yr or {}).get('净利润'), 'money') if show_yoy else '')
            + "</tr>")
    rows.append(
        "<tr class='tot'><td class='l'>合计</td><td>{}</td><td>{}</td><td>{}</td>"
        "<td class='{}'>{}</td><td>{:.2f}%</td><td>{}</td>{}</tr>".format(
            _q(cur['单数']), _w(inc), _y(cur['单均收入']), _cls(prof), _w(prof), rate,
            _y(cur['单均利润']),
            (_delta(inc, (yoy or {}).get('收入'), 'money')
             + _delta(prof, (yoy or {}).get('净利润'), 'money')) if show_yoy else ''))
    s2 = ("<div class='sec'><span>二</span>业务线总览</div>"
          "<div class='tb'><table><thead><tr>"
          "<th class='l'>业务线</th><th>订单</th><th>收入(万)</th><th>单均收入(元)</th>"
          "<th>净利润(万)</th><th>利润率</th><th>单均利润(元)</th>"
          + (f"<th>{yl} 收入同比</th><th>{yl} 利润同比</th>" if show_yoy else "")
          + "</tr></thead><tbody>" + ''.join(rows) + "</tbody></table></div>")

    # ===== 三、套系盈亏榜（按当前口径）
    idx = 'pa' if k == 'a' else 'pb'
    s3 = _rank_section(mm, idx, name)

    # ===== 四、成本结构
    fee_rows = []
    for disp, cat, v in cur['fees']:
        # 只跳过「没有发生额」的科目（<0.5 元视为 0）；
        # 负数（结息等收入冲减，如税费及手续费净额为负）要照常显示，否则表里少一行、对账也对不上
        if abs(v) < 0.00005:
            continue
        kind2 = 'd' if cat == 'D' else 'i'
        pv = None
        if prev:
            for d2, c2, v2 in prev['fees']:
                if d2 == disp:
                    pv = v2
                    break
        src = FEE_SRC.get(disp, '—')
        why = ''
        if show_prev:
            why = _why_txt(disp, v, pv, cur.get('单数'), prev.get('单数') if prev else None, cat)
        fee_rows.append((v,
                         f"<tr><td class='l'>{_e(disp)}</td>"
                         f"<td class='c'>{_tag(CAT_TXT[cat], kind2)}</td>"
                         f"<td>{_w(v)}</td><td class='pc'>{_p(v, inc)}</td>"
                         + (_delta(v, pv, 'money') if show_prev else '')
                         + (f"<td class='src'>{_e(why)}</td>" if show_prev else '')
                         + f"<td class='src'>{_e(src)}</td>"
                         + "</tr>"))
    fee_rows.sort(key=lambda t: -t[0])
    cost_all = cur['总成本']
    s4 = ("<div class='sec'><span>四</span>成本结构（按金额降序）</div>"
          "<div class='tb'><table><thead><tr>"
          "<th class='l'>费用项</th><th class='c' style='width:88px'>类别</th>"
          "<th style='width:110px'>金额(万)</th><th class='pc' style='width:90px'>占收入</th>"
          + (f"<th style='width:110px'>环比 {_e(pl)}</th>" if show_prev else "")
          + ("<th class='l' style='width:270px'>环比原因</th>" if show_prev else "")
          + "<th class='l' style='width:300px'>数据来源</th>"
          + "</tr></thead><tbody>" + ''.join(h for _, h in fee_rows)
          + f"<tr class='tot'><td class='l'>成本合计</td><td class='c'>—</td>"
            f"<td>{_w(cost_all)}</td><td class='pc'>{_p(cost_all, inc)}</td>"
          + (_delta(cost_all, prev['总成本'] if prev else None, 'money') if show_prev else '')
          + ("<td class='src'>" + _e(_why_txt('成本合计', cost_all,
                                              prev['总成本'] if prev else None,
                                              cur.get('单数'), prev.get('单数') if prev else None,
                                              'D')) + "</td>" if show_prev else '')
          + "<td class='src'>以上各项实际发生额之和，与利润表「成本合计」一致。</td>"
          + "</tr></tbody></table></div>"
          + ("<div style='padding:6px 2px;color:#64748b;font-size:12px'>"
             "💡 环比原因按「单量 × 单均」拆分：单均 = 该费用项金额 ÷ 本表订单数；"
             "间接成本为按月期间费用，总额不随单量变化，单均只反映摊薄效应。</div>"
             if show_prev else ""))

    # ===== 五+、系统分析块（价盘均价 / 拍摄费用 / 人工部门）
    extras = _extra_html(extra)
    n_ext = sum(1 for x in (extra or []) if x and x[0] != '__notice__')

    # ===== 经营建议
    s5 = _tips(cur, prev, yoy, mm, idx, name, inc, prof, rate, span, pl, yl,
               has_prev, has_yoy, sec_no=5 + n_ext)

    return summ + kpis + s1 + s2 + s3 + s4 + ''.join(extras) + s5


def _setcnt(df):
    return int((pd.to_numeric(df['套系数量'], errors='coerce').fillna(0.0) > 0).sum())


def _merge_sets(cur_a, cur_b):
    """合并两口径的逐套系利润，得到 pa / pb。"""
    A = cur_a if cur_a is not None else cur_b
    B = cur_b if cur_b is not None else cur_a
    if A is None or B is None or len(A) == 0 or len(B) == 0:
        return pd.DataFrame()

    def prof(df):
        s = _num(df, '总收入').reset_index(drop=True)
        for c, _, _ in FEE:
            s = s - _num(df, c).reset_index(drop=True)
        return s

    la = pd.DataFrame({'套系': A['套系'].astype(str)})
    la['业务类型'] = A['业务类型'].astype(str) if '业务类型' in A.columns else ''
    la['单数'] = _num(A, '套系数量').values
    la['收入'] = _num(A, '总收入').values
    la['pa'] = prof(A).values
    lb = pd.DataFrame({'套系': B['套系'].astype(str)})
    lb['pb'] = prof(B).values
    m = pd.merge(la, lb, on='套系', how='outer')
    m['业务类型'] = m['业务类型'].fillna('')
    for c in ('单数', '收入', 'pa', 'pb'):
        m[c] = pd.to_numeric(m[c], errors='coerce').fillna(0.0)
    return m


def _rank_section(mm, idx, name):
    if not len(mm):
        return ''
    top = mm.sort_values(idx, ascending=False).head(10)
    neg = mm[mm[idx] < 0].sort_values(idx).head(10)

    def tbl(rows, kind):
        body = []
        for i, r in enumerate(rows.itertuples(), 1):
            q = float(getattr(r, '单数', 0) or 0)
            p = float(getattr(r, idx, 0) or 0)
            other = float(getattr(r, 'pb' if idx == 'pa' else 'pa', 0) or 0)
            avg = (p / q) if q else 0.0
            body.append(
                f"<tr><td class='c rk'>{i}</td><td class='l'>{_e(getattr(r, '套系', ''))}</td>"
                f"<td class='c'>{_e(getattr(r, '业务类型', '') or '')}</td>"
                f"<td class='c'>{q:,.0f}</td>"
                f"<td class='{_cls(p)}'>{_w(p)}</td>"
                f"<td class='{_cls(avg)}'>{_y(avg)}</td>"
                f"<td class='{_cls(other)}'>{_w(other)}</td></tr>")
        if not body:
            body.append("<tr><td colspan='7' class='empty'>无</td></tr>")
        return ("<div class='rank " + kind + "'><div class='rt'>"
                + ('盈利 TOP10' if kind == 'good' else '亏损 TOP10') + "</div>"
                "<div class='tb'><table><thead><tr>"
                "<th class='c' style='width:32px'>#</th><th class='l'>套系</th>"
                "<th class='c' style='width:46px'>业务</th><th style='width:54px'>订单</th>"
                "<th style='width:84px'>利润(万)</th><th style='width:88px'>单均利润(元)</th>"
                "<th style='width:84px'>另一口径(万)</th>"
                "</tr></thead><tbody>" + ''.join(body) + "</tbody></table></div></div>")

    return (f"<div class='sec'><span>三</span>套系盈亏榜（{name}，单位：万元）</div>"
            "<div class='grid2'>" + tbl(top, 'good') + tbl(neg, 'bad') + "</div>")


_NUM_CN = {5: '五', 6: '六', 7: '七', 8: '八', 9: '九', 10: '十'}


def _tof(t):
    t = str(t).replace('¥', '').replace(',', '').strip()
    try:
        return float(t)
    except Exception:
        return None


def _delta_cls(txt):
    """变化列单元格着色：+红 / -绿 / 0 灰。"""
    t = str(txt).strip()
    if t in ('—', '-', '', '无数据'):
        return 'na'
    v = _tof(t)
    if v is None:
        return ''
    if abs(v) < 1e-9:
        return 'fl'
    return 'up' if v > 0 else 'dn'


def _extra_html(extra):
    """渲染系统导出自带的分析块：[(标题, 表头, 行), ...]。

    自动做两件事：
    1. 变化列（同比/环比差异与百分比）按 +红/-绿 着色；
    2. 数据体检——人工成本均价为 0 时提示「工资未录入」。
    """
    if not extra:
        return []
    blocks, warn, no = [], '', 5
    for bi, (title, headers, rows) in enumerate(extra):
        # 特殊项：('__notice__', '提示文案', []) —— 渲染成提示卡，不占段号
        if title == '__notice__':
            blocks.append(_notice(headers))
            continue
        n = len(headers)
        th = ''.join("<th class='%s'>%s</th>" % ('l' if i == 0 else '', _e(h))
                     for i, h in enumerate(headers))
        body = []
        for r in rows:
            cells = [str(c) for c in list(r) + [''] * (n - len(r))][:n]
            first = cells[0].strip()
            tr_cls = " class='tot'" if ('📌' in first or first == '合计') else ''
            tds = []
            for i, c in enumerate(cells):
                c = c.replace('📌', '').strip()
                if i == 0:
                    tds.append(f"<td class='l'>{_e(c)}</td>")
                    continue
                dc = _delta_cls(c) if i >= 3 else ''   # 前 3 列内数值不上色
                if dc:
                    tds.append(f"<td class='dl {dc}'>{_e(c)}</td>")
                else:
                    tds.append(f"<td>{_e(c)}</td>")
            body.append(f"<tr{tr_cls}>" + ''.join(tds) + "</tr>")
            # ---- 数据体检
            if '均价' in title and first == '人工成本' and (_tof(cells[1]) or 0) == 0:
                warn = ('数据体检：本期「人工成本」均价为 0，疑似当月工资尚未录入——'
                        '此时利润会偏高、单均成本会偏低，请先在系统补录工资再对外汇报。')
            if '人工' in title and first == '合计' and (_tof(cells[1]) or 0) == 0:
                warn = ('数据体检：本期人工成本合计为 0，疑似当月工资尚未录入——'
                        '此时利润会偏高、单均成本会偏低，请先在系统补录工资再对外汇报。')
        clean = re.sub(r'^[^\w\u4e00-\u9fff（]+', '', title).strip()
        blocks.append(
            f"<div class='sec'><span>{_NUM_CN.get(no, no)}</span>{_e(clean)}</div>"
            "<div class='tb'><table><thead><tr>" + th + "</tr></thead><tbody>"
            + ''.join(body) + "</tbody></table></div>")
        no += 1
    if warn:
        blocks.insert(0, _notice('⚠ ' + warn))
    return blocks


def _tips(cur, prev, yoy, mm, idx, name, inc, prof, rate, span, pl, yl, has_prev, has_yoy,
          sec_no=5):
    tips = []
    tips.append(
        f"<b>盈利质量</b>：{name}下净利润 <b>{_w(prof)} 万元</b>，利润率 <b>{rate:.2f}%</b>，"
        f"单均利润 <b>{_y(cur['单均利润'])} 元</b>。"
        + ("给股东、财务汇报以<b>实际口径</b>为准（与支出对账一致）；"
           "给销售部做经营分析用<b>分摊口径</b>（看套系自身赚钱能力）。" if name == '实际口径'
           else "本口径按订单自行承担成本核算，适合评估套系定价与获客效率。"))

    if has_yoy and yoy:
        y = yoy
        di = inc - y['收入']
        dp = prof - y['净利润']
        tips.append(
            f"<b>同比（vs {yl}）</b>：收入 {'增长' if di >= 0 else '下滑'} "
            f"<b>{abs(di) / 10000:,.2f} 万元</b>（{di / y['收入'] * 100:+.1f}%），"
            f"净利润 {'增加' if dp >= 0 else '减少'} <b>{abs(dp) / 10000:,.2f} 万元</b>"
            f"（{(dp / abs(y['净利润']) * 100 if y['净利润'] else 0):+.1f}%）。"
            f"单均收入由 {_y(y['单均收入'])} 元变为 {_y(cur['单均收入'])} 元，"
            f"单均利润由 {_y(y['单均利润'])} 元变为 {_y(cur['单均利润'])} 元。")
    if has_prev and prev:
        di = inc - prev['收入']
        dp = prof - prev['利润']
        tips.append(
            f"<b>环比（vs {pl}）</b>：收入 {'增长' if di >= 0 else '下滑'} "
            f"<b>{abs(di) / 10000:,.2f} 万元</b>"
            f"（{(di / prev['收入'] * 100 if prev['收入'] else 0):+.1f}%），"
            f"净利润 {'增加' if dp >= 0 else '减少'} <b>{abs(dp) / 10000:,.2f} 万元</b>"
            f"（{(dp / abs(prev['利润']) * 100 if prev['利润'] else 0):+.1f}%）。")

    if len(mm):
        loss = int((mm[idx] < 0).sum())
        both = mm[(mm['pa'] < 0) & (mm['pb'] < 0)]
        sens = mm[(mm[idx] < 0) & (mm['pb' if idx == 'pa' else 'pa'] >= 0)]
        worst = mm.sort_values(idx).head(1)
        best = mm.sort_values(idx, ascending=False).head(1)
        wn = str(worst['套系'].iloc[0]) if len(worst) else '—'
        wa = float(worst[idx].iloc[0]) if len(worst) else 0.0
        bn = str(best['套系'].iloc[0]) if len(best) else '—'
        ba = float(best[idx].iloc[0]) if len(best) else 0.0
        tips.append(
            f"<b>亏损面</b>：{name}下 <b>{loss}</b> 个套系亏损，其中 <b>{len(both)}</b> 个两个口径都亏"
            f"（真实亏损，建议核对成本并评估是否继续售卖）；"
            f"<b>{len(sens)}</b> 个仅在本口径亏损（对人工与推广分摊敏感，可优先调价或控投放）。")
        if wa < 0:
            # 只有真亏损才点名「最需关注」；无亏损时不把利润最低的盈利套系拿出来示众
            # （例如只有 1 单但单均利润很高的套系，点它反而误导）
            tips.append(
                f"<b>最需关注</b>：<b>{_e(wn)}</b> 净亏 <b>{_w(abs(wa))} 万元</b>，为亏损最大套系；"
                f"<b>{_e(bn)}</b> 净赚 <b>{_w(ba)} 万元</b>，为利润最大贡献套系。")
        else:
            tips.append(
                f"<b>利润最大贡献</b>：<b>{_e(bn)}</b> 净赚 <b>{_w(ba)} 万元</b>，为利润最大贡献套系"
                f"（该{name.replace('口径', '')}下全部套系盈利，无需重点关注亏损）。")

    pro, sal = cur['推广'], cur['人工']
    tips.append(
        f"<b>成本结构</b>：推广费占收入 <b>{_p(pro, inc)}</b>、人工成本占收入 <b>{_p(sal, inc)}</b>，"
        f"两项合计 <b>{_p(pro + sal, inc)}</b>，是最大的两块成本，作为降本主攻方向；"
        f"单均成本 <b>{_y(cur['单均成本'])} 元</b>，"
        f"其中单均推广 <b>{_y(_div(pro, cur['单数']))} 元</b>、"
        f"单均人工 <b>{_y(_div(sal, cur['单数']))} 元</b>。")
    return ("<div class='sec'><span>%s</span>经营建议</div><div class='tips'>"
            % _NUM_CN.get(sec_no, sec_no)
            + ''.join(f"<div class='tip'>{t}</div>" for t in tips) + "</div>")


# ---------------------------------------------------------------- 对照视图
def _view_both(A, B, mm, span, biz):
    inc, sal_a, pro_a = A['收入'], A['人工'], A['推广']
    sal_b, pro_b = B['人工'], B['推广']
    dc_a, dc_b = A['直接小计'], B['直接小计']
    ic_a, ic_b = A['间接'], B['间接']
    prof_a, prof_b = A['利润'], B['利润']
    d_sal, d_pro = sal_a - sal_b, pro_a - pro_b
    tot_diff = prof_a - prof_b

    # KPI 双列
    def kpi_cmp(title, va, vb, fmt='w', sub=None):
        f = {'w': _w, 'y': _y, 'q': _q}[fmt]
        u = {'w': '万', 'y': '元', 'q': '单'}[fmt]
        return (f"<div class='kpi'><div class='k'>{_e(title)}</div>"
                f"<div class='v'>{f(va)}<span class='u'>{u}</span></div>"
                f"<div class='s'>{_e(sub) if sub else ('分摊口径 ' + f(vb))}</div></div>")

    kpis = ("<div class='kpis'>"
            + kpi_cmp('总收入', inc, B['收入'], sub='两口径完全相同')
            + kpi_cmp('净利润 · 实际口径', prof_a, prof_b,
                      sub=f"分摊口径 {_w(prof_b)} 万")
            + kpi_cmp('两口径利润差', tot_diff, 0.0, sub='实际 − 分摊（分摊更高）')
            + kpi_cmp('单均利润 · 实际口径', A['单均利润'], B['单均利润'], 'y',
                      sub=f"分摊口径 {_y(B['单均利润'])} 元")
            + "</div>")

    rows = []

    def r(label, va, vb, kind='w'):
        if kind == 'pt':
            rows.append(f"<tr><td class='l'>{label}</td><td>{va:.2f}%</td>"
                        f"<td>{vb:.2f}%</td><td>{(va - vb):+.2f}pt</td></tr>")
            return
        f = _w if kind == 'w' else _y
        d = va - vb
        if abs(d) < 0.005:
            d = 0.0
        rows.append(f"<tr><td class='l'>{label}</td><td>{f(va)}</td><td>{f(vb)}</td>"
                    f"<td class='{_cls(d)}'>{f(d)}</td></tr>")

    tbl = ("<div class='tb'><table><thead><tr>"
           "<th class='l'>项目（万元）</th><th>实际口径</th><th>分摊口径</th><th>差额</th>"
           "</tr></thead><tbody>")
    r('总收入', inc, B['收入'])
    rows.append("<tr class='dim'><td class='l' colspan='4'>减：成本</td></tr>")
    r('人工成本', sal_a, sal_b)
    r('推广费用', pro_a, pro_b)
    r('其他直接费用（13 项）', dc_a - sal_a - pro_a, dc_b - sal_b - pro_b)
    r('直接成本小计', dc_a, dc_b)
    r('间接成本（5 项）', ic_a, ic_b)
    r('成本合计', dc_a + ic_a, dc_b + ic_b)
    rows.append("<tr class='dim'><td class='l' colspan='4'>等于：利润</td></tr>")
    r('净利润', prof_a, prof_b)
    r('利润率', A['利润率'], B['利润率'], 'pt')
    r('单均收入（元）', A['单均收入'], B['单均收入'], 'y')
    r('单均成本（元）', A['单均成本'], B['单均成本'], 'y')
    r('单均利润（元）', A['单均利润'], B['单均利润'], 'y')
    tbl += ''.join(rows) + "</tbody></table></div>"

    sec1 = ("<div class='sec'><span>一</span>两口径利润对照</div>" + tbl
            + f"<div class='note'>两口径差额 <b>{_w(abs(tot_diff))} 万元</b>"
              f"（分摊口径更高）＝ 人工成本差 <b>{_w(d_sal)}</b> 万 + 推广费差 "
              f"<b>{_w(d_pro)}</b> 万，完全闭合；收入、其余 13 项直接费用、"
              "5 项间接费用两口径分文不差。</div>")

    mx = max(abs(d_sal), abs(d_pro), 1.0)

    def bar(v):
        wd = 0.0 if not mx else max(3.0, min(100.0, abs(v) / mx * 100))
        return f"<div class='bar'><i class='{_cls(v)}' style='width:{wd:.1f}%'></i></div>"

    sec2 = ("<div class='sec'><span>二</span>差异来源（唯一的两处口径差）</div>"
            "<div class='grid2'>"
            f"<div class='mini'><div class='mk'>人工成本</div>"
            f"<div class='mv'><span>实际 {_w(sal_a)} 万</span><em>vs</em>"
            f"<span>分摊 {_w(sal_b)} 万</span></div>"
            f"<div class='md'>差 <b>{_w(d_sal)}</b> 万元{bar(d_sal)}</div></div>"
            f"<div class='mini'><div class='mk'>推广费用</div>"
            f"<div class='mv'><span>实际 {_w(pro_a)} 万</span><em>vs</em>"
            f"<span>分摊 {_w(pro_b)} 万</span></div>"
            f"<div class='md'>差 <b>{_w(d_pro)}</b> 万元{bar(d_pro)}</div></div>"
            "</div>"
            "<div class='note'><b>实际口径</b>把当月人工与推广池按「<b>选片订单数</b>」分摊，"
            "两项合计与实际支出完全一致，适合与财务、股东对账；"
            "<b>分摊口径</b>按「<b>下单订单数</b>」分摊，分母更大，"
            "选片订单只承担自己那一份，适合销售部看套系自身赚钱能力。</div>")
    return kpis + sec1 + sec2


# ---------------------------------------------------------------- 页脚
def _foot(span, biz, gen, has_yoy, has_prev, pl, yl, yoy_src=None):
    src = yoy_src or '历史汇总数据'
    y = (f"同比基准 {_e(yl)}（{_e(src)}，仅提供收入 / 订单数 / 推广 / 人工 / 净利润，"
         "其余细分项同比显示「—」）；" if has_yoy else "未接入同比数据；")
    p = f"环比基准 {_e(pl)}；" if has_prev else "未接入环比数据；"
    return ("<div class='ft'>"
            "<b>口径说明</b>　实际口径＝人工成本与推广费按「拍摄选片订单数」分摊，两项合计与实际支出一致；"
            "分摊口径＝按「下单订单数」分摊，合计小于实际支出，只反映选片订单自身承担的部分。<br>"
            f"<b>基准说明</b>　{y}{p}箭头颜色仅表示数值升降（红＝上升，绿＝下降）。<br>"
            f"{_e(span)}　·　{_e(biz)}　·　生成时间 {_e(gen)}　·　"
            "由「旅拍利润系统」生成，纯展示层，不改动任何计算口径</div>")


# ---------------------------------------------------------------- 模板
_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>经营利润汇报 · 动态口径切换</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#eef1f7;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
color:#1f2d3d;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 56px}
.hd{position:relative;background:linear-gradient(135deg,#1e3a8a,#2f5bd0);border-radius:16px;padding:24px 28px;color:#fff;
box-shadow:0 6px 24px rgba(30,58,138,.22)}
.hd h1{margin:0;font-size:24px;letter-spacing:.5px}
.hd .sub{font-size:13.5px;opacity:.92;margin-top:7px;letter-spacing:2px}
.hd .meta{font-size:12.5px;opacity:.86;margin-top:12px;padding-top:12px;border-top:1px solid rgba(255,255,255,.22)}
.sw{position:absolute;right:24px;top:24px;display:flex;background:rgba(255,255,255,.16);
border-radius:11px;padding:4px;gap:3px;backdrop-filter:blur(6px)}
.sw-b{border:0;background:transparent;color:#e8eeff;font-size:13px;padding:7px 15px;border-radius:8px;
cursor:pointer;font-family:inherit;transition:.16s;white-space:nowrap}
.sw-b:hover{background:rgba(255,255,255,.16)}
.sw-b.on{background:#fff;color:#1e3a8a;font-weight:700;box-shadow:0 2px 8px rgba(0,0,0,.16)}
body[data-m="a"] .v-b,body[data-m="a"] .v-both,
body[data-m="b"] .v-a,body[data-m="b"] .v-both,
body[data-m="both"] .v-a,body[data-m="both"] .v-b{display:none}
/* —— 分业务线：每段一张卡，卡内再按口径切换 —— */
.seg{background:#fff;border-radius:14px;padding:18px 20px 22px;margin:16px 0;
box-shadow:0 2px 12px rgba(15,34,66,.07);border:1px solid #e3e9f2}
.seg-hd{font-size:17px;font-weight:700;color:#1e3a8a;padding:2px 0 12px 12px;margin-bottom:4px;
border-left:4px solid #2f5bd0;letter-spacing:.5px;scroll-margin-top:14px}
.seg .hd{position:static;margin-bottom:6px}
.rt-bar{position:fixed;right:26px;bottom:24px;background:rgba(30,58,138,.92);border-radius:11px;
padding:6px;display:flex;gap:4px;box-shadow:0 6px 20px rgba(30,58,138,.28);z-index:99}
.rt-bar a{color:#e8eeff;text-decoration:none;font-size:12.5px;padding:6px 12px;border-radius:8px}
.rt-bar a:hover{background:rgba(255,255,255,.18)}
.seg .wrap{padding:0;max-width:none}
/* —— 利润表折叠明细行 —— */
tr.xrow{display:none}
tr.xrow.open{display:table-row}
tr.xrow td{background:#f7f9fc;font-size:12.5px;color:#5c6f85}
td.l.ind2{padding-left:38px}
td.togg{cursor:pointer;user-select:none}
td.togg:hover{color:#2f5bd0}
.tw{display:inline-block;width:11px;color:#8296a8;font-size:11px}
.sum{background:#fff;border-radius:14px;padding:18px 22px;margin-top:16px;border-left:4px solid #2f5bd0;
box-shadow:0 1px 4px rgba(16,42,67,.08)}
.sum-t{font-size:13px;font-weight:700;color:#2f5bd0;letter-spacing:1px;margin-bottom:8px}
.sum-l{margin:0;padding-left:20px;font-size:13.5px;line-height:2.05;color:#33455c}
.sum-l b{color:#12233d}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0 4px}
.kpi{background:#fff;border-radius:13px;padding:15px 17px;box-shadow:0 1px 4px rgba(16,42,67,.08)}
.kpi .k{font-size:12.5px;color:#8296a8}
.kpi .v{font-size:23px;font-weight:700;margin-top:7px;letter-spacing:-.3px}
.kpi .v .u{font-size:12.5px;font-weight:500;margin-left:3px;color:#8a9aaa}
.kpi .s{font-size:11.5px;color:#9aa9b8;margin-top:5px;padding-bottom:9px;border-bottom:1px dashed #eef2f8}
.kpi-d{display:flex;justify-content:space-between;align-items:center;font-size:11.5px;margin-top:7px}
.kdl{color:#9aa9b8}
.kdd{font-weight:700;font-size:11.5px}
.kdd.up{color:#c0392b}.kdd.dn{color:#059669}.kdd.fl{color:#8a9aaa}.kdd.na{color:#c2ccd8}
.sec{font-size:15.5px;font-weight:700;margin:28px 0 12px;display:flex;align-items:center;color:#12233d}
.sec span{display:inline-flex;width:22px;height:22px;border-radius:6px;background:#2f5bd0;color:#fff;
font-size:12.5px;align-items:center;justify-content:center;margin-right:9px;font-weight:600}
.hint2{font-size:12px;color:#8a9aaa;margin:-4px 0 10px;line-height:1.8}
.tb{background:#fff;border-radius:13px;overflow:hidden;box-shadow:0 1px 4px rgba(16,42,67,.08)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f6f8fc;color:#5b6b7c;font-weight:600;padding:10px 11px;text-align:right;
border-bottom:1px solid #e6ecf4;white-space:nowrap;font-size:12.5px}
th.l,td.l{text-align:left}
td{padding:9px 11px;text-align:right;border-bottom:1px solid #f2f5f9;white-space:nowrap}
td.c{text-align:center}
td.ind{padding-left:24px;color:#5b6b7c}
td.pc{color:#8296a8}
td.base{color:#8296a8;background:#fcfdff}
td.src{text-align:left;white-space:normal;font-size:11.5px;line-height:1.5;
color:#7b8a9c;background:#fbfcfe;max-width:300px}
td.na{color:#c2ccd8}
tr:last-child td{border-bottom:0}
tr.tot td{background:#fafbfd;font-weight:700;border-top:1px solid #e6ecf4}
tr.sub td{background:#fbfcfe;font-weight:600}
tr.key td{background:#f7f9fe;font-weight:700;border-top:1px solid #e6ecf4;border-bottom:1px solid #e6ecf4}
tr.dim td{background:#fbfcfe;color:#8a9aaa;font-size:12px;padding:6px 11px;font-weight:600}
td.empty{text-align:center;color:#9aa9b8;padding:16px}
.rk{color:#8a9aaa;font-size:12px}
.pos{color:#c0392b}.neg{color:#059669}
.up{color:#c0392b}.dn{color:#059669}.fl{color:#8a9aaa}
.dl{font-weight:600}
.un{font-weight:400;color:#9aa9b8;font-size:11.5px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.mini{background:#fff;border-radius:13px;padding:16px 18px;box-shadow:0 1px 4px rgba(16,42,67,.08)}
.mini .mk{font-size:13px;font-weight:700;color:#33455c}
.mini .mv{font-size:13px;color:#5b6b7c;margin:9px 0 0;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mini .mv em{font-style:normal;color:#c2ccd8;font-size:12px}
.mini .md{font-size:12.5px;color:#5b6b7c;margin-top:9px}
.mini .md b{color:#12233d;font-size:14px}
.bar{height:7px;background:#f0f3f8;border-radius:4px;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:4px}
.bar i.pos{background:#e05c4b}.bar i.neg{background:#3aa87c}
.note{background:#eef3ff;border:1px solid #c9d8f7;border-radius:12px;padding:13px 18px;font-size:13px;
line-height:1.95;color:#33455c;margin-top:12px}
.note b{color:#12233d}
.rank .rt{font-size:13px;font-weight:700;margin-bottom:9px;color:#33455c}
.rank.bad .rt{color:#b03a2e}
.rank.good .rt{color:#1e7a52}
.tg{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:5px;line-height:1.7}
.tg-d{background:#fdecea;color:#c0392b;border:1px solid #f5c6c0}
.tg-i{background:#fef5e7;color:#b9770e;border:1px solid #f7dcae}
.tips{background:#fff;border-radius:13px;padding:8px 20px;box-shadow:0 1px 4px rgba(16,42,67,.08)}
.tip{font-size:13.5px;line-height:2.05;color:#33455c;padding:11px 0;border-bottom:1px dashed #eef2f8}
.tip:last-child{border-bottom:0}
.tip b{color:#12233d}
.ft{text-align:center;font-size:12px;color:#93a3b5;margin-top:28px;line-height:1.95}
@media (max-width:900px){
.kpis{grid-template-columns:repeat(2,1fr)}
.grid2{grid-template-columns:1fr}
.wrap{padding:16px 12px 40px}
.hd{padding:20px}
.sw{position:static;margin-top:14px;display:inline-flex}
.tb{overflow-x:auto}
}
@media print{
body{background:#fff}
.wrap{max-width:none;padding:0}
.v{display:block!important}
.sw{display:none}
.kpi,.tb,.mini,.tips,.sum{box-shadow:none;border:1px solid #e2e8f0}
.sec{break-after:avoid}
table,.mini,.rank{break-inside:avoid}
}
</style></head><body data-m="a"><div class="wrap">/*__BODY__*/</div>
<script>
function _xt(el){
  var tb = el.closest('tbody');
  if(!tb) return;
  var g = el.getAttribute('data-g');
  var rows = tb.querySelectorAll("tr.xrow[data-g='"+g+"']");
  var open = !(rows[0] && rows[0].classList.contains('open'));
  for(var i=0;i<rows.length;i++) rows[i].classList.toggle('open', open);
  var t = el.querySelector('.tw');
  if(t) t.textContent = open ? '▾' : '▸';
}
(function(){
  // 切换以「整个页面」为单位（分业务线时多段报表一起切），并同步所有段里的按钮高亮。
  var btns=document.querySelectorAll('.sw-b');
  function apply(m){
    document.body.setAttribute('data-m',m);
    for(var j=0;j<btns.length;j++){
      if(btns[j].getAttribute('data-m')===m) btns[j].classList.add('on');
      else btns[j].classList.remove('on');
    }
  }
  for(var i=0;i<btns.length;i++){
    btns[i].addEventListener('click',function(){
      apply(this.getAttribute('data-m'));
      var bar=document.getElementById('rtBar');
      if(bar) window.scrollTo({top:0,behavior:'smooth'});
    });
  }
})();
</script>
</body></html>"""
