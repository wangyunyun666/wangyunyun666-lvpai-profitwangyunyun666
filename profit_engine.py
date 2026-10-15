# profit_engine.py
import pandas as pd
from datetime import datetime, timedelta
from database import SessionLocal, Order, SetStandardCost, ActualDirectCost, CherryProductCost, IndirectCost, AllocationRule, MonthlyStats
from sqlalchemy import func
from streamlit import cache_data

# ==================== 内部单剔除 ====================
# 「一纸情书」系列为公司内部单，不属于真实经营业务，不参与任何利润计算。
# 之所以用「套系名关键字」而不是「type=='nan'」来判定，是因为 type 列存成了字符串 "nan"
# 只是当初导入时类型列留空、被 str(row['类型']) 转义的结果，它并非这批内部单的稳定标识；
# 而套系名「一纸情书」才是业务上可靠的唯一标识。同时这批订单的存在会导致业务线筛选出现 "nan" 选项。
EXCLUDE_SET_KEYWORDS = ('一纸情书',)


def is_excluded_order(set_name):
    """判断订单是否为内部单（不参与利润计算）。

    Args:
        set_name: 订单的套系名称，可能为 None。

    Returns:
        bool: 命中任一内部单关键字则返回 True。
    """
    if not set_name:
        return False
    return any(kw in str(set_name) for kw in EXCLUDE_SET_KEYWORDS)


def _valid_biz_type(t):
    """type 是否为有效业务类型（旅拍/婚礼）。

    Args:
        t: 订单的 type（业务类型）字段，可能为 None 或脏字符串 'nan'。

    Returns:
        bool: 仅当去空格后严格等于 '旅拍' 或 '婚礼' 时返回 True。
    """
    s = str(t or '').strip()
    return s in ('旅拍', '婚礼')


def infer_type_from_set(set_name):
    """按套系名关键字推断业务类型；推断不出返回 None。

    Args:
        set_name: 订单的套系名称，可能为 None。

    Returns:
        str | None: 命中「婚礼」返回 '婚礼'，命中「旅拍」返回 '旅拍'，否则 None。
    """
    s = str(set_name or '')
    if '婚礼' in s:
        return '婚礼'
    if '旅拍' in s:
        return '旅拍'
    return None


