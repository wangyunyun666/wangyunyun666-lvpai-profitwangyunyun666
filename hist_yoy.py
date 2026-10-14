# -*- coding: utf-8 -*-
"""读取「利润全年-汇总」历史表，供经营利润汇报的「同比」使用。

数据来源：用户的企业历史汇总表（每业务线一份），例如
    2025年旅拍利润全年-汇总.xlsx
    2025年婚礼利润全年-汇总.xlsx

每份文件内含两个区块，区块标题末位数字决定口径：
    ××1  → 实际口径（推广费用（实际）、人工成本(实际)）
    ××2  → 分摊口径（推广费用（分摊）、人工成本(分摊)）

统一返回：
    {('2025-07', 'actual', '旅拍'): {'收入':x, '单数':n, '推广':x, '人工':x, '净利润':x}, ...}

本模块只读取与汇总，不参与任何成本口径计算。
"""
import pandas as pd

BLOCK_MODE = {'1': 'actual', '2': 'allocation'}

# 固定列位（两份表的列顺序一致，已逐格核对）
C_DATE, C_INC, C_QTY, C_PROMO, C_SAL, C_NET = 0, 1, 2, 3, 4, 16


def _num(v):
    try:
        f = float(v)
    except Exception:
        return 0.0
    return 0.0 if pd.isna(f) else f


def load_from_files(files):
    """从上传的文件读取。

    Args:
        files: [file_like, ...]，可以是 Streamlit 的 UploadedFile 或本地路径。

    Returns:
        dict: {( 'YYYY-MM', mode, 业务线 ): {...}}；业务线按「区块标题所在文件」判定，
              文件名含「旅拍」即归为旅拍、含「婚礼」即归为婚礼，无法判定时归入「全部」。
    """
    out = {}
    for f in files:
        if f is None:
            continue
        name = str(getattr(f, 'name', f))
        if '旅拍' in name and '婚礼' not in name:
            biz = '旅拍'
        elif '婚礼' in name:
            biz = '婚礼'
        else:
            biz = '全部'
        try:
            d = pd.read_excel(f, header=None)
        except Exception:
            continue
        cur_mode = None
        for i in range(len(d)):
            first = str(d.iat[i, C_DATE]).strip()
            second = str(d.iat[i, 1]).strip()
            if first == '类别':
                cur_mode = BLOCK_MODE.get(second[-1:]) if second else None
                continue
            if first == '日期' or first in ('总计', 'nan', ''):
                continue
            if cur_mode is None:
                continue
            ts = pd.to_datetime(d.iat[i, C_DATE], errors='coerce')
            if pd.isna(ts):
                continue
            key = (ts.strftime('%Y-%m'), cur_mode, biz)
            rec = out.setdefault(key, {'收入': 0.0, '单数': 0.0, '推广': 0.0,
                                       '人工': 0.0, '净利润': 0.0})
            rec['收入'] += _num(d.iat[i, C_INC])
            rec['单数'] += _num(d.iat[i, C_QTY])
            rec['推广'] += _num(d.iat[i, C_PROMO])
            rec['人工'] += _num(d.iat[i, C_SAL])
            rec['净利润'] += _num(d.iat[i, C_NET])
    return out


def sum_months(hist, months, mode, biz=None):
    """把若干个月（如 ['2025-01', ...]）按口径 +（可选）业务线汇总成一条记录。"""
    agg = {'收入': 0.0, '单数': 0.0, '推广': 0.0, '人工': 0.0, '净利润': 0.0}
    hit = False
    for m in months:
        for b in ([biz] if biz else ('旅拍', '婚礼', '全部')):
            r = hist.get((m, mode, b))
            if not r:
                continue
            hit = True
            for k in agg:
                agg[k] += r[k]
    return agg if hit else None


def yoy_pack(hist, mode, months, prev_year_root):
    """一次取出「整体 + 分业务线」的同比数据。

    Args:
        hist: load_from_files() 的返回值。
        mode: 'actual' / 'allocation'。
        months: 本期月份列表（如 ['2026-07']），用于换算去年同月。
        prev_year_root: 去年年份，如 2025。

    Returns:
        dict: {'all': {...}, '旅拍': {...}, '婚礼': {...}}，缺失的键为 None。
    """
    mm = [m.replace(m[:4], str(prev_year_root), 1) for m in months]
    return {
        'all': sum_months(hist, mm, mode),
        '旅拍': sum_months(hist, mm, mode, '旅拍'),
        '婚礼': sum_months(hist, mm, mode, '婚礼'),
    }