@cache_data(ttl=600, show_spinner="正在生成利润报表…")
def generate_profit_report(period_start, period_end, promo_mode='actual'):
    db = SessionLocal()
    try:
        start_date = datetime.strptime(period_start, '%Y-%m-%d').date()
        end_date = datetime.strptime(period_end, '%Y-%m-%d').date()

        # ========== 1. 订单数据 ==========
        orders = db.query(Order).filter(
            func.date(Order.selection_date) >= start_date,
            func.date(Order.selection_date) <= end_date
        ).all()
        # 先剔除「一纸情书」内部单。
        # 注意：旧的 str(o.type).strip() != '' 对字符串 "nan" 是成立的（"nan" 不是空串），
        # 因此单单靠 type 判空无法拦下这类脏数据，必须靠套系名关键字剔除内部单。
        orders = [o for o in orders if not is_excluded_order(o.set_name)]
        # 历史脏数据归一：type 缺失 / 为 'nan' 字符串的订单，按套系名推断业务类型。
        # 这里只就地修正内存中的 SQLAlchemy ORM 对象属性（_o.type = _it），engine 内全程无 commit，
        # 因此绝不会写库，仅影响本次报表口径。推断不出的订单无法归入任何业务线，予以排除，
        # 否则会以 nan 业务类型混入利润表。
        _fixed = []
        for _o in orders:
            if not _valid_biz_type(_o.type):
                _it = infer_type_from_set(_o.set_name)
                if _it:
                    _o.type = _it
                else:
                    continue
            _fixed.append(_o)
        orders = _fixed
        order_ids = [o.order_id for o in orders]
        if not orders:
            return pd.DataFrame()

        orders_df = pd.DataFrame([{
            'order_id': o.order_id, 'type': o.type, 'set_name': o.set_name,
            'set_price': o.set_price, 'second_sales': o.second_sales,
            'refund': o.refund, 'selected_photos': o.selected_photos or 0,
            'extra_photos': o.extra_photos or 0, 'photo_count': o.photo_count or 0,
            'actual_retouch_fee': o.actual_retouch_fee or 0, 'selection_date': o.selection_date
        } for o in orders])

        # ========== 2. 标准成本、实际成本、樱桃云成本 ==========
        std_all = db.query(SetStandardCost).all()
        std_pivot = pd.DataFrame()
        if std_all:
            std_df = pd.DataFrame([{'set_name': s.set_name, 'cost_item': s.cost_item, 'amount': s.amount} for s in std_all])
            std_pivot = std_df.pivot(index='set_name', columns='cost_item', values='amount').fillna(0)

        actual_all = db.query(ActualDirectCost).filter(
            ActualDirectCost.order_id.in_(order_ids)
        ).all()
        actual_pivot = pd.DataFrame()
        actual_existing = set()
        if actual_all:
            actual_df = pd.DataFrame([{'order_id': a.order_id, 'cost_item': a.cost_item, 'amount': a.amount} for a in actual_all])
            actual_pivot = actual_df.pivot_table(index='order_id', columns='cost_item', values='amount', aggfunc='sum').fillna(0)
            actual_existing = {(a.order_id, a.cost_item) for a in actual_all}

        latest_batch = db.query(
            CherryProductCost.batch_id,
            func.max(CherryProductCost.import_time).label('max_time')
        ).group_by(CherryProductCost.batch_id).order_by(func.max(CherryProductCost.import_time).desc()).first()
        cherry_costs = {}
        if latest_batch:
            costs = db.query(
                CherryProductCost.order_id,
                func.sum(CherryProductCost.product_total_cost).label('total_cost')
            ).filter(
                CherryProductCost.batch_id == latest_batch.batch_id,
                CherryProductCost.order_id.in_(order_ids)
            ).group_by(CherryProductCost.order_id).all()
            for c in costs:
                cherry_costs[c.order_id] = c.total_cost or 0.0

        # ========== 3. 汇总所选时间段内所有月份的间接费用 ==========
        period_months = []
        current = start_date.replace(day=1)
        while current <= end_date:
            period_months.append(current.strftime('%Y-%m'))
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        rows = db.query(
            IndirectCost.cost_item, IndirectCost.business_type,
            func.coalesce(IndirectCost.amount, 0.0).label('amount')
        ).filter(IndirectCost.period.in_(period_months)).all()

        travel_indirect = {}
        wedding_indirect = {}
        xinjiang_shoot_total = 0.0
        travel_promo = 0.0
        wedding_promo = 0.0

        salary_items = ['策划部工资','销售部工资','运营部工资','综合部工资','总经办工资','售后服务工资','AI与数据中心工资','视频剪辑部工资']
        indirect_items = ['房租、水电、办公费等','税费及手续费','样片研发','场地铺设费','舆情处理']

        for inv in rows:
            amt = float(inv.amount)
            if inv.cost_item == '推广费':
                if inv.business_type == '旅拍': travel_promo += amt
                elif inv.business_type == '婚礼': wedding_promo += amt
            else:
                if inv.business_type == '旅拍':
                    travel_indirect[inv.cost_item] = travel_indirect.get(inv.cost_item, 0.0) + amt
                elif inv.business_type == '婚礼':
                    wedding_indirect[inv.cost_item] = wedding_indirect.get(inv.cost_item, 0.0) + amt
                elif inv.business_type == '新疆' and inv.cost_item == '新疆拍样费用':
                    xinjiang_shoot_total += amt

        # ========== 4. 订单数量统计 ==========
        travel_qty = 0.0
        wedding_qty = 0.0
        xinjiang_qty = 0.0
        travel_nonxj_qty = 0.0
        wedding_nonxj_qty = 0.0
        xinjiang_travel_qty = 0.0
        xinjiang_wedding_qty = 0.0
        for (biz_type, set_name), grp in orders_df.groupby(['type', 'set_name']):
            qty = len(grp)
            if '新疆' in set_name:
                xinjiang_qty += qty
                if biz_type == '旅拍':
                    xinjiang_travel_qty += qty
                elif biz_type == '婚礼':
                    xinjiang_wedding_qty += qty
            else:
                if biz_type == '旅拍':
                    travel_nonxj_qty += qty
                elif biz_type == '婚礼':
                    wedding_nonxj_qty += qty
            if biz_type == '旅拍':
                travel_qty += qty
            elif biz_type == '婚礼':
                wedding_qty += qty

        # 下单订单数
        travel_order_cnt = 0.0
        wedding_order_cnt = 0.0
        xinjiang_order_count = 0.0
        for stat in db.query(MonthlyStats).filter(MonthlyStats.period.in_(period_months)).all():
            if stat.business_type == '旅拍':
                travel_order_cnt += float(stat.order_count or 0)
            elif stat.business_type == '婚礼':
                wedding_order_cnt += float(stat.order_count or 0)
            elif stat.business_type == '新疆':
                xinjiang_order_count += float(stat.order_count or 0)

        def safe_div(a, b):
            return a / b if b > 0 else 0.0

        # ========== 人工成本均价 ==========
        if promo_mode == 'allocation':
            # 分摊口径：工资总额 ÷ 下单订单数（作为每单均价），再 × 选片订单数
            travel_avg_salary = {item: safe_div(travel_indirect.get(item, 0.0), travel_order_cnt) for item in salary_items}
            wedding_avg_salary = {item: safe_div(wedding_indirect.get(item, 0.0), wedding_order_cnt) for item in salary_items}
        else:
            # 实际口径：工资总额 ÷ 选片订单数
            travel_avg_salary = {item: safe_div(travel_indirect.get(item, 0.0), travel_qty) for item in salary_items}
            wedding_avg_salary = {item: safe_div(wedding_indirect.get(item, 0.0), wedding_qty) for item in salary_items}

        # ========== 推广费 ==========
        if promo_mode == 'allocation':
            # 分摊口径：新疆 2450 × 选片数；旅拍/婚礼各自下单均价 × 选片数
            travel_avg_promo = safe_div(travel_promo, travel_order_cnt)
            wedding_avg_promo = safe_div(wedding_promo, wedding_order_cnt)
            xinjiang_promo_unit = 2450.0
        else:
            # 实际口径：新疆固定 2450 × 下单订单数，非新疆倒挤
            xinjiang_promo_total = 2450.0 * xinjiang_order_count
            if xinjiang_qty > 0:
                xinjiang_travel_order = xinjiang_order_count * (xinjiang_travel_qty / xinjiang_qty)
                xinjiang_wedding_order = xinjiang_order_count * (xinjiang_wedding_qty / xinjiang_qty)
            else:
                xinjiang_travel_order = 0.0
                xinjiang_wedding_order = 0.0

            travel_promo_nonxj = max(0.0, travel_promo - 2450.0 * xinjiang_travel_order)
            wedding_promo_nonxj = max(0.0, wedding_promo - 2450.0 * xinjiang_wedding_order)
            travel_avg_promo = safe_div(travel_promo_nonxj, travel_nonxj_qty)
            wedding_avg_promo = safe_div(wedding_promo_nonxj, wedding_nonxj_qty)
            xinjiang_promo_unit = safe_div(xinjiang_promo_total, xinjiang_qty)

        # 其他间接费用均价（两种口径一致，按选片订单数）
        travel_avg_indirect = {item: safe_div(travel_indirect.get(item, 0.0), travel_qty) for item in indirect_items}
        wedding_avg_indirect = {item: safe_div(wedding_indirect.get(item, 0.0), wedding_qty) for item in indirect_items}

        # 样片研发（两种口径一致）
        travel_rd_nonxj = travel_indirect.get('样片研发', 0.0)
        wedding_rd_nonxj = wedding_indirect.get('样片研发', 0.0)
        travel_avg_rd_nonxj = safe_div(travel_rd_nonxj, travel_nonxj_qty)
        wedding_avg_rd_nonxj = safe_div(wedding_rd_nonxj, wedding_nonxj_qty)
        xinjiang_avg_rd = safe_div(xinjiang_shoot_total, xinjiang_qty)

        # ========== 5. 订单级成本计算 ==========
        def get_std_val(std_series, *names, default=0.0):
            for name in names:
                if name in std_series.index: return float(std_series[name])
            return default

        def new_retouch_price(row):
            biz, sp, ss = row['type'], row['set_price'], row['second_sales']
            if ss > 6000:
                return 15.0
            if biz == '旅拍':
                if sp >= 10980 and ss == 0: return 10.0
                elif ss < 3000: return 9.0
                else: return 11.0
            elif biz == '婚礼':
                if sp >= 16980 and ss == 0: return 11.0
                elif ss < 3000: return 10.0
                else: return 12.0
            return 9.0

        pure_actual = ['搭建', '主持', '场地', '微电影拍摄费用', '拍摄费用', '鲜花费用']

        for idx, row in orders_df.iterrows():
            oid, set_name = row['order_id'], row['set_name']
            std = std_pivot.loc[set_name] if set_name in std_pivot.index else pd.Series(dtype=float)
            act = actual_pivot.loc[oid] if oid in actual_pivot.index else pd.Series(dtype=float)

            pc = row['photo_count']; sd = row['selection_date']
            orders_df.at[idx, '像素蛋糕修图费'] = pc * 0.55 * 0.08 if (sd and pd.Timestamp(sd) >= pd.Timestamp('2026-04-01')) else pc * 0.55 * 0.1

            sel, ext = row['selected_photos'], row['extra_photos']
            base = sel - ext; total_p = base + ext if base >= 0 else ext
            ar = row['actual_retouch_fee']
            if ar > 0 and total_p > 0:
                rf = ar * (base / total_p); rs = ar - rf
            else:
                unit = new_retouch_price(row) if (sd and pd.Timestamp(sd) >= pd.Timestamp('2026-04-01')) else (9.0 if row['type']=='旅拍' else 10.0)
                rf = base * unit; rs = ext * unit
            orders_df.at[idx, 'retouch_first_sales'] = rf; orders_df.at[idx, 'retouch_second_sales'] = rs

            fs = get_std_val(std, '工厂费用', '工厂成本')
            ct = cherry_costs.get(oid, 0.0) or 0.0
            if (oid, '工厂费用') in actual_existing:
                af = act.get('工厂费用', 0.0); tf = af if af > 0 else ct
            else:
                tf = ct
            if tf == 0.0: tf = fs
            orders_df.at[idx, 'factory_first'] = min(tf, fs); orders_df.at[idx, 'factory_second'] = tf - min(tf, fs)

            cs = get_std_val(std, '微电影剪辑费用', '微电影剪辑费')
            if (oid, '微电影剪辑费用') in actual_existing:
                # 有实际记录就用实际值（哪怕为0），保证绿色=实际
                orders_df.at[idx, '微电影剪辑费用'] = act.get('微电影剪辑费用', 0.0)
            else:
                # 无实际记录时才回退到标准成本
                orders_df.at[idx, '微电影剪辑费用'] = cs

            for f in pure_actual:
                orders_df.at[idx, f] = act.get(f, 0.0) if f in act.index else 0.0

            default_fee = row['second_sales'] * 0.45
            act_fee = act.get('二销选片费', None)
            if act_fee is not None and act_fee > 0:
                orders_df.at[idx, '二销选片费'] = act_fee
            else:
                orders_df.at[idx, '二销选片费'] = default_fee

        # ========== 6. 按套系汇总 ==========
        direct_items_map = {
            '微电影拍摄费用': '微电影拍摄费用', '微电影剪辑费用': '微电影剪辑费用',
            '交付费用（主持）': '主持', '交付费用（场地）': '场地', '交付费用（搭建）': '搭建',
            '鲜花费用': '鲜花费用', '拍摄费用': '拍摄费用', '像素蛋糕修图费': '像素蛋糕修图费',
            '后期修片费(一销)': 'retouch_first_sales', '后期修片费(二销)': 'retouch_second_sales',
            '工厂费用（一销）': 'factory_first', '工厂费用（二销）': 'factory_second',
            '二销选片费': '二销选片费',
        }

        report_rows = []
        for (biz_type, set_name), grp in orders_df.groupby(['type', 'set_name']):
            qty = len(grp)
            row_dict = {
                '业务类型': biz_type, '套系': set_name,
                '套系单价': float(grp['set_price'].iloc[0]), '套系数量': qty,
                '套系金额': float(grp['set_price'].sum()),
                '套系均价': float(grp['set_price'].sum() / qty) if qty else 0.0,
                '二销金额': float(grp['second_sales'].sum()),
                '二销均价': float(grp['second_sales'].sum() / qty) if qty else 0.0,
                '客诉退款金额': float(grp['refund'].sum()),
                '总收入': float(grp['set_price'].sum() + grp['second_sales'].sum() - grp['refund'].sum()),
            }
            total_direct = 0.0
            for chinese, eng in direct_items_map.items():
                val = float(grp[eng].sum()) if eng in grp.columns else 0.0
                row_dict[chinese] = val
                total_direct += val

            already_included = set(direct_items_map.values()) | {'像素蛋糕修图费', '工厂费用', '新疆拍摄费用'}
            for item in actual_pivot.columns:
                if item in already_included: continue
                val = 0.0
                for oid in grp['order_id']:
                    if oid in actual_pivot.index and item in actual_pivot.columns:
                        val += float(actual_pivot.at[oid, item])
                if val != 0:
                    row_dict[item] = val
                    total_direct += val

            is_xinjiang = '新疆' in set_name

            if is_xinjiang:
                if biz_type == '旅拍':
                    for item in salary_items:
                        share = travel_avg_salary[item] * qty
                        row_dict[item] = share
                        total_direct += share
                    row_dict['人工成本'] = sum(row_dict[item] for item in salary_items)
                    row_dict['推广费用（实际）'] = xinjiang_promo_unit * qty
                    total_direct += row_dict['推广费用（实际）']
                    total_indirect = 0.0
                    for item in indirect_items:
                        if item == '样片研发':
                            share = xinjiang_avg_rd * qty
                        else:
                            share = travel_avg_indirect.get(item, 0.0) * qty
                        row_dict[item] = share
                        total_indirect += share
                    row_dict['总间接成本'] = total_indirect
                else:
                    for item in salary_items:
                        share = wedding_avg_salary[item] * qty
                        row_dict[item] = share
                        total_direct += share
                    row_dict['人工成本'] = sum(row_dict[item] for item in salary_items)
                    row_dict['推广费用（实际）'] = xinjiang_promo_unit * qty
                    total_direct += row_dict['推广费用（实际）']
                    total_indirect = 0.0
                    for item in indirect_items:
                        if item == '样片研发':
                            share = xinjiang_avg_rd * qty
                        else:
                            share = wedding_avg_indirect.get(item, 0.0) * qty
                        row_dict[item] = share
                        total_indirect += share
                    row_dict['总间接成本'] = total_indirect
            else:
                if biz_type == '旅拍':
                    for item in salary_items:
                        share = travel_avg_salary[item] * qty  # 两种口径统一
                        row_dict[item] = share
                        total_direct += share
                    row_dict['人工成本'] = sum(row_dict[item] for item in salary_items)
                    row_dict['推广费用（实际）'] = travel_avg_promo * qty
                    total_direct += row_dict['推广费用（实际）']
                    total_indirect = 0.0
                    for item in indirect_items:
                        if item == '样片研发':
                            share = travel_avg_rd_nonxj * qty
                        else:
                            share = travel_indirect.get(item, 0.0) * (qty / travel_qty) if travel_qty > 0 else 0.0
                        row_dict[item] = share
                        total_indirect += share
                    row_dict['总间接成本'] = total_indirect
                elif biz_type == '婚礼':
                    for item in salary_items:
                        share = wedding_avg_salary[item] * qty  # 两种口径统一
                        row_dict[item] = share
                        total_direct += share
                    row_dict['人工成本'] = sum(row_dict[item] for item in salary_items)
                    row_dict['推广费用（实际）'] = wedding_avg_promo * qty
                    total_direct += row_dict['推广费用（实际）']
                    total_indirect = 0.0
                    for item in indirect_items:
                        if item == '样片研发':
                            share = wedding_avg_rd_nonxj * qty
                        else:
                            share = wedding_indirect.get(item, 0.0) * (qty / wedding_qty) if wedding_qty > 0 else 0.0
                        row_dict[item] = share
                        total_indirect += share
                    row_dict['总间接成本'] = total_indirect
                else:
                    row_dict['人工成本'] = 0.0
                    row_dict['推广费用（实际）'] = 0.0
                    total_indirect = 0.0
                    for item in indirect_items: row_dict[item] = 0.0
                    row_dict['总间接成本'] = 0.0

            row_dict['总直接成本'] = total_direct
            report_rows.append(row_dict)

        report_df = pd.DataFrame(report_rows)
        if report_df.empty:
            return report_df

        report_df['业务类型'] = report_df['业务类型'].str.strip()
        report_df['利润合计'] = report_df['总收入'] - report_df['总直接成本'] - report_df['总间接成本']
        report_df['利润率'] = report_df.apply(lambda r: r['利润合计'] / r['总收入'] if r['总收入'] else 0.0, axis=1)

        base_cols = ['业务类型', '套系', '套系单价', '套系数量', '套系金额', '套系均价',
                     '二销金额', '二销均价', '客诉退款金额', '总收入']
        desired_cost_order = [
            '推广费用（实际）',
            '交付费用（主持）',
            '交付费用（场地）',
            '交付费用（搭建）',
            '鲜花费用',
            '微电影拍摄费用',
            '拍摄费用',
            '二销选片费',
            '像素蛋糕修图费',
            '微电影剪辑费用',
            '后期修片费(一销)',
            '后期修片费(二销)',
            '工厂费用（一销）',
            '工厂费用（二销）',
            '人工成本',
            '房租、水电、办公费等',
            '税费及手续费',
            '样片研发',
            '场地铺设费',
            '舆情处理',
            '总直接成本',
            '总间接成本',
            '利润合计',
            '利润率'
        ]
        existing_direct = [col for col in report_df.columns if col not in base_cols + desired_cost_order + salary_items]
        final_order = base_cols + desired_cost_order + existing_direct + salary_items
        seen = set()
        final_order = [x for x in final_order if not (x in seen or seen.add(x))]
        final_order = [c for c in final_order if c in report_df.columns]
        report_df = report_df[final_order]

        return report_df
    finally:
        db.close()


@cache_data(ttl=600, show_spinner="正在生成利润报表…")
def generate_profit_report_multi_month(*period_months, promo_mode='actual'):
    """
    按月逐个生成利润报告，然后将各月结果按套系合并，并重新按总期间统一分摊人工成本。
    """
    all_dfs = []
    for month_str in period_months:
        y, m = map(int, month_str.split('-'))
        start_date = datetime(y, m, 1)
        if m == 12:
            end_date = datetime(y + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = datetime(y, m + 1, 1) - timedelta(days=1)
        df = generate_profit_report(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'), promo_mode)
        if not df.empty:
            all_dfs.append(df)
    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    group_cols = ['业务类型', '套系']
    agg_dict = {}
    for col in combined.columns:
        if col in group_cols:
            continue
        if pd.api.types.is_numeric_dtype(combined[col]):
            agg_dict[col] = 'sum'
        else:
            agg_dict[col] = 'first'
    result = combined.groupby(group_cols, as_index=False).agg(agg_dict)

    if '套系金额' in result.columns and '套系数量' in result.columns:
        result['套系均价'] = result['套系金额'] / result['套系数量']
    if '二销金额' in result.columns and '套系数量' in result.columns:
        result['二销均价'] = result['二销金额'] / result['套系数量']
    if '总收入' in result.columns and '总直接成本' in result.columns and '总间接成本' in result.columns:
        result['利润合计'] = result['总收入'] - result['总直接成本'] - result['总间接成本']
        result['利润率'] = result.apply(lambda r: r['利润合计'] / r['总收入'] if r['总收入'] else 0, axis=1)

    # ========== 重新按总期间统一分摊人工成本 ==========
    db = SessionLocal()
    try:
        salary_items = ['策划部工资','销售部工资','运营部工资','综合部工资','总经办工资','售后服务工资','AI与数据中心工资','视频剪辑部工资']

        travel_salary = {item: 0.0 for item in salary_items}
        wedding_salary = {item: 0.0 for item in salary_items}
        rows = db.query(IndirectCost.cost_item, IndirectCost.business_type, func.sum(IndirectCost.amount)).filter(
            IndirectCost.period.in_(period_months),
            IndirectCost.cost_item.in_(salary_items),
            IndirectCost.business_type.in_(['旅拍', '婚礼'])
        ).group_by(IndirectCost.cost_item, IndirectCost.business_type).all()
        for cost_item, biz, total in rows:
            if biz == '旅拍':
                travel_salary[cost_item] = float(total or 0)
            elif biz == '婚礼':
                wedding_salary[cost_item] = float(total or 0)

        start_date = datetime.strptime(period_months[0] + '-01', '%Y-%m-%d').date()
        last_month = period_months[-1]
        y, m = map(int, last_month.split('-'))
        if m == 12:
            end_date = datetime(y + 1, 1, 1).date() - timedelta(days=1)
        else:
            end_date = datetime(y, m + 1, 1).date() - timedelta(days=1)

        # 人工成本分摊的除数（选片订单数）。这里必须排除「一纸情书」内部单，否则基数偏大、人工成本被摊薄。
        # 为什么把过滤放到 Python 层而不是 SQL：一是 SQL 的 NOT LIKE 对 set_name 为 NULL 的行会返回
        # 未知值从而把正常订单也误删，二是 LIKE 关键字在不同方言下的转义行为有差异；取回后用
        # is_excluded_order 判定，SQLite（本地）与 PostgreSQL（Neon 生产）行为完全一致。
        _qty_rows = db.query(Order.type, Order.set_name).filter(
            Order.selection_date >= start_date,
            Order.selection_date <= end_date,
            Order.type.isnot(None),
            func.trim(Order.type) != ''
        ).all()
        # 与单月报表口径保持一致：type 无效（缺失/为 'nan'）的订单按套系名推断业务类型后再计数；
        # 推断不出业务类型的订单跳过（不计入 any 业务线除数），避免污染分摊。
        travel_qty = 0
        wedding_qty = 0
        for _t, _s in _qty_rows:
            if is_excluded_order(_s):
                continue
            _bt = _t if _valid_biz_type(_t) else infer_type_from_set(_s)
            if _bt == '旅拍':
                travel_qty += 1
            elif _bt == '婚礼':
                wedding_qty += 1
            # 推断仍无结果的订单：无业务线归属，跳过

        travel_order_cnt = 0.0
        wedding_order_cnt = 0.0
        for stat in db.query(MonthlyStats).filter(MonthlyStats.period.in_(period_months)).all():
            if stat.business_type == '旅拍':
                travel_order_cnt += float(stat.order_count or 0)
            elif stat.business_type == '婚礼':
                wedding_order_cnt += float(stat.order_count or 0)

        if promo_mode == 'allocation':
            # 分摊口径：工资总额 ÷ 下单订单数
            travel_avg = {item: (travel_salary[item] / travel_order_cnt if travel_order_cnt else 0) for item in salary_items}
            wedding_avg = {item: (wedding_salary[item] / wedding_order_cnt if wedding_order_cnt else 0) for item in salary_items}
        else:
            # 实际口径：工资总额 ÷ 选片订单数
            travel_avg = {item: (travel_salary[item] / travel_qty if travel_qty else 0) for item in salary_items}
            wedding_avg = {item: (wedding_salary[item] / wedding_qty if wedding_qty else 0) for item in salary_items}

        for idx, row in result.iterrows():
            biz = row['业务类型']
            qty = row['套系数量']
            if biz == '旅拍':
                avg = travel_avg
            elif biz == '婚礼':
                avg = wedding_avg
            else:
                continue
            total_labor = 0.0
            for item in salary_items:
                val = avg[item] * qty
                result.at[idx, item] = val
                total_labor += val
            result.at[idx, '人工成本'] = total_labor

    finally:
        db.close()

    return result


def clear_profit_cache() -> None:
    """清空利润报表缓存。写操作（导入/维护成本等）成功后调用，
    避免用户在 10 分钟缓存窗口内看到旧数字。任何异常都不得影响调用方。"""
    try:
        generate_profit_report.clear()
    except Exception:
        pass
    try:
        generate_profit_report_multi_month.clear()
    except Exception:
        pass