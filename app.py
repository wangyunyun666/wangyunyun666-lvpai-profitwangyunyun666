# app.py
import streamlit as st
import pandas as pd
import numpy as np
import json
import io
pd.set_option("styler.render.max_elements", 10**9)  # 设置为10亿，足够覆盖任何查询结果
from datetime import datetime, date, timedelta
from workbench import workbench_page, _wb_handle_goto
from import_center import import_center_page, inject_ic_css as _ic_css
import auth_persist as _auth
from database import SessionLocal, User, Order, SetStandardCost, ActualDirectCost, CherryProductCost, IndirectCost, AllocationRule, OperationLog, MonthlyStats, ModulePermission, ProfitSnapshot, EmployeeSalary
from profit_engine import generate_profit_report, generate_profit_report_multi_month
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
import re
import calendar
import time
import os

st.set_page_config(page_title="旅拍利润系统", layout="wide")

st.markdown("""
<style>
    .stApp { background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf1 100%); }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #2c3e50 0%, #3498db 100%); color: white; }
    [data-testid="stSidebar"] .stRadio label, [data-testid="stSidebar"] .stCaption, 
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: white !important; }
    [data-testid="stSidebar"] .stRadio > div { background: rgba(255,255,255,0.1); border-radius: 8px; padding: 0.5rem; margin-bottom: 0.3rem; }
    .stButton > button { border-radius: 10px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; font-weight: 600; border: none; transition: all 0.3s; box-shadow: 0 2px 6px rgba(0,0,0,0.1); }
    .stButton > button:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.2); }
    .block-container { background: rgba(255,255,255,0.9); border-radius: 16px; padding: 2rem; margin-top: 1rem; backdrop-filter: blur(10px); box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
    [data-testid="metric-container"] { background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); padding: 1rem; border-left: 4px solid #667eea; }
    h1, h2, h3 { color: #2c3e50; font-weight: 600; }
    .login-box { max-width: 400px; margin: 4rem auto; padding: 2rem; background: white; border-radius: 16px; box-shadow: 0 12px 24px rgba(0,0,0,0.08); border-top: 4px solid #667eea; }
    .import-hint { background: #fff8e1; border-left: 5px solid #ffc107; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }
</style>
""", unsafe_allow_html=True)

SET_TYPE_MAP = {
    "丽江员工特惠5577": "旅拍", "丽江网红款5980": "旅拍", "大理网红款5980": "旅拍",
    "大理员工特惠5577": "旅拍", "大理爆款6980": "旅拍", "丽江爆款6980": "旅拍",
    "大理全包套餐8980": "旅拍", "丽江8999一价全包套餐": "旅拍", "大理9777 国庆活动": "旅拍",
    "丽江全包套系9980": "旅拍", "大理双影像618活动套餐": "旅拍", "双11爆款4911大理": "旅拍",
    "大理8888一价全包套系": "旅拍", "大理双影像9888限定套餐": "旅拍", "店庆爆款丽江6888": "旅拍",
    "丽江8888一价全包套餐": "旅拍", "大理定制套系10980": "旅拍", "双11爆款丽江": "旅拍",
    "丽江定制套系10980": "旅拍", "店庆爆款大理6888": "旅拍", "丽江主理人套系10980": "旅拍",
    "大理主理人套系10980": "旅拍", "丽江小发哥专属10980": "旅拍", "大理小发哥专属10980": "旅拍",
    "丽江小发哥专属12520": "旅拍", "丽江小发哥专属16980": "旅拍", "大理小发哥专属16980": "旅拍",
    "新疆特惠8980": "旅拍", "新疆网红款10980": "旅拍", "新疆高定款12980": "旅拍",
    "13980丽江婚礼": "婚礼", "16980丽江目的地婚礼": "婚礼", "16980丽江婚礼": "婚礼",
    "13980丽江婚礼套系": "婚礼", "19980丽江婚礼": "婚礼", "16980大理婚礼": "婚礼",
    "19980大理婚礼": "婚礼", "19980丽江目的地婚礼": "婚礼", "16980丽江目的地": "婚礼",
    "29980丽江目的地婚礼": "婚礼", "20980新疆婚礼": "婚礼", "14980大理婚礼": "婚礼",
    "16980新疆婚礼": "婚礼", "9999纯拍婚礼丽江": "婚礼",
}

if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_id' not in st.session_state: st.session_state.user_id = None
if 'username' not in st.session_state: st.session_state.username = None
if 'role' not in st.session_state: st.session_state.role = None

# 会话超时（加固）：从环境变量读取，默认 480 分钟，转换为秒
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT_MIN", "480")) * 60

# ============================================================================
# 持久化登录（新增，2026-09）
# ----------------------------------------------------------------------------
# 解决"过一会不用就被踢回登录页"：session_state 只活在 WebSocket 连接期间，
# 连接一断就丢；改为把签名令牌存进浏览器 cookie，重连时自动恢复登录态。
# 令牌签名与有效期逻辑全部在 auth_persist.py，此处只做接入。
# ============================================================================
_auth.try_restore_login()          # 必须先于任何页面渲染
_auth.check_session_timeout()      # 应用内超时（默认 480 分钟）及时长判断
_auth.flush_pending_cookie()       # 登录/退出后的 cookie 落盘

def add_log(user_id, username, action, details=""):
    db = SessionLocal()
    try:
        log = OperationLog(user_id=user_id, username=username, action=action, details=details)
        db.add(log); db.commit()
    except: pass
    finally: db.close()

def standardize_period(raw):
    s = str(raw).strip()
    if '年' in s and '月' in s:
        parts = s.replace('年', ' ').replace('月', '').split()
        if len(parts) >= 2:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}"
    return s[:7]

def login_page():
    st.markdown("<div class='login-box'>", unsafe_allow_html=True)
    st.title("📸 旅拍利润系统")
    username = st.text_input("用户名")
    password = st.text_input("密码", type="password")
    if st.button("登 录"):
        db = SessionLocal()
        user = db.query(User).filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            st.session_state.logged_in = True
            st.session_state._login_ts = time.time()
            st.session_state.user_id = user.id
            st.session_state.username = user.username
            st.session_state.role = user.role
            _auth.save_login(user.username, user.role, user.id)  # 写入持久化 cookie
            add_log(user.id, user.username, "登录", "登录成功")
            st.success("登录成功！"); st.rerun()
        else: st.error("用户名或密码错误")
        db.close()
    st.caption("默认管理员：admin")
    st.markdown("</div>", unsafe_allow_html=True)

def get_permission_map():
    db = SessionLocal()
    try:
        perms = db.query(ModulePermission).all()
        perm_map = {}
        for p in perms:
            perm_map[p.module_name] = [r.strip() for r in p.allowed_roles.split(',') if r.strip()]
        return perm_map
    finally:
        db.close()

def has_permission(module_name, role):
    perm_map = get_permission_map()
    allowed = perm_map.get(module_name, ['admin'])
    return role in allowed

def mask_numeric(value):
    if isinstance(value, (int, float)):
        return '***'
    return value

def mask_dataframe(df):
    if df.empty:
        return df
    df_masked = df.copy()
    for col in df_masked.select_dtypes(include=[np.number]).columns:
        df_masked[col] = '***'
    return df_masked


# ---- 导航菜单（正式可见项）----
# 说明：原「导入类」入口已全部收进「📥 数据导入中心」，故此处不再单独列出，
# 菜单从 24 项精简为 13 项。对应的页面函数与路由分支均**原样保留**，
# 只是不再出现在侧边栏；如需恢复某个入口，把它加回本列表即可。
_WB_MENU = [
    "🏠 工作台",
    "📥 数据导入中心",
    "📊 生成利润表",
    "📁 利润表历史",
    "📋 运营成本查询",
    "🔍 选片订单查询",
    "📋 账单查询",
    "📋 数据查询",
    "🧹 清理重复数据",
    "🔧 修复历史拍摄费用解析",
    "📖 规则说明",
    "🔐 权限管理",
    "👥 用户管理",
    "📜 操作日志",
]

# ---- 已收进导入中心的入口（保留变量以便将来恢复/排查，不在菜单中显示）----
_WB_MENU_HIDDEN = [
    "📥 导入收入数据",
    "📊 导入运营成本",
    "📋 维护标准成本",
    "💰 导入实际直接成本",
    "🏭 导入樱桃云产品成本",
    "📈 录入间接成本",
    "👥 员工工资管理",
    "📊 录入月度基础数据",
    "⚙️ 推广费分摊设置",
    "📥 账单导入",
    "🏜️ 新疆费用导入",
]


def main_sidebar():
    with st.sidebar:
        st.title(f"👤 {st.session_state.username}")
        st.caption(f"角色：{'🔑 管理员' if st.session_state.role == 'admin' else '✏️ 编辑者'}")
        st.divider()
        # ---- 工作台跳转通道（必须在 radio 之前）----
        _wb_handle_goto(_WB_MENU)
        menu = st.radio("导航菜单", _WB_MENU, key="nav_menu")
        st.divider()
        if st.button("🚪 退出登录"):
            add_log(st.session_state.user_id, st.session_state.username, "退出", "退出登录")
            # 清除持久化 cookie。⚠️ 必须先落盘再 rerun：clear_login 靠渲染
            # 隐藏 iframe 执行 JS，若之后立刻 rerun，这次渲染会被丢弃，cookie 清不掉。
            _auth.clear_login()
            st.session_state.logged_in = False
            st.session_state._login_ts = 0
            # 给浏览器一点时间执行清理脚本；随后由下面的 rerun 回到登录页
            time.sleep(0.6)
            st.rerun()
        return menu

def permission_management_page():
    st.header("🔐 模块权限管理")
    st.markdown("设置每个功能模块允许查看真实数据的角色（未配置的模块默认仅管理员可看）。")
    all_modules = [
        "📊 生成利润表", "📁 利润表历史", "📥 导入收入数据", "📋 维护标准成本", "💰 导入实际直接成本",
        "🏭 导入樱桃云产品成本", "📈 录入间接成本", "👥 员工工资管理", "📊 录入月度基础数据",
        "⚙️ 推广费分摊设置", "🔍 选片订单查询", "📥 账单导入",
        "🏜️ 新疆费用导入", "📋 账单查询", "📋 数据查询", "🧹 清理重复数据",
        "🔧 修复历史拍摄费用解析", "📖 规则说明", "🔐 权限管理", "👥 用户管理", "📜 操作日志"
    ]
    db = SessionLocal()
    try:
        saved_perms = {p.module_name: p.allowed_roles for p in db.query(ModulePermission).all()}
    finally:
        db.close()
    with st.form("perm_form"):
        new_perms = {}
        for mod in all_modules:
            default_roles = saved_perms.get(mod, 'admin')
            selected_roles = st.multiselect(
                f"允许查看真实数据的角色：{mod}",
                options=['admin', 'editor'],
                default=default_roles.split(',') if default_roles else ['admin'],
                key=mod
            )
            new_perms[mod] = ','.join(selected_roles) if selected_roles else 'admin'
        if st.form_submit_button("保存权限设置"):
            db = SessionLocal()
            try:
                for mod, roles in new_perms.items():
                    existing = db.query(ModulePermission).filter_by(module_name=mod).first()
                    if existing:
                        existing.allowed_roles = roles
                    else:
                        db.add(ModulePermission(module_name=mod, allowed_roles=roles))
                db.commit()
                st.success("权限配置已更新！")
                add_log(st.session_state.user_id, st.session_state.username, "修改模块权限", "")
            except Exception as e:
                db.rollback()
                st.error(f"保存失败：{e}")
            finally:
                db.close()

# 交互式报告前端 JS 模板（占位符 __REPORT_DATA_JSON__ / __DEFAULT_CALIBER__ 由 generate_html_report 注入）。
# 纯前端：切换「费用口径」「套系筛选」下拉时，KPI 卡片、3 张图表、套系利润明细表实时重绘，不依赖服务器。
_INTERACTIVE_REPORT_JS = """<script>
const REPORT_DATA = __REPORT_DATA_JSON__;
(function(){
  if (typeof REPORT_DATA === 'undefined' || !REPORT_DATA) { return; }
  var FONT = {family: "'PingFang SC','Microsoft YaHei',sans-serif"};
  var STRING_COLS = ['套系', '业务类型'];
  var AVG_COLS = ['拍摄费用','样片研发','推广费用（实际）','人工成本','微电影拍摄费用','二销选片费','微电影剪辑费用'];
  var SET_ALL = 'ALL';
  var CALIBER_LABEL = {'actual':'实际口径','allocation':'分摊口径'};

  var caliberSelect = document.getElementById('caliberSelect');
  var setSelect = document.getElementById('setSelect');

  function fmtMoney(v){
    if (v === null || v === undefined || isNaN(Number(v))) return '—';
    return '¥' + Number(v).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  function fmtWan(v){
    if (v === null || v === undefined || isNaN(Number(v))) return '—';
    return '¥' + (Number(v)/10000).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2}) + '万';
  }
  function fmtInt(v){
    if (v === null || v === undefined || isNaN(Number(v))) return '—';
    return String(Math.round(Number(v)));
  }
  function fmtPct(v){
    if (v === null || v === undefined || isNaN(Number(v))) return '—';
    return (Number(v)*100).toFixed(1) + '%';
  }
  function num(v){
    if (v === null || v === undefined) return 0;
    var n = Number(v);
    return isNaN(n) ? 0 : n;
  }

  function getScope(caliber, setOption){
    var d = REPORT_DATA[caliber];
    if (!d || !d.rows || d.rows.length === 0) return null;
    if (setOption === SET_ALL || !setOption){
      return { rows: d.rows, scope: d.totals || {}, isAll: true };
    }
    var row = null;
    for (var i=0;i<d.rows.length;i++){ if (String(d.rows[i]['套系']) === setOption){ row = d.rows[i]; break; } }
    if (!row) row = d.rows[0];
    return { rows: [row], scope: row, isAll: false };
  }

  function updateKPI(caliber, setOption){
    var inc = document.getElementById('kpiIncome');
    var cost = document.getElementById('kpiCost');
    var prof = document.getElementById('kpiProfit');
    var g = getScope(caliber, setOption);
    if (!g){ inc.textContent='—'; cost.textContent='—'; prof.textContent='—'; return; }
    var s = g.scope;
    inc.textContent = fmtWan(num(s['总收入']));
    cost.textContent = fmtWan(num(s['总直接成本']) + num(s['总间接成本']));
    prof.textContent = fmtWan(num(s['利润合计']));
  }

  function renderCost(caliber, setOption){
    var g = getScope(caliber, setOption);
    if (!g) return;
    var s = g.scope;
    var totalCost = num(s['总直接成本']) + num(s['总间接成本']);
    var profit = num(s['利润合计']);
    var traces, layout;
    if (profit >= 0){
      traces = [{ type:'pie', labels:['总成本','利润'], values:[totalCost, profit], marker:{colors:['#ef4444','#10b981']}, textinfo:'label+percent', hole:0.0 }];
      layout = { title:{text:'成本与利润结构'}, font:FONT, margin:{t:40,b:10,l:10,r:10} };
    } else {
      traces = [{ type:'bar', x:['总成本','利润'], y:[totalCost, profit], marker:{color:['#ef4444','#ef4444']}, hovertemplate:'%{x}<br>金额: ¥%{y:,.2f}<extra></extra>' }];
      layout = { title:{text:'成本与利润结构（利润为负的说明）'}, yaxis:{title:{text:'金额 (元)'}}, shapes:[{type:'line',x0:-0.5,x1:1.5,y0:0,y1:0,line:{color:'black',width:1}}], font:FONT };
    }
    if (typeof Plotly !== 'undefined') Plotly.react('cost_chart', traces, layout, {responsive:true, displayModeBar:true});
  }

  function renderProfit(caliber, setOption){
    var g = getScope(caliber, setOption);
    if (!g) return;
    var rows = g.rows;
    var names = [], profits = [], colors = [];
    for (var i=0;i<rows.length;i++){
      names.push(String(rows[i]['套系']));
      var p = num(rows[i]['利润合计']);
      profits.push(p);
      colors.push(p >= 0 ? '#10b981' : '#ef4444');
    }
    var traces = [{ type:'bar', x:names, y:profits, marker:{color:colors}, hovertemplate:'%{x}<br>利润: ¥%{y:,.2f}<extra></extra>' }];
    var layout = { title:{text:'各套系利润对比'}, xaxis:{tickangle:-45, automargin:true, tickfont:{size:10}}, yaxis:{title:{text:'利润 (元)'}}, font:FONT };
    if (typeof Plotly !== 'undefined') Plotly.react('profit_chart', traces, layout, {responsive:true, displayModeBar:true});
  }

  function renderAvg(caliber, setOption){
    var g = getScope(caliber, setOption);
    if (!g) return;
    var d = REPORT_DATA[caliber];
    var rows = g.rows;
    var div = (REPORT_DATA.divisors || {orders:0, qty:0, erxiao:0, wedding:0});
    var ERXIAO_COLS = ['后期修片费(二销)','工厂费用（二销）','二销选片费'];
    var WEDDING_COLS = ['交付费用（主持）','交付费用（搭建）'];
    var AREA_COLS = ['交付费用（场地）'];
    var present = [], values = [];
    var col0 = rows[0] || {};
    for (var k=0;k<AVG_COLS.length;k++){
      var col = AVG_COLS[k];
      if (Object.prototype.hasOwnProperty.call(col0, col)){
        var total = 0;
        for (var i=0;i<rows.length;i++){ total += num(rows[i][col]); }
        var divisor;
        if (col === '人工成本' || col === '推广费用（实际）'){
          // 人工成本、推广费用（实际）：分摊口径除以下单订单数，实际口径除以选片订单总数
          // 单套系筛选时前端无逐套系下单订单数，沿用该套系选片订单总数（与页面口径一致）
          divisor = g.isAll
            ? (caliber === 'allocation' ? num(div.orders) : num(div.qty))
            : num(g.scope['套系数量']);
        } else if (ERXIAO_COLS.indexOf(col) >= 0){
          // 二销相关费用：除数=二销选片数（做过二销的订单数），与口径无关
          divisor = g.isAll ? num(div.erxiao) : num(g.scope['套系数量']);
        } else if (WEDDING_COLS.indexOf(col) >= 0){
          // 交付费用（主持/搭建）：仅婚礼订单产生，除数=婚礼订单数（婚礼选品订单总数），与口径无关
          divisor = g.isAll ? num(div.wedding) : num(g.scope['套系数量']);
        } else if (AREA_COLS.indexOf(col) >= 0){
          // 交付费用（场地）：除数=有场地费的选片订单数（与口径无关）；单套系筛选时前端无该值，沿用该套系选品订单数
          divisor = g.isAll ? num(div.area) : num(g.scope['套系数量']);
        } else {
          divisor = g.isAll ? num(div.qty) : num(g.scope['套系数量']);
        }
        present.push(col);
        values.push(divisor ? total/divisor : 0);
      }
    }
    var traces = [{ type:'bar', x:present, y:values, marker:{color:'#4f46e5'}, hovertemplate:'%{x}<br>单均: ¥%{y:,.2f}<extra></extra>' }];
    var layout = { title:{text:'主要费用项均价（按订单数分摊）'}, xaxis:{tickangle:-45, automargin:true, tickfont:{size:10}}, yaxis:{title:{text:'单均 (元/单)'}}, font:FONT };
    if (typeof Plotly !== 'undefined') Plotly.react('avg_chart', traces, layout, {responsive:true, displayModeBar:true});
  }

  // 重绘「均价及同比环比分析」表中的「本期均价 / 除数」两列：
  // 人工成本、推广费用（实际）随口径切换除数（分摊=下单订单数，实际=选片订单总数）；其余费用项与口径无关。
  function renderAvgTable(caliber){
    var d = REPORT_DATA[caliber];
    if (!d || !d.rows) return;
    var div = (REPORT_DATA.divisors || {orders:0, qty:0});
    function colSum(colName){
      var s = 0;
      for (var i=0;i<d.rows.length;i++){ var v = d.rows[i][colName]; if (v !== null && v !== undefined) s += num(v); }
      return s;
    }
    var ordersDiv = caliber === 'allocation' ? num(div.orders) : num(div.qty);
    var divisorLabel = caliber === 'allocation' ? '下单订单数' : '选片订单总数';
    var items = [
      {name:'人工成本', sum: colSum('人工成本')},
      {name:'推广费用（实际）', sum: colSum('推广费用（实际）')}
    ];
    var body = document.getElementById('avgTableBody');
    if (!body) return;
    var trs = body.querySelectorAll('tr');
    for (var t=0;t<trs.length;t++){
      var tds = trs[t].querySelectorAll('td');
      if (tds.length < 3) continue;
      var nm = (tds[0].textContent || '').trim();
      for (var j=0;j<items.length;j++){
        if (nm === items[j].name){
          var val = ordersDiv ? items[j].sum / ordersDiv : 0;
          tds[1].textContent = fmtMoney(val);
          tds[2].textContent = divisorLabel + ': ' + Math.round(ordersDiv);
          break;
        }
      }
    }
  }

  function renderTable(caliber){
    var d = REPORT_DATA[caliber];
    var head = document.getElementById('setTableHead');
    var body = document.getElementById('setTableBody');
    if (!d || !d.rows || !head || !body){ return; }
    var rows = d.rows;
    if (rows.length === 0){ head.innerHTML='<tr><th>暂无数据</th></tr>'; body.innerHTML='<tr><td>暂无数据</td></tr>'; return; }
    var cols = Object.keys(rows[0]);
    var h = '';
    for (var c=0;c<cols.length;c++){ h += '<th>' + cols[c] + '</th>'; }
    head.innerHTML = '<tr>' + h + '</tr>';
    var html = '';
    for (var i=0;i<rows.length;i++){
      var r = rows[i];
      html += '<tr>';
      for (var c=0;c<cols.length;c++){
        var col = cols[c];
        var v = r[col];
        if (STRING_COLS.indexOf(col) !== -1){
          html += '<td>' + (v === null || v === undefined ? '—' : String(v)) + '</td>';
        } else if (col === '套系数量'){
          html += '<td>' + fmtInt(v) + '</td>';
        } else if (col === '利润率'){
          html += '<td>' + fmtPct(v) + '</td>';
        } else if (col === '利润合计'){
          if (v === null || v === undefined){ html += '<td>—</td>'; }
          else { var cls = num(v) < 0 ? 'loss' : 'profit'; html += '<td><span class="' + cls + '">' + fmtMoney(v) + '</span></td>'; }
        } else {
          html += '<td>' + fmtMoney(v) + '</td>';
        }
      }
      html += '</tr>';
    }
    body.innerHTML = html;
  }

  function populateSetSelect(caliber){
    var d = REPORT_DATA[caliber];
    var cur = setSelect.value;
    setSelect.innerHTML = '<option value="ALL">全部套系（合计）</option>';
    if (d && d.rows){
      for (var i=0;i<d.rows.length;i++){
        var name = String(d.rows[i]['套系']);
        var opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        setSelect.appendChild(opt);
      }
    }
    var exists = false;
    for (var j=0;j<setSelect.options.length;j++){ if (setSelect.options[j].value === cur){ exists = true; break; } }
    setSelect.value = (cur && cur !== 'ALL' && exists) ? cur : 'ALL';
  }

  function updateAll(){
    var caliber = caliberSelect.value;
    var setOption = setSelect.value;
    var badge = document.getElementById('caliberBadge');
    if (badge) badge.textContent = (CALIBER_LABEL[caliber] || caliber);
    renderTable(caliber);
    updateKPI(caliber, setOption);
    renderCost(caliber, setOption);
    renderProfit(caliber, setOption);
    renderAvg(caliber, setOption);
    renderAvgTable(caliber);
  }

  caliberSelect.addEventListener('change', function(){
    populateSetSelect(caliberSelect.value);
    updateAll();
  });
  setSelect.addEventListener('change', updateAll);

  caliberSelect.value = __DEFAULT_CALIBER__;
  populateSetSelect(caliberSelect.value);
  updateAll();
})();
</script>"""


def generate_html_report(df_biz, total_income, total_direct, total_indirect, total_profit, total_orders,
                         period_label, filter_option, fee_table_rows, avg_table_rows, shoot_table_rows, labor_table_rows,
                         set_table_full_html, cost_chart_html, profit_chart_html, avg_chart_html, caliber_label="实际口径",
                         report_data_json=None):
    # 成本口径说明（直接费用 / 间接费用）备注块，原样保留中文与换行
    caliber_remark_html = '''<div style="margin:24px 0;padding:16px 20px;border:1px solid #e2e8f0;border-left:4px solid #4f46e5;border-radius:12px;background:#fff;">
  <div style="font-size:1.05rem;font-weight:700;margin-bottom:8px;">📌 成本口径说明（直接费用 / 间接费用）</div>
  <div style="margin-bottom:6px;"><strong>【直接费用】</strong>计入「总直接成本」列，包含：推广费用（实际）（即"推广客资费（实际）"）、交付费用（场地）、交付费用（主持）、交付费用（搭建）、鲜花费用、微电影拍摄费用、拍摄费用（即"拍摄费用 (郭鹏)"）、二销选片费（即"门店二销款结算费"）、像素蛋糕修图费、微电影剪辑费用、后期修片费 (一销)、后期修片费 (二销)、工厂费用（一销）、工厂费用（二销）</div>
  <div style="margin-bottom:6px;"><strong>【间接费用】</strong>计入「总间接成本」列，包含：房租、水电、办公费等；税费及手续费；样片研发；场地铺设费；舆情处理</div>
  <div style="color:#b45309;"><strong>⚠️ 对账提示：</strong>当前系统计算时，「人工成本（工资，7 个部门）」也计入「总直接成本」，上表未单列——用你自己的表对账时，请把工资一并计入直接费用，否则两边会差一块。</div>
</div>'''
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>利润分析报告</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
    <style>
        body {{ font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f8fafc; margin: 20px; color: #1e293b; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .header h1 {{ color: #4f46e5; }}
        .kpi-grid {{ display: flex; gap: 20px; justify-content: center; margin-bottom: 30px; }}
        .kpi-card {{ background: white; border-radius: 16px; padding: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); text-align: center; flex: 1; }}
        .kpi-value {{ font-size: 2rem; font-weight: bold; }}
        .section-title {{ font-size: 1.5rem; font-weight: bold; margin: 30px 0 10px; color: #1e293b; }}
        .table-wrapper {{ background: white; border-radius: 16px; padding: 15px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
        th {{ background: #f1f5f9; padding: 12px; text-align: right; }}
        th:first-child, td:first-child {{ text-align: left; }}
        td {{ padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: right; }}
        .remark-cell {{ text-align: left !important; white-space: normal !important; color: #475569; font-size: 0.8rem; }}
        .loss {{ color: #ef4444; font-weight: bold; }}
        .profit {{ color: #10b981; font-weight: bold; }}
        .chart-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 20px; margin: 20px 0; }}
        .chart-box {{ background: white; border-radius: 16px; padding: 15px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); text-align: center; min-width: 0; overflow: hidden; }}
        .chart-box img {{ max-width: 100%; height: auto; }}
        .control-bar {{ display: flex; gap: 24px; justify-content: center; align-items: flex-end; margin: 0 0 24px; padding: 16px 24px; background: white; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); flex-wrap: wrap; }}
        .control-item {{ display: flex; flex-direction: column; gap: 6px; }}
        .control-item label {{ font-size: 0.85rem; color: #475569; font-weight: 600; }}
        .control-bar select {{ padding: 8px 14px; border: 1px solid #cbd5e1; border-radius: 10px; font-size: 0.95rem; background: #fff; color: #1e293b; min-width: 200px; cursor: pointer; }}
        .control-bar select:focus {{ outline: 2px solid #4f46e5; outline-offset: 1px; }}
        .caliber-note {{ font-size: 0.8rem; color: #64748b; text-align: center; margin: 0 0 8px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 利润分析报告</h1>
        <p>{period_label} · {filter_option} · 订单总数 {total_orders}
        <span id="caliberBadge" style="{{background:#4f46e5;color:#fff;padding:4px 14px;border-radius:999px;font-weight:600;margin-left:8px;}}">{caliber_label}</span></p>
    </div>

    <div class="control-bar">
        <div class="control-item">
            <label for="caliberSelect">费用口径</label>
            <select id="caliberSelect">
                <option value="actual">实际口径</option>
                <option value="allocation">分摊口径</option>
            </select>
        </div>
        <div class="control-item">
            <label for="setSelect">套系筛选</label>
            <select id="setSelect">
                <option value="ALL">全部套系（合计）</option>
            </select>
        </div>
    </div>

    <div class="kpi-grid">
        <div class="kpi-card"><div>💰 总收入</div><div class="kpi-value" id="kpiIncome">¥{total_income/10000:.2f}万</div></div>
        <div class="kpi-card"><div>📉 总成本</div><div class="kpi-value" id="kpiCost">¥{(total_direct+total_indirect)/10000:.2f}万</div></div>
        <div class="kpi-card"><div>💎 净利润</div><div class="kpi-value" id="kpiProfit" style="color:#ef4444;">¥{total_profit/10000:.2f}万</div></div>
    </div>

    <div class="chart-row">
        <div class="chart-box"><h3>成本与利润结构</h3>{cost_chart_html}</div>
        <div class="chart-box"><h3>各套系利润对比</h3>{profit_chart_html}</div>
    </div>

    <div class="caliber-note">同比/环比分析基于导出口径：{caliber_label}</div>

    <div class="section-title">📸 拍摄费用明细分析（含同比）</div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>明细项目</th><th>本期均价</th><th>去年同期均价</th><th>同比差异</th><th>同比变化</th><th>上月均价</th><th>环比差异</th><th>环比变化</th></tr></thead>
            <tbody>{shoot_table_rows}</tbody>
        </table>
    </div>

    <div class="section-title">💼 人工成本分析（部门均价同比）</div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>部门</th><th>本期均价</th><th>去年同期均价</th><th>同比差异</th><th>同比变化</th><th>上月均价</th><th>环比差异</th><th>环比变化</th></tr></thead>
            <tbody>{labor_table_rows}</tbody>
        </table>
    </div>

    <div class="section-title">📈 均价及同比环比分析</div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>费用项</th><th>本期均价</th><th>除数</th><th>去年同期均价</th><th>同比差异</th><th>同比变化</th><th>上月均价</th><th>上月除数</th><th>环比差异</th><th>环比变化</th></tr></thead>
            <tbody id="avgTableBody">{avg_table_rows}</tbody>
        </table>
    </div>

    <div class="section-title">📋 费用明细（含注释）</div>
    <div class="table-wrapper">
        <table>
            <thead><tr><th>费用项</th><th>总金额 (¥)</th><th>占总收入比例</th><th>备注</th></tr></thead>
            <tbody>{fee_table_rows}</tbody>
        </table>
    </div>

    <div class="section-title">📋 套系利润明细表</div>
    {set_table_full_html}

    <div class="chart-box"><h3>🔸 主要费用项均价</h3>{avg_chart_html}</div>

    {caliber_remark_html}
</body>
</html>'''
    if report_data_json:
        # 防御：避免数据中出现 </script> 提前截断脚本；NaN 已在 Python 侧转 None，此处 JSON 必合法
        _safe_json = report_data_json.replace('</', '<\\/')
        _script = (_INTERACTIVE_REPORT_JS
                   .replace('__REPORT_DATA_JSON__', _safe_json)
                   .replace('__DEFAULT_CALIBER__', json.dumps('actual' if caliber_label == '实际口径' else 'allocation', ensure_ascii=False)))
        html = html.replace('</body>', _script + '\n</body>', 1)
    return html

def save_profit_snapshot(period_mode, filter_option, period_start, period_end, period_label, df_full):
    db = SessionLocal()
    try:
        json_data = df_full.to_json(orient='records', date_format='iso')
        last_snapshot = db.query(ProfitSnapshot).filter(
            ProfitSnapshot.period_mode == period_mode,
            ProfitSnapshot.filter_option == filter_option,
            ProfitSnapshot.period_start == period_start,
            ProfitSnapshot.period_end == period_end
        ).order_by(ProfitSnapshot.created_at.desc()).first()
        if not last_snapshot or last_snapshot.data_json != json_data:
            snapshot = ProfitSnapshot(
                period_mode=period_mode,
                filter_option=filter_option,
                period_start=period_start,
                period_end=period_end,
                period_label=period_label,
                data_json=json_data
            )
            db.add(snapshot)
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"保存利润表快照失败：{e}")
    finally:
        db.close()

_PH_CSS = """
<style>
.ph-wrap{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.ph-hd{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;
  border-radius:16px;padding:24px 30px;margin-bottom:16px;
  box-shadow:0 6px 20px rgba(102,126,234,.22)}
.ph-hd h1{margin:0 0 8px;font-size:23px;font-weight:700;color:#fff}
.ph-hd .meta{opacity:.92;font-size:13px;line-height:1.7}
.ph-mode{display:flex;gap:10px;align-items:stretch;margin-bottom:16px;flex-wrap:wrap}
.ph-mbtn{padding:9px 22px;border-radius:22px;border:1px solid #e8ecf1;background:#fff;
  font-size:13.5px;font-weight:600;color:#7a8a9a;cursor:default}
.ph-mbtn.on{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;
  border-color:transparent;box-shadow:0 3px 10px rgba(102,126,234,.28)}
.ph-mtip{flex:1;min-width:260px;background:#fff;border:1px solid #e8ecf1;border-radius:12px;
  padding:9px 16px;font-size:12.5px;color:#7a8a9a;line-height:1.6;display:flex;
  align-items:center}
.ph-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:14px;margin-bottom:18px}
.ph-card{background:#fff;border:1px solid #e8ecf1;border-radius:14px;
  padding:16px 18px;box-shadow:0 2px 8px rgba(44,62,80,.05)}
.ph-card .k{font-size:12px;color:#7a8a9a;margin-bottom:7px;font-weight:500}
.ph-card .v{font-size:23px;font-weight:700;color:#2c3e50;line-height:1.25}
.ph-card .s{font-size:12px;color:#95a5b6;margin-top:5px}
.ph-up{color:#c0392b}
.ph-down{color:#27ae60}
.ph-sec{font-size:15px;font-weight:700;color:#2c3e50;margin:22px 0 10px;
  padding-left:11px;border-left:4px solid #667eea;line-height:1.3}
.ph-hint{font-size:12.5px;color:#95a5b6;margin:0 0 12px 15px}
.ph-tb{background:#fff;border:1px solid #e8ecf1;border-radius:14px;overflow:hidden;
  box-shadow:0 2px 8px rgba(44,62,80,.05)}
.ph-tb table{width:100%;border-collapse:collapse;font-size:13px}
.ph-tb th{background:#f8fafc;color:#5a6a7a;font-weight:600;font-size:12.5px;
  padding:11px 14px;text-align:right;border-bottom:1px solid #e8ecf1;white-space:nowrap}
.ph-tb th.l{text-align:left}
.ph-tb th.c{text-align:center}
.ph-tb td{padding:10px 14px;text-align:right;border-bottom:1px solid #f2f5f8;
  color:#2c3e50;white-space:nowrap}
.ph-tb td.l{text-align:left}
.ph-tb td.c{text-align:center;color:#8a9aaa}
.ph-tb tbody tr:hover{background:#fafbfd}
.ph-tb tbody tr:last-child td{border-bottom:1px solid #e8ecf1}
.ph-tb tfoot td{padding:12px 14px;text-align:right;background:#f8fafc;
  font-weight:700;color:#2c3e50;border-top:2px solid #667eea}
.ph-tb tfoot td.l{text-align:left}
.ph-tb tfoot td.c{text-align:center}
.ph-tag{display:inline-block;padding:2px 9px;border-radius:11px;font-size:11.5px;
  font-weight:600}
.ph-tag.t-lp{background:#eef4ff;color:#4a72c8;border:1px solid #dce6fa}
.ph-tag.t-hl{background:#fff0f3;color:#c04a6a;border:1px solid #fadce4}
.ph-shape{display:inline-block;padding:2px 9px;border-radius:11px;font-size:11.5px;
  font-weight:600;background:#f0f4ff;color:#4a72c8;border:1px solid #dce6fa}
.ph-bar{display:inline-block;height:7px;border-radius:4px;background:#c0392b;
  vertical-align:middle;margin-right:7px}
</style>
"""


def _ph_fmt(v):
    """金额格式化（≥1万显示万元）。"""
    try:
        v = float(v)
    except Exception:
        return "—"
    if abs(v) >= 10000:
        return f"{v / 10000:,.2f}万"
    return f"{v:,.2f}"


def _ph_esc(s):
    """HTML 转义，防止套系名里的特殊字符破坏页面。"""
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _ph_month_options():
    """可选月份列表（2025-01 至今，倒序）。"""
    today = date.today()
    out = []
    for y in range(2025, today.year + 1):
        for m in range(1, 13):
            if y == today.year and m > today.month:
                break
            out.append(f"{y}-{m:02d}")
    return out


def view_profit_history_page():
    """利润表历史 / 分析。

    改造说明（2026-09-30）：
      原页面只读 profit_snapshots（生成利润表时保存的快照），只能看单一口径。
      现改为按月份区间实时调用 profit_engine 计算，支持：
        · 费用口径切换（实际口径 / 分摊口径）
        · 业务线筛选（全部 / 旅拍 / 婚礼）
        · 套系利润排行榜（原生表格渲染，带搜索与 CSV 导出）
        · 费用结构（含占比条形图）
        · 历史快照浏览（保留原功能，原有快照不丢）
      零改动契约：profit_engine.py / database.py 未做任何修改。
    """
    module_name = "📁 利润表历史"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown(_PH_CSS, unsafe_allow_html=True)

    # ==================== 顶部：筛选区 ====================
    c1, c2 = st.columns([2, 1])
    with c1:
        month_opts = _ph_month_options()
        default_months = [m for m in month_opts if m.startswith('2026-')]
        if not default_months:
            default_months = month_opts[-1:]
        sel_months = st.multiselect(
            "📅 选择月份（可多选，按月区间统计）",
            month_opts,
            default=default_months,
            key='ph_months',
            help="同「生成利润表」口径：月份逐个计算后按套系合并，人工成本按整个区间统一分摊。")
    with c2:
        mode_label = st.selectbox("费用口径", ["分摊口径", "实际口径"],
                                  key='ph_mode')
        _mode = 'allocation' if mode_label == "分摊口径" else 'actual'

    if not sel_months:
        st.warning("请至少选择一个月")
        return

    sel_months = sorted(sel_months)
    _span_txt = (sel_months[0] if len(sel_months) == 1
                 else f"{sel_months[0]} ~ {sel_months[-1]}")

    # ==================== 计算 ====================
    try:
        with st.spinner("正在计算…"):
            df_raw = generate_profit_report_multi_month(*sel_months, promo_mode=_mode)
    except Exception as e:
        st.error(f"计算失败：{e}")
        return

    if df_raw is None or df_raw.empty:
        st.info(f"{_span_txt} 区间内暂无订单数据")
        return

    df = mask_dataframe(df_raw) if not can_see else df_raw

    # ==================== 顶部标题卡 ====================
    st.markdown(
        "<div class='ph-wrap'><div class='ph-hd'>"
        "<h1>利润表历史报告</h1>"
        f"<div class='meta'>数据区间：{_span_txt}　·　共 {len(df)} 个套系<br>"
        f"费用口径：{mode_label}　·　业务线：全部</div>"
        "</div></div>",
        unsafe_allow_html=True)

    # 口径说明条
    if _mode == 'allocation':
        _tip = ("分摊口径 —— 人工、推广费按下单订单数分摊到各套系，"
                "用于评估套系本身定价是否合理。")
    else:
        _tip = ("实际口径 —— 推广费按实际发生计入，人工按下单订单数分摊，"
                "更接近当期真实现金成本。")
    st.markdown(
        "<div class='ph-mode'>"
        f"<div class='ph-mbtn on'>{mode_label}</div>"
        f"<div class='ph-mtip'>{_tip}</div>"
        "</div>",
        unsafe_allow_html=True)

    # ==================== 业务线筛选 ====================
    _bizs = ['全部'] + sorted([b for b in df['业务类型'].dropna().unique()
                               if str(b).strip()],
                              key=lambda x: 0 if '旅拍' in str(x) else 1)
    _biz = st.radio("业务线", _bizs, horizontal=True, key='ph_biz')

    vdf = df if _biz == '全部' else df[df['业务类型'] == _biz].copy()
    if vdf.empty:
        st.info(f"{_biz} 该区间内无数据")
        return

    # 排序（按利润降序）
    vdf = vdf.sort_values('利润合计', ascending=False).reset_index(drop=True)

    _orders = int(vdf['套系数量'].sum())
    _income = float(vdf['总收入'].sum())
    _profit = float(vdf['利润合计'].sum())
    _margin = (_profit / _income * 100) if _income else 0.0
    _cls = 'ph-up' if _profit >= 0 else 'ph-down'

    # ==================== 指标卡 ====================
    st.markdown(
        "<div class='ph-cards'>"
        "<div class='ph-card'><div class='k'>套系总数</div>"
        f"<div class='v'>{_orders:,} <span style='font-size:15px'>单</span></div>"
        f"<div class='s'>{_biz} · {'单月' if len(sel_months) == 1 else str(len(sel_months)) + '个月合并'}</div></div>"
        "<div class='ph-card'><div class='k'>总收入</div>"
        f"<div class='v'>{_ph_fmt(_income)}</div>"
        "<div class='s'>套系金额 + 二销 − 退款</div></div>"
        "<div class='ph-card'><div class='k'>总利润</div>"
        f"<div class='v {_cls}'>{_ph_fmt(_profit)}</div>"
        "<div class='s'>收入 − 直接成本 − 间接成本</div></div>"
        "<div class='ph-card'><div class='k'>整体利润率</div>"
        f"<div class='v {_cls}'>{_margin:.2f}%</div>"
        f"<div class='s'>{mode_label} · 利润 ÷ 收入</div></div>"
        "</div>",
        unsafe_allow_html=True)

    # ==================== 套系利润排行榜 ====================
    st.markdown("<div class='ph-sec'>套系利润排行榜</div>", unsafe_allow_html=True)
    st.markdown("<div class='ph-hint'>按利润合计降序排列。红色=盈利（中国习惯），绿色=亏损。</div>",
                unsafe_allow_html=True)

    _rows = []
    for i, r in vdf.iterrows():
        _p = float(r.get('利润合计', 0) or 0)
        _inc = float(r.get('总收入', 0) or 0)
        _cnt = int(r.get('套系数量', 0) or 0)
        _m = (_p / _inc * 100) if _inc else 0.0
        _pc = 'ph-up' if _p >= 0 else 'ph-down'
        _bt = str(r.get('业务类型', ''))
        _tag = 't-lp' if '旅拍' in _bt else ('t-hl' if '婚礼' in _bt else '')
        _rows.append(
            "<tr>"
            f"<td class='c'>{i + 1}</td>"
            f"<td class='l'>{_ph_esc(r.get('套系', ''))}</td>"
            f"<td class='c'><span class='ph-tag {_tag}'>{_ph_esc(_bt)}</span></td>"
            f"<td>{_cnt:,}</td>"
            f"<td>{_inc / 10000:,.2f}</td>"
            f"<td class='{_pc}'>{_p / 10000:,.2f}</td>"
            f"<td class='{_pc}'>{_m:.2f}%</td>"
            "</tr>")

    _tf = (
        "<tfoot><tr>"
        "<td class='c'>—</td>"
        "<td class='l'>合计</td>"
        "<td class='c'>—</td>"
        f"<td>{_orders:,}</td>"
        f"<td>{_income / 10000:,.2f}</td>"
        f"<td class='{_cls}'>{_profit / 10000:,.2f}</td>"
        f"<td class='{_cls}'>{_margin:.2f}%</td>"
        "</tr></tfoot>")

    _table_html = (
        "<div class='ph-tb'><table>"
        "<thead><tr>"
        "<th class='c' style='width:48px'>#</th>"
        "<th class='l'>套系</th>"
        "<th class='c' style='width:76px'>业务线</th>"
        "<th style='width:76px'>单数</th>"
        "<th style='width:104px'>收入(万)</th>"
        "<th style='width:104px'>利润(万)</th>"
        "<th style='width:88px'>利润率</th>"
        "</tr></thead>"
        "<tbody>" + "".join(_rows) + "</tbody>"
        + _tf +
        "</table></div>")

    st.markdown(_table_html, unsafe_allow_html=True)

    # CSV 导出（保留原「下载」能力）
    _csv_cols = ['业务类型', '套系', '套系数量', '总收入', '利润合计', '利润率']
    _csv_cols = [c for c in _csv_cols if c in vdf.columns]
    _csv_src = vdf[_csv_cols].copy()
    if '利润率' in _csv_src.columns:
        _csv_src['利润率'] = _csv_src['利润率'].apply(
            lambda x: f"{float(x) * 100:.2f}%" if pd.notna(x) else "")
    _csv = _csv_src.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        "📥 导出排行榜 CSV", _csv,
        f"套系利润排行榜_{_span_txt}_{mode_label}_{_biz}.csv", "text/csv")

    # ==================== 费用结构 ====================
    st.markdown("<div class='ph-sec'>费用结构</div>", unsafe_allow_html=True)
    st.markdown("<div class='ph-hint'>各费用项合计金额与占直接成本比重。</div>",
                unsafe_allow_html=True)

    _FEE = [
        ('拍摄费用', '摄影师、化妆师、助理等团队费用，占比最大'),
        ('推广费用（实际）', '线上线下广告投放、推广活动'),
        ('交付费用（主持）', '婚礼主持费用，仅婚礼订单产生'),
        ('交付费用（场地）', '婚礼及旅拍场地租赁费，含自租场地'),
        ('交付费用（搭建）', '婚礼现场搭建布置费用'),
        ('鲜花费用', '婚礼鲜花布置及手捧花等'),
        ('微电影拍摄费用', '微电影前期拍摄团队成本'),
        ('微电影剪辑费用', '微电影后期剪辑成本'),
        ('二销选片费', '门店二销结算费用，按选片金额提成'),
        ('像素蛋糕修图费', '第三方修图服务费'),
        ('后期修片费(一销)', '一销订单后期修片成本'),
        ('后期修片费(二销)', '二销订单后期修片成本'),
        ('工厂费用（一销）', '相册、相框等工厂制作费（一销）'),
        ('工厂费用（二销）', '相册、相框等工厂制作费（二销）'),
        ('人工成本', '员工薪酬分摊（按订单数）'),
        ('房租、水电、办公费等', '固定运营成本分摊'),
        ('税费及手续费', '平台手续费、税费'),
        ('样片研发', '样片拍摄与研发投入'),
        ('场地铺设费', '场地建设与铺设投入'),
        ('舆情处理', '口碑维护与客诉处理'),
    ]
    _tot_dir = float(vdf['总直接成本'].sum()) if '总直接成本' in vdf.columns else 0.0
    _fee_rows = []
    for _name, _desc in _FEE:
        if _name not in vdf.columns:
            continue
        _val = float(vdf[_name].sum())
        if _val <= 0:
            continue
        _pct = (_val / _tot_dir * 100) if _tot_dir else 0.0
        _w = min(100.0, _pct * 1.6)
        _fee_rows.append(
            "<tr>"
            f"<td class='l'>{_name}</td>"
            f"<td style='width:150px'>{_ph_fmt(_val)}</td>"
            "<td style='width:190px'>"
            f"<span class='ph-bar' style='width:{_w:.1f}px'></span>{_pct:.2f}%</td>"
            f"<td class='l' style='color:#8a9aaa;font-size:12.5px'>{_desc}</td>"
            "</tr>")

    if _fee_rows:
        st.markdown(
            "<div class='ph-tb'><table>"
            "<thead><tr>"
            "<th class='l'>费用项</th>"
            "<th style='width:150px'>金额</th>"
            "<th style='width:190px'>占直接成本</th>"
            "<th class='l' style='width:300px'>说明</th>"
            "</tr></thead>"
            "<tbody>" + "".join(_fee_rows) + "</tbody>"
            "</table></div>",
            unsafe_allow_html=True)

    # ==================== 成本与利润结构 ====================
    st.markdown("<div class='ph-sec'>成本与利润结构</div>", unsafe_allow_html=True)

    _tot_cost = float(vdf['总直接成本'].sum()) + float(vdf['总间接成本'].sum()) \
        if '总间接成本' in vdf.columns else float(vdf['总直接成本'].sum())
    _struct = [("总直接成本", float(vdf['总直接成本'].sum()), "#e74c3c"),
               ("总间接成本", float(vdf['总间接成本'].sum()) if '总间接成本' in vdf.columns else 0.0, "#f39c12"),
               ("利润合计", _profit, "#27ae60" if _profit >= 0 else "#c0392b")]
    _struct = [(n, v, c) for n, v, c in _struct if v > 0]
    _smax = max([v for _, v, _ in _struct]) if _struct else 1.0
    _srows = []
    for _n, _v, _c in _struct:
        _w = (_v / _smax * 100) if _smax else 0
        _pct = (_v / _income * 100) if _income else 0
        _srows.append(
            "<tr>"
            f"<td class='l'>{_n}</td>"
            f"<td style='width:140px'>{_ph_fmt(_v)}</td>"
            "<td style='width:260px'>"
            f"<span style='display:inline-block;height:14px;border-radius:4px;"
            f"background:{_c};width:{_w * 2:.1f}px;max-width:200px;"
            f"vertical-align:middle;margin-right:8px'></span>{_pct:.2f}%</td>"
            "</tr>")
    st.markdown(
        "<div class='ph-tb'><table>"
        "<thead><tr><th class='l'>项目</th>"
        "<th style='width:140px'>金额</th>"
        "<th style='width:260px'>占收入比重</th></tr></thead>"
        "<tbody>" + "".join(_srows) + "</tbody></table></div>",
        unsafe_allow_html=True)

    # ==================== 主要费用项均价 ====================
    # ⚠️ 口径修正（2026-09-30）：
    #   原实现用「费用合计 ÷ 该区间全部订单数」算单均，在费用只覆盖部分订单时是错的。
    #   实测 2026年1-8月：鲜花费用仅 19 单产生，却除以 3306 单，单均从 ¥352.63 被
    #   摊薄成 ¥2.03（差 174 倍）；微电影剪辑 700 单 vs 3306 单，¥417.16 → ¥88.33。
    #   现改为「费用合计 ÷ 该费用项实际覆盖的订单数」——分母是"有这项支出的单"，
    #   得到的是"有微电影的单，平均花多少微电影钱"，才是业务上有意义的单价。
    #   完全覆盖的费用项（如拍摄费用 100%）两种算法结果一致，不受影响。
    st.markdown("<div class='ph-sec'>主要费用项均价</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='ph-hint'>均摊单数 = 该费用项实际产生的订单数。"
        "只按「有这项支出的订单」计算单均，不全量摊薄。</div>",
        unsafe_allow_html=True)

    # 费用展示名 → actual_direct_cost.cost_item 原始名
    _AVG_COST_ITEM = {
        '拍摄费用': '拍摄费用',
        '交付费用（场地）': '场地',
        '交付费用（搭建）': '搭建',
        '交付费用（主持）': '主持',
        '鲜花费用': '鲜花费用',
        '微电影拍摄费用': '微电影拍摄费用',
        '二销选片费': '二销选片费',
    }
    # ⚠️ 必须按「全量订单」分摊的费用项（利润表口径下每单都有值，见 profit_engine.py）：
    #   · 微电影剪辑费用：无实际记录时回退到标准成本 → 每单都有值
    #   · 二销选片费：无实际记录时按「二销金额 × 45%」计算 → 每单都有值
    #   这两项若拿实际覆盖单数当分母，会与分子的口径不一致，算出的单均偏高。
    _FORCE_ALL_ORDERS = {'微电影剪辑费用', '二销选片费'}
    # 一次性查出各费用项的覆盖订单数（限定当前月份区间）
    _cover_map = {}
    try:
        _p_start = f"{sel_months[0]}-01"
        _ey, _em = int(sel_months[-1][:4]), int(sel_months[-1][5:7])
        _p_end = (pd.Timestamp(year=_ey, month=_em, day=1)
                  + pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
        _cdb = SessionLocal()
        try:
            _q = (_cdb.query(ActualDirectCost.cost_item,
                             func.count(func.distinct(ActualDirectCost.order_id)))
                  .join(Order, Order.order_id == ActualDirectCost.order_id)
                  .filter(Order.selection_date >= _p_start,
                          Order.selection_date <= _p_end))
            for _it, _cnt in _q.group_by(ActualDirectCost.cost_item).all():
                _cover_map[_it] = int(_cnt)
        finally:
            _cdb.close()
    except Exception:
        _cover_map = {}

    def _cover_of(col_name):
        """该费用项应使用的均摊单数。

        返回 (单数, 是否为部分覆盖)：
          · 全量分摊项（微电影剪辑/二销选片费）→ (全单数, False)
          · 有实际成本记录的项 → (实际覆盖单数, True)
          · 查不到的项 → (全单数, False)
        """
        if col_name in _FORCE_ALL_ORDERS:
            return _orders, False
        _item = _AVG_COST_ITEM.get(col_name)
        if _item and _cover_map.get(_item):
            return _cover_map[_item], True
        return _orders, False

    _avg_src = [('拍摄费用', '摄影师/化妆师/助理团队'),
                ('推广费用（实际）', '线上线下推广投放'),
                ('交付费用（场地）', '婚礼及旅拍场地租赁'),
                ('交付费用（搭建）', '婚礼现场搭建布置'),
                ('交付费用（主持）', '婚礼主持费用'),
                ('鲜花费用', '婚礼鲜花布置'),
                ('微电影拍摄费用', '微电影前期拍摄'),
                ('微电影剪辑费用', '微电影后期剪辑'),
                ('二销选片费', '门店二销结算'),
                ('后期修片费(一销)', '一销后期修片'),
                ('工厂费用（一销）', '相册相框制作（一销）'),
                ('人工成本', '员工薪酬分摊')]
    _arows = []
    for _n, _d in _avg_src:
        if _n not in vdf.columns:
            continue
        _v = float(vdf[_n].sum())
        if _v <= 0:
            continue
        _cov, _is_item = _cover_of(_n)
        _avg = _v / _cov if _cov else 0.0
        # 有明确成本项来源的标注覆盖单数；否则标注为全量（该费用项按订单数分摊）
        _cov_txt = f"{_cov:,} 单" if _is_item else f"{_cov:,} 单·全量"
        _arows.append(
            "<tr>"
            f"<td class='l'>{_n}</td>"
            f"<td style='width:130px'>¥{_avg:,.2f}</td>"
            f"<td style='width:120px;color:#8a9aaa'>{_cov_txt}</td>"
            f"<td style='width:130px'>{_ph_fmt(_v)}</td>"
            f"<td class='l' style='color:#8a9aaa;font-size:12.5px;width:220px'>{_d}</td>"
            "</tr>")
    if _arows:
        st.markdown(
            "<div class='ph-tb'><table>"
            "<thead><tr><th class='l'>费用项</th>"
            "<th style='width:130px'>单均</th>"
            "<th style='width:120px'>均摊单数</th>"
            "<th style='width:130px'>合计</th>"
            "<th class='l' style='width:220px'>说明</th></tr></thead>"
            "<tbody>" + "".join(_arows) + "</tbody></table></div>",
            unsafe_allow_html=True)
    else:
        st.markdown("<div class='ph-hint'>该区间暂无可展示的费用项。</div>",
                    unsafe_allow_html=True)

    # ==================== 拍摄费用明细 ====================
    st.markdown("<div class='ph-sec'>拍摄费用明细</div>", unsafe_allow_html=True)
    _shoot_cols = [('拍摄费用', '拍摄团队费用', '¥'),
                   ('微电影拍摄费用', '微电影拍摄', '¥'),
                   ('微电影剪辑费用', '微电影剪辑', '¥'),
                   ('二销选片费', '二销选片结算', '¥'),
                   ('像素蛋糕修图费', '第三方修图', '¥'),
                   ('后期修片费(一销)', '一销后期修片', '¥'),
                   ('后期修片费(二销)', '二销后期修片', '¥'),
                   ('工厂费用（一销）', '工厂制作（一销）', '¥'),
                   ('工厂费用（二销）', '工厂制作（二销）', '¥')]
    _shrows = []
    _shsum = 0.0
    for _c, _d, _u in _shoot_cols:
        if _c not in vdf.columns:
            continue
        _v = float(vdf[_c].sum())
        _shsum += _v
        # 单均分母：优先用该费用项实际覆盖的订单数（见「主要费用项均价」处口径说明）
        _cov_s, _ = _cover_of(_c)
        # ⚠️ 注意：append 的结果是 None，不能参与字符串拼接。
        #    原先写成 `_shrows.append(...) if _orders else None`，会在 _orders 为真时
        #    把 append 的返回值 None 拼进 HTML，页面上出现 9 个 "None"。
        _shrows.append(
            "<tr>"
            f"<td class='l'>{_d}</td>"
            f"<td style='width:140px'>{_u}{_v:,.2f}</td>"
            f"<td style='width:110px'>¥{(_v / _cov_s) if _cov_s else 0:,.2f}</td>"
            f"<td style='width:110px;color:#8a9aaa'>{_cov_s:,} 单</td>"
            "</tr>")
    if _shrows:
        st.markdown(
            "<div class='ph-tb'><table>"
            "<thead><tr><th class='l'>明细项目</th>"
            "<th style='width:140px'>合计金额</th>"
            "<th style='width:110px'>单均</th>"
            "<th style='width:110px'>均摊单数</th></tr></thead>"
            "<tbody>" + "".join(_shrows) +
            f"<tr style='background:#f8fafc;font-weight:700'>"
            f"<td class='l'>拍摄相关合计</td>"
            f"<td>¥{_shsum:,.2f}</td>"
            f"<td colspan='2'>各费用项单均见上行</td></tr>"
            "</tbody></table></div>",
            unsafe_allow_html=True)

    # ==================== 交付费用明细 ====================
    st.markdown("<div class='ph-sec'>交付费用明细</div>", unsafe_allow_html=True)
    _deliv_cols = [('交付费用（主持）', '婚礼主持费用'),
                   ('交付费用（场地）', '场地租赁（含自租）'),
                   ('交付费用（搭建）', '现场搭建布置'),
                   ('鲜花费用', '鲜花布置及手捧花')]
    _dlrows = []
    _dlsum = 0.0
    for _c, _d in _deliv_cols:
        if _c not in vdf.columns:
            continue
        _v = float(vdf[_c].sum())
        _dlsum += _v
        _cov_d, _ = _cover_of(_c)
        _dlrows.append(
            "<tr>"
            f"<td class='l'>{_d}</td>"
            f"<td style='width:140px'>¥{_v:,.2f}</td>"
            f"<td style='width:110px'>¥{(_v / _cov_d) if _cov_d else 0:,.2f}</td>"
            f"<td style='width:110px;color:#8a9aaa'>{_cov_d:,} 单</td>"
            "</tr>")
    if _dlrows:
        st.markdown(
            "<div class='ph-tb'><table>"
            "<thead><tr><th class='l'>明细项目</th>"
            "<th style='width:140px'>合计金额</th>"
            "<th style='width:110px'>单均</th>"
            "<th style='width:110px'>均摊单数</th></tr></thead>"
            "<tbody>" + "".join(_dlrows) +
            f"<tr style='background:#f8fafc;font-weight:700'>"
            f"<td class='l'>交付费用合计</td>"
            f"<td>¥{_dlsum:,.2f}</td>"
            f"<td colspan='2'>各费用项单均见上行</td></tr>"
            "</tbody></table></div>",
            unsafe_allow_html=True)

    # ==================== 人工成本明细（按部门）====================
    st.markdown("<div class='ph-sec'>人工成本明细</div>", unsafe_allow_html=True)
    _sal_cols = [c for c in vdf.columns
                 if c.endswith('工资') or c in ('人工成本',)]
    _slrows = []
    _slsum = 0.0
    for _c in _sal_cols:
        _v = float(vdf[_c].sum())
        _slsum += _v
        _pct = (_v / _income * 100) if _income else 0
        _slrows.append(
            "<tr>"
            f"<td class='l'>{_c}</td>"
            f"<td style='width:140px'>¥{_v:,.2f}</td>"
            f"<td style='width:110px'>{_pct:.2f}%</td>"
            "</tr>")
    if _slrows:
        st.markdown(
            "<div class='ph-tb'><table>"
            "<thead><tr><th class='l'>部门</th>"
            "<th style='width:140px'>金额</th>"
            "<th style='width:110px'>占收入</th></tr></thead>"
            "<tbody>" + "".join(_slrows) +
            f"<tr style='background:#f8fafc;font-weight:700'>"
            f"<td class='l'>人工成本合计</td>"
            f"<td>¥{_slsum:,.2f}</td>"
            f"<td>{( _slsum / _income * 100) if _income else 0:.2f}%</td></tr>"
            "</tbody></table></div>",
            unsafe_allow_html=True)

    # ==================== 历史快照（保留原功能）====================
    st.markdown("<div class='ph-sec'>历史快照</div>", unsafe_allow_html=True)

    _db = SessionLocal()
    try:
        _snaps = _db.query(ProfitSnapshot).order_by(
            ProfitSnapshot.created_at.desc()).all()
        if not _snaps:
            st.markdown("<div class='ph-hint'>暂无保存的历史快照。"
                        "在「生成利润表」页生成后会在此出现。</div>",
                        unsafe_allow_html=True)
        else:
            _opts = [f"ID:{s.id} | {s.period_label} | {s.filter_option} | "
                     f"{s.created_at.strftime('%Y-%m-%d %H:%M')}" for s in _snaps]
            _sel = st.selectbox("选择历史快照版本", _opts, key='ph_snap')
            if _sel:
                _sid = int(_sel.split('|')[0].split(':')[1])
                _snap = _db.query(ProfitSnapshot).filter_by(id=_sid).first()
                if _snap:
                    _sdf = pd.read_json(io.StringIO(_snap.data_json),
                                        orient='records')
                    if not can_see:
                        _sdf = mask_dataframe(_sdf)
                    _scols = [c for c in _sdf.columns
                              if c not in ['总直接成本', '总间接成本']
                              and not c.endswith('工资')]
                    st.dataframe(_sdf[_scols], width='stretch', height=420)

                    _a, _b = st.columns([1, 1])
                    with _a:
                        _scsv = _sdf.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            "📥 下载该快照 CSV", _scsv,
                            f"利润表_{_snap.period_label}_{_snap.id}.csv",
                            "text/csv", width='stretch')
                    with _b:
                        if st.session_state.role == 'admin':
                            if st.button("🗑 删除此快照", width='stretch'):
                                _db.delete(_snap)
                                _db.commit()
                                add_log(st.session_state.user_id,
                                        st.session_state.username,
                                        "删除利润表快照",
                                        f"ID:{_snap.id} {_snap.period_label}")
                                st.success("已删除")
                                st.rerun()
    finally:
        _db.close()

def profit_report_page():
    st.header("📊 利润表生成与分析")
    module_name = "📊 生成利润表"
    can_see = has_permission(module_name, st.session_state.role)

    today = date.today()
    period_mode = st.radio("时间筛选方式", ["按月", "按年"], horizontal=True, key='period_mode_radio_2')

    # ===== 推广费口径选择（用 selectbox 避免 radio 状态异常） =====
    promo_mode_label = st.selectbox("费用口径", ["实际口径", "分摊口径"], key='promo_mode_selectbox')
    promo_mode = 'actual' if promo_mode_label == "实际口径" else 'allocation'

    if period_mode == "按月":
        months = []
        for y in range(2025, today.year + 1):
            for m in range(1, 13):
                if y == today.year and m > today.month:
                    break
                months.append(f"{y}年{m}月")
        default_idx = [months[-1]] if months else []
        selected_months = st.multiselect("📅 选择月份（可多选）", months, default=default_idx, key='selected_months')
        filter_option = st.radio("显示范围", ["全部", "仅旅拍", "仅婚礼", "仅新疆地区"], horizontal=True, key="filter_option_month")
        if not selected_months:
            st.warning("请至少选择一个月")
            return
        month_parts = []
        for month_str in selected_months:
            parts = month_str.replace('年', ' ').replace('月', '').split()
            y = int(parts[0]); m = int(parts[1])
            month_parts.append((y, m))
        min_year, min_month = min(month_parts)
        max_year, max_month = max(month_parts)
        period_start = date(min_year, min_month, 1)
        last_day = calendar.monthrange(max_year, max_month)[1]
        period_end = date(max_year, max_month, last_day)

        if len(month_parts) == 1:
            period_label = f"{min_year}-{min_month:02d}"
            _render_profit_report(period_start, period_end, filter_option, period_label, min_year, min_month, can_see, promo_mode=promo_mode)
        else:
            period_label = f"{min_year}年{min_month}月-{max_year}年{max_month}月"
            month_list = [f"{y}-{m:02d}" for y, m in month_parts]
            df_multi = generate_profit_report_multi_month(*month_list, promo_mode=promo_mode)
            _render_profit_report(period_start, period_end, filter_option, period_label, None, None, can_see, df_full=df_multi, promo_mode=promo_mode)

    elif period_mode == "按年":
        st.info("按年统计功能开发中，请使用按月筛选。")

def _render_profit_report(period_start, period_end, filter_option, period_month, year, month, can_see, is_week=False, df_full=None, promo_mode='actual'):
    # ==================== 费用列名映射 ====================
    fee_columns = [
        ('推广费用（实际）', '推广费用', '线上线下广告投放、推广活动，已按订单数分摊'),
        ('交付费用（主持）', '交付费用(主持)', '婚礼主持费用，仅婚礼订单产生'),
        ('交付费用（场地）', '交付费用(场地)', '婚礼及旅拍场地租赁费，含自租场地'),
        ('交付费用（搭建）', '交付费用(搭建)', '婚礼现场搭建布置费用'),
        ('鲜花费用', '鲜花费用', '婚礼鲜花布置及手捧花等'),
        ('微电影拍摄费用', '微电影拍摄费用', '微电影前期拍摄团队成本'),
        ('拍摄费用', '拍摄费用', '摄影师、化妆师、助理等团队费用，占比最大'),
        ('二销选片费', '二销选片费', '门店二销结算费用，按选片金额提成'),
        ('像素蛋糕修图费', '像素蛋糕修图费', '自动修图软件像素蛋糕使用费'),
        ('微电影剪辑费用', '微电影剪辑费用', '微电影后期剪辑制作费用'),
        ('后期修片费(一销)', '后期修片费(一销)', '一销部分照片精修成本'),
        ('后期修片费(二销)', '后期修片费(二销)', '二销加修照片额外修片成本'),
        ('工厂费用（一销）', '工厂费用(一销)', '产品制作（相册、摆台等）一销部分'),
        ('工厂费用（二销）', '工厂费用(二销)', '产品制作二销加选部分'),
        ('人工成本', '人工成本', '包含销售部、策划部、运营部、综合部、总经办、售后服务部门工资、AI与数据中心部门工资，按订单数分摊'),
        ('房租、水电、办公费等', '房租、水电、办公费等', '办公场所租赁、物业水电、日常办公消耗'),
        ('税费及手续费', '税费及手续费', '各项税金、支付平台手续费等'),
        ('样片研发', '样片研发', '包含新疆拍样费用、样片研发及模特道具等支出'),
        ('场地铺设费', '场地铺设费', '无法归属到具体订单的公共场地布置费用'),
        ('舆情处理', '舆情处理', '种草推广、舆情维护等费用')
    ]

    with st.spinner("计算中..."):
        if df_full is None:
            df_full = generate_profit_report(period_start.strftime('%Y-%m-%d'), period_end.strftime('%Y-%m-%d'), promo_mode)
        if df_full.empty:
            st.warning("该期间内没有数据")
            return
        df_data = df_full[df_full['业务类型'] != '合计'].copy()

        db = SessionLocal()
        # ---------- 订单数统计（按期间内所有月份汇总）----------
        period_months = []
        current = period_start.replace(day=1)
        while current <= period_end:
            period_months.append(current.strftime('%Y-%m'))
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        # 汇总旅拍、婚礼、新疆下单订单数
        travel_order_cnt = db.query(func.sum(MonthlyStats.order_count)).filter(
            MonthlyStats.period.in_(period_months),
            MonthlyStats.business_type == '旅拍'
        ).scalar() or 0
        wedding_order_cnt = db.query(func.sum(MonthlyStats.order_count)).filter(
            MonthlyStats.period.in_(period_months),
            MonthlyStats.business_type == '婚礼'
        ).scalar() or 0
        xinjiang_order_cnt = db.query(func.sum(MonthlyStats.order_count)).filter(
            MonthlyStats.period.in_(period_months),
            MonthlyStats.business_type == '新疆'
        ).scalar() or 0

        # 去年同期间订单数
        last_year_start = period_start.replace(year=period_start.year - 1)
        last_year_end = period_end.replace(year=period_end.year - 1)
        last_year_period_months = []
        current = last_year_start.replace(day=1)
        while current <= last_year_end:
            last_year_period_months.append(current.strftime('%Y-%m'))
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        travel_order_cnt_last_year = db.query(func.sum(MonthlyStats.order_count)).filter(
            MonthlyStats.period.in_(last_year_period_months),
            MonthlyStats.business_type == '旅拍'
        ).scalar() or 0
        wedding_order_cnt_last_year = db.query(func.sum(MonthlyStats.order_count)).filter(
            MonthlyStats.period.in_(last_year_period_months),
            MonthlyStats.business_type == '婚礼'
        ).scalar() or 0
        xinjiang_order_cnt_last_year = db.query(func.sum(MonthlyStats.order_count)).filter(
            MonthlyStats.period.in_(last_year_period_months),
            MonthlyStats.business_type == '新疆'
        ).scalar() or 0

        # 微电影订单数（本期间）—— 单条 JOIN 查询替换 N+1（避免跨国网络下逐行查询拖垮性能）
        micro_travel_oids = set()
        micro_wedding_oids = set()
        micro_xinjiang_oids = set()
        micro_rows = (
            db.query(ActualDirectCost.order_id, Order.selection_date, Order.set_name, Order.type)
            .join(Order, Order.order_id == ActualDirectCost.order_id)
            .filter(ActualDirectCost.cost_item == '微电影拍摄费用')
            .all()
        )
        for oid, sel_date, set_name, otype in micro_rows:
            if sel_date and period_start <= sel_date <= period_end:
                if '新疆' in (set_name or ''):
                    micro_xinjiang_oids.add(oid)
                if otype == '旅拍':
                    micro_travel_oids.add(oid)
                elif otype == '婚礼':
                    micro_wedding_oids.add(oid)
        micro_travel_cnt = len(micro_travel_oids)
        micro_wedding_cnt = len(micro_wedding_oids)
        micro_xinjiang_cnt = len(micro_xinjiang_oids)

        # 二销选片数（本期间，按业务类型拆分）：做过二销(second_sales>0)的订单数
        erxiao_rows = db.query(Order.order_id, Order.type, Order.set_name).filter(
            Order.second_sales != None, Order.second_sales > 0,
            Order.selection_date >= period_start, Order.selection_date <= period_end
        ).all()
        erxiao_travel_cnt = erxiao_wedding_cnt = erxiao_xinjiang_cnt = 0
        for _oid, _otype, _sname in erxiao_rows:
            # 业务类型与地区是两个独立维度（新疆订单既可能属旅拍、也可能属婚礼），
            # 因此新疆必须独立累加、不与上面互斥，否则新疆口径恒为 0。
            if _otype == '婚礼':
                erxiao_wedding_cnt += 1
            else:
                erxiao_travel_cnt += 1
            if _sname and '新疆' in _sname:
                erxiao_xinjiang_cnt += 1

        def _erxiao_cnt_for_range(_start, _end, _biz_type):
            """任意期间内做过二销(second_sales>0)的订单数，按业务类型口径拆分。
            「全部业务」= 旅拍 + 婚礼（新疆是二者的子集，不重复计入）。"""
            _db = SessionLocal()
            try:
                _rows = _db.query(Order.type, Order.set_name).filter(
                    Order.second_sales != None, Order.second_sales > 0,
                    Order.selection_date >= _start, Order.selection_date <= _end
                ).all()
            finally:
                _db.close()
            _travel = _wedding = _xj = 0
            for _t, _s in _rows:
                if _t == '婚礼':
                    _wedding += 1
                else:
                    _travel += 1
                if _s and '新疆' in _s:
                    _xj += 1
            if _biz_type == '新疆':
                return float(_xj)
            if _biz_type == '旅拍':
                return float(_travel)
            if _biz_type == '婚礼':
                return float(_wedding)
            return float(_travel + _wedding)

        def _area_cnt_for_range(_start, _end, _biz_type):
            """任意期间内产生过「场地」费用（actual_direct_cost.cost_item='场地'）的选片订单数，
            按业务类型口径拆分。用于「交付费用（场地）」均价除数（除数为有场地费的选片订单数，而非全部选品订单数）。
            新疆套系可能属旅拍或婚礼，独立累加不重复计入。

            关键实现说明（避免后人踩坑）：查询必须对「订单号」去重，而不是只对 (业务类型, 套系名) 两列去重。
            若误用 .distinct() 作用在 (Order.type, Order.set_name) 上，SQL 会按「套系种类」去重，
            把 167 个有场地费的旅拍订单压缩成 7 种套系 → 除数变成 7，导致场地费均价被放大约 24 倍（如 ¥150.30→¥3,585.71）。
            正确做法：.query(Order.order_id, Order.type, Order.set_name)...distinct()，
            因 order_id 唯一，三列去重等价于「按订单去重」，计数才是正确的订单数。
            该写法 SQLite 与 PostgreSQL 均兼容，请勿改用 PostgreSQL 专有的 DISTINCT ON（SQLite 不支持）。"""
            _db = SessionLocal()
            try:
                _rows = _db.query(Order.order_id, Order.type, Order.set_name).join(
                    ActualDirectCost, ActualDirectCost.order_id == Order.order_id
                ).filter(
                    ActualDirectCost.cost_item == '场地',
                    Order.selection_date >= _start, Order.selection_date <= _end
                ).distinct().all()
            finally:
                _db.close()
            _travel = _wedding = _xj = 0
            for _oid, _t, _s in _rows:
                if _t == '婚礼':
                    _wedding += 1
                else:
                    _travel += 1
                if _s and '新疆' in _s:
                    _xj += 1
            if _biz_type == '新疆':
                return float(_xj)
            if _biz_type == '旅拍':
                return float(_travel)
            if _biz_type == '婚礼':
                return float(_wedding)
            return float(_travel + _wedding)

        def _wedding_cnt_from_df(_df, _biz_type):
            """婚礼订单数 = 婚礼业务的套系数量合计。
            「全部业务」「婚礼」「新疆」口径均可能含婚礼套系（新疆套系名如「16980新疆婚礼」），
            「旅拍」口径不含婚礼套系，返回 0。"""
            if _df is None or _df.empty or _biz_type not in ('全部业务', '婚礼', '新疆'):
                return 0.0
            if _biz_type == '婚礼':
                return float(_df['套系数量'].sum())
            _col = _df[_df['业务类型'].astype(str).str.strip() == '婚礼']['套系数量']
            return float(_col.sum()) if not _col.empty else 0.0

        def _fmt_div(_v):
            """除数展示：整数值去掉小数点（315.0 → 315），避免出现 xx.0 的别扭显示。"""
            try:
                _f = float(_v)
                return str(int(_f)) if _f == int(_f) else f"{_f:g}"
            except (TypeError, ValueError):
                return str(_v)

        # ---------- 转化率（毛客数 ÷ 订单数）数据准备 ----------
        # 代表月：统一取「期间内第一个月」(period_months[0])。
        # ⚠️ 不要用 period_month 作代表月：它只在单月模式下是 'YYYY-MM'，
        #    多月模式下传入的是展示用标签（如 '2026年1月-2026年8月'），
        #    拿去 _shift_month 做 int() 解析会直接抛 ValueError。
        _conv_rep_month = period_months[0] if period_months else None

        def _shift_month(p, delta):
            """对 'YYYY-MM' 形式的期间字符串平移若干个月，自动处理跨年（如 2026-01 -> 2025-12）。"""
            if not p:
                return None
            _s = str(p).strip()
            _parts = _s.split('-')
            # 非 'YYYY-MM' 形式（如多月展示标签）一律视为无代表月，返回 None，
            # 让下游转化率渲染为「—」而不是整页崩溃。
            if len(_parts) != 2:
                return None
            try:
                y, m = int(_parts[0]), int(_parts[1])
            except (TypeError, ValueError):
                return None
            if not (1 <= m <= 12):
                return None
            total = y * 12 + (m - 1) + delta
            return f"{total // 12}-{total % 12 + 1:02d}"

        def _safe_rate(gross_leads, order_count):
            """安全除法计算转化率：转化率 = 订单数(order_count) ÷ 毛客数(gross_leads)。

            说明（重要）：需求正文公式误写为「毛客数(gross_leads) ÷ 订单数(order_count)」，
            但该写法在真实库下会得到 18785÷1988≈944.9%（转化率不可能 >100%，且与其自带
            示例「1988÷18785≈10.6%」、预期吻合值、以及「订单/毛客」标准语义均矛盾）。
            因此此处按需求「预期吻合」一节给出的数值与真实库字段语义实现：
            转化率 = order_count / gross_leads。
            分母(毛客数)或分子(订单数)为 0 / None 时返回 None（渲染为 —），禁止除零与显示 0 误导。
            """
            if gross_leads is None or order_count is None or gross_leads <= 0 or order_count <= 0:
                return None
            return order_count / gross_leads

        def _fetch_conv(period_str):
            """按期间查询各业务类型的 gross_leads 与 order_count 汇总（单条 GROUP BY 查询）。"""
            if not period_str:
                return {}
            rows = (db.query(MonthlyStats.business_type,
                             func.sum(MonthlyStats.gross_leads),
                             func.sum(MonthlyStats.order_count))
                      .filter_by(period=period_str)
                      .group_by(MonthlyStats.business_type)
                      .all())
            return {bt: (gl or 0, oc or 0) for bt, gl, oc in rows}

        _conv_cur_m = _conv_rep_month
        _conv_last_m = _shift_month(_conv_rep_month, -1)
        _conv_ly_m = _shift_month(_conv_rep_month, -12)

        _conv_raw = {
            'cur': _fetch_conv(_conv_cur_m),
            'last': _fetch_conv(_conv_last_m),
            'ly': _fetch_conv(_conv_ly_m),
        }

        _CONV_BIZ_TYPES = ['旅拍', '婚礼', '新疆']
        conv_df = pd.DataFrame([
            {
                '业务类型': bt,
                '本月转化率': _safe_rate(*_conv_raw['cur'].get(bt, (0, 0))),
                '上月转化率': _safe_rate(*_conv_raw['last'].get(bt, (0, 0))),
                '上年同期转化率': _safe_rate(*_conv_raw['ly'].get(bt, (0, 0))),
            }
            for bt in _CONV_BIZ_TYPES
        ])
        # 格式化：None → 短横 —
        for _col in ['本月转化率', '上月转化率', '上年同期转化率']:
            conv_df[_col] = conv_df[_col].apply(lambda r: f"{r:.1%}" if r is not None else "—")

        db.close()

        if month is not None:
            last_month_start = (period_start - timedelta(days=period_start.day)).replace(day=1)
            last_month_end = period_start - timedelta(days=period_start.day)
        else:
            last_month_start = None
            last_month_end = None

        if filter_option == "全部":
            tabs_to_show = ["全部业务"]
        elif filter_option == "仅旅拍":
            tabs_to_show = ["旅拍"]
        elif filter_option == "仅婚礼":
            tabs_to_show = ["婚礼"]
        elif filter_option == "仅新疆地区":
            tabs_to_show = ["新疆"]
        else:
            tabs_to_show = ["旅拍", "婚礼"]

        tabs = st.tabs(tabs_to_show)

        for idx, biz_type in enumerate(tabs_to_show):
            with tabs[idx]:
                if biz_type == "全部业务":
                    df_biz = df_data
                    total_orders = df_biz['套系数量'].sum()
                    order_cnt = travel_order_cnt + wedding_order_cnt
                    micro_cnt = micro_travel_cnt + micro_wedding_cnt + micro_xinjiang_cnt
                    erxiao_cnt = erxiao_travel_cnt + erxiao_wedding_cnt
                    wedding_cnt = float(df_data[df_data['业务类型']=='婚礼']['套系数量'].sum()) if not df_data[df_data['业务类型']=='婚礼'].empty else 0.0
                    # 「全部业务」口径有场地费订单数 = 旅拍 + 婚礼（_area_cnt_for_range 传入 '全部业务' 即为此语义，不含 set_name 含「新疆」者）。
                    # 必须显式赋值：显示范围单选「全部」时 tabs_to_show 只有 ["全部业务"] 一项，循环仅一轮且无前序赋值可沿用，
                    # 若漏赋值，下游 calc_avg()/final_rows 在 div_type == 'area' 时读取 area_cnt 会直接抛 NameError 整页崩溃。
                    area_cnt = _area_cnt_for_range(period_start, period_end, '全部业务')
                    ly_order_cnt_for_avg = travel_order_cnt_last_year + wedding_order_cnt_last_year
                elif biz_type == "新疆":
                    df_biz = df_data[df_data['套系'].str.contains('新疆', na=False)]
                    total_orders = df_biz['套系数量'].sum()
                    if month is not None:
                        stats_xj = db.query(MonthlyStats).filter_by(period=period_month, business_type='新疆').first()
                        xj_order_cnt = stats_xj.order_count if stats_xj else 0
                    else:
                        xj_order_cnt = db.query(func.sum(MonthlyStats.order_count)).filter(
                            MonthlyStats.period >= period_start.strftime('%Y-%m'),
                            MonthlyStats.period <= period_end.strftime('%Y-%m'),
                            MonthlyStats.business_type == '新疆'
                        ).scalar() or 0
                    order_cnt = xj_order_cnt
                    micro_cnt = micro_xinjiang_cnt
                    erxiao_cnt = erxiao_xinjiang_cnt
                    # 新疆 tab 的「有场地费订单数」必须显式赋值：Python 的 for 循环不产生新作用域，
                    # 若不在此赋值，新疆 tab 会沿用上一轮循环遗留的 area_cnt（如「全部业务」的 244），
                    # 导致新疆场地费均价被除以 244 而非 33，均价严重偏小。
                    area_cnt = _area_cnt_for_range(period_start, period_end, '新疆')
                    # 新疆 tab 可能含「新疆婚礼」套系（实测 2026-08：33 单、搭建费 ¥47,600、场地费 ¥36,364），
                    # 婚礼订单数须取新疆范围内的婚礼套系数量，不能硬编码 0（否则主持/搭建均价被归零）
                    _xj_wedding_rows = df_biz[df_biz['业务类型'].astype(str).str.strip() == '婚礼']
                    wedding_cnt = float(_xj_wedding_rows['套系数量'].sum()) if not _xj_wedding_rows.empty else 0.0
                    ly_order_cnt_for_avg = xinjiang_order_cnt_last_year
                else:
                    df_biz = df_data[df_data['业务类型'] == biz_type]
                    total_orders = df_biz['套系数量'].sum()
                    if biz_type == '旅拍':
                        order_cnt = travel_order_cnt
                        micro_cnt = micro_travel_cnt
                        erxiao_cnt = erxiao_travel_cnt
                        ly_order_cnt_for_avg = travel_order_cnt_last_year
                    else:
                        order_cnt = wedding_order_cnt
                        micro_cnt = micro_wedding_cnt
                        erxiao_cnt = erxiao_wedding_cnt
                        ly_order_cnt_for_avg = wedding_order_cnt_last_year
                    wedding_cnt = total_orders if biz_type == '婚礼' else 0.0
                    area_cnt = _area_cnt_for_range(period_start, period_end, biz_type)

                if df_biz.empty:
                    st.warning(f"{biz_type} 无数据")
                    continue

                total_income = df_biz['总收入'].sum()
                total_direct = df_biz['总直接成本'].sum()
                total_indirect = df_biz['总间接成本'].sum()
                total_profit = df_biz['利润合计'].sum()
                profit_rate = total_profit / total_income if total_income else 0

                col1, col2, col3, col4, col5 = st.columns(5)
                if can_see:
                    col1.metric("💰 总收入", f"¥{total_income/10000:,.2f}万")
                    col2.metric("📉 总直接成本", f"¥{total_direct/10000:,.2f}万")
                    col3.metric("📈 总间接成本", f"¥{total_indirect/10000:,.2f}万")
                    col4.metric("💎 总利润", f"¥{total_profit/10000:,.2f}万", delta=f"{profit_rate:.1%}")
                    col5.metric("📦 订单总数", int(total_orders))
                else:
                    col1.metric("💰 总收入", "***")
                    col2.metric("📉 总直接成本", "***")
                    col3.metric("📈 总间接成本", "***")
                    col4.metric("💎 总利润", "***")
                    col5.metric("📦 订单总数", "***")

                st.subheader(f"📋 {biz_type}详细利润表")
                show_cols = [c for c in df_biz.columns if c not in ['总直接成本', '总间接成本', '工厂费用'] and not c.endswith('工资')]
                if can_see:
                    st.dataframe(df_biz[show_cols], width='stretch')
                else:
                    st.dataframe(mask_dataframe(df_biz[show_cols]), width='stretch')

                # ==================== 均价及同比环比分析 ====================
                with st.expander(f"📈 {biz_type}均价及同比环比分析", expanded=False):
                    # 人工成本除数随口径变化
                    if promo_mode == 'allocation':
                        salary_div_type = 'orders'
                        salary_div_name = '下单订单数'
                        promo_div_type = 'orders'
                        promo_div_name = '下单订单数'
                    else:
                        salary_div_type = 'qty'
                        salary_div_name = '选片订单总数'
                        promo_div_type = 'qty'
                        promo_div_name = '选片订单总数'

                    avg_config = [
                        ('人工成本', salary_div_type, salary_div_name),
                        ('推广费用（实际）', promo_div_type, promo_div_name),
                        ('微电影拍摄费用', 'micro', '微电影订单数'),
                        ('微电影剪辑费用', 'micro', '微电影订单数'),
                        ('拍摄费用', 'qty', '选片订单总数'),
                        ('鲜花费用', 'qty', '选片订单总数'),
                        ('像素蛋糕修图费', 'qty', '选片订单总数'),
                        ('后期修片费(一销)', 'qty', '选片订单总数'),
                        ('后期修片费(二销)', 'erxiao', '二销选片数'),
                        ('工厂费用（一销）', 'qty', '选品订单总数'),
                        ('工厂费用（二销）', 'erxiao', '二销选片数'),
                        ('二销选片费', 'erxiao', '二销选片数'),
                        ('交付费用（主持）', 'wedding', '婚礼订单数'),
                        # 场地费婚礼与旅拍均会产生（旅拍大理/丽江套系实测 168 单 ¥25,099.98），
                        # 故除数用本业务「选片订单总数」，不能沿用「婚礼订单数」（旅拍 tab 下为 0 会整行归零）
                        # 场地费仅部分订单产生，除数用「有场地费的选片订单数」（按业务类型口径拆分），不能沿用全量选品订单总数
                        ('交付费用（场地）', 'area', '有场地费订单数'),
                        ('交付费用（搭建）', 'wedding', '婚礼订单数'),
                    ]

                    def calc_avg(item, div_type):
                        if item not in df_biz.columns:
                            return 0
                        total = df_biz[item].sum()
                        if div_type == 'orders':
                            divisor = order_cnt
                        elif div_type == 'erxiao':
                            divisor = erxiao_cnt
                        elif div_type == 'wedding':
                            divisor = wedding_cnt
                        elif div_type == 'micro':
                            divisor = micro_cnt
                        elif div_type == 'area':
                            divisor = area_cnt
                        else:
                            divisor = total_orders
                        return total / divisor if divisor else 0

                    # 注：本表最终数据统一由下方 final_rows → df_avg 生成（页面展示与导出共用），
                    # 此前这里另有一份未被使用的 rows 构建代码，已删除以免两处口径不一致。

                    # 去年同期
                    df_ly = generate_profit_report(last_year_start.strftime('%Y-%m-%d'), last_year_end.strftime('%Y-%m-%d'), promo_mode)
                    ly_avgs = {}
                    ly_erxiao_cnt = 0.0
                    ly_wedding_cnt = 0.0
                    ly_area_cnt = 0.0
                    if not df_ly.empty and ly_order_cnt_for_avg > 0:
                        df_ly_data = df_ly[df_ly['业务类型'] != '合计'].copy()
                        df_ly_data['业务类型'] = df_ly_data['业务类型'].str.strip()
                        if biz_type == "全部业务":
                            df_ly_biz = df_ly_data
                        elif biz_type == "新疆":
                            df_ly_biz = df_ly_data[df_ly_data['套系'].str.contains('新疆', na=False)]
                        else:
                            df_ly_biz = df_ly_data[df_ly_data['业务类型'] == biz_type]
                        ly_total_orders = df_ly_biz['套系数量'].sum() if not df_ly_biz.empty else 1
                        ly_micro_cnt = ly_total_orders
                        ly_erxiao_cnt = _erxiao_cnt_for_range(last_year_start, last_year_end, biz_type)
                        ly_wedding_cnt = _wedding_cnt_from_df(df_ly_biz, biz_type)
                        ly_area_cnt = _area_cnt_for_range(last_year_start, last_year_end, biz_type)
                        for item, div_type, _ in avg_config:
                            if item not in df_ly_biz.columns:
                                continue
                            total_ly = df_ly_biz[item].sum()
                            if div_type == 'orders':
                                divisor = ly_order_cnt_for_avg if ly_order_cnt_for_avg else 1
                            elif div_type == 'erxiao':
                                divisor = ly_erxiao_cnt if ly_erxiao_cnt else 1
                            elif div_type == 'wedding':
                                divisor = ly_wedding_cnt if ly_wedding_cnt else 1
                            elif div_type == 'micro':
                                divisor = ly_micro_cnt if ly_micro_cnt else 1
                            elif div_type == 'area':
                                divisor = ly_area_cnt if ly_area_cnt else 1
                            else:
                                divisor = ly_total_orders if ly_total_orders else 1
                            ly_avgs[item] = total_ly / divisor

                    # 上月同期（环比）
                    lm_avgs = {}
                    lm_divisor_info = {}
                    lm_erxiao_cnt = 0.0
                    lm_wedding_cnt = 0.0
                    lm_area_cnt = 0.0
                    if last_month_start and last_month_end:
                        df_lm = generate_profit_report(last_month_start.strftime('%Y-%m-%d'), last_month_end.strftime('%Y-%m-%d'), promo_mode)
                        if not df_lm.empty:
                            df_lm_data = df_lm[df_lm['业务类型'] != '合计'].copy()
                            df_lm_data['业务类型'] = df_lm_data['业务类型'].str.strip()
                            if biz_type == "全部业务":
                                df_lm_biz = df_lm_data
                            elif biz_type == "新疆":
                                df_lm_biz = df_lm_data[df_lm_data['套系'].str.contains('新疆', na=False)]
                            else:
                                df_lm_biz = df_lm_data[df_lm_data['业务类型'] == biz_type]

                            lm_qty = df_lm_biz['套系数量'].sum() if not df_lm_biz.empty else 1
                            lm_erxiao_cnt = _erxiao_cnt_for_range(last_month_start, last_month_end, biz_type)
                            lm_wedding_cnt = _wedding_cnt_from_df(df_lm_biz, biz_type)
                            lm_area_cnt = _area_cnt_for_range(last_month_start, last_month_end, biz_type)

                            lm_month_str = last_month_start.strftime('%Y-%m')
                            db_lm = SessionLocal()
                            try:
                                if biz_type == "全部业务":
                                    lm_travel = db_lm.query(MonthlyStats).filter_by(period=lm_month_str, business_type='旅拍').first()
                                    lm_wedding = db_lm.query(MonthlyStats).filter_by(period=lm_month_str, business_type='婚礼').first()
                                    lm_orders = (lm_travel.order_count if lm_travel else 0) + (lm_wedding.order_count if lm_wedding else 0)
                                elif biz_type == "新疆":
                                    lm_xj = db_lm.query(MonthlyStats).filter_by(period=lm_month_str, business_type='新疆').first()
                                    lm_orders = lm_xj.order_count if lm_xj else 0
                                elif biz_type == "旅拍":
                                    lm_travel = db_lm.query(MonthlyStats).filter_by(period=lm_month_str, business_type='旅拍').first()
                                    lm_orders = lm_travel.order_count if lm_travel else 0
                                else:
                                    lm_wedding = db_lm.query(MonthlyStats).filter_by(period=lm_month_str, business_type='婚礼').first()
                                    lm_orders = lm_wedding.order_count if lm_wedding else 0
                            finally:
                                db_lm.close()

                            db_lm_micro = SessionLocal()
                            try:
                                micro_records_lm = db_lm_micro.query(ActualDirectCost.order_id).filter(
                                    ActualDirectCost.cost_item == '微电影拍摄费用'
                                ).distinct().all()
                                micro_oids_lm = [r[0] for r in micro_records_lm]
                                if micro_oids_lm:
                                    if biz_type == "全部业务":
                                        lm_micro_orders = db_lm_micro.query(Order).filter(
                                            Order.order_id.in_(micro_oids_lm),
                                            Order.selection_date >= last_month_start,
                                            Order.selection_date <= last_month_end
                                        ).count()
                                    elif biz_type == "新疆":
                                        lm_micro_orders = db_lm_micro.query(Order).filter(
                                            Order.order_id.in_(micro_oids_lm),
                                            Order.set_name.contains('新疆'),
                                            Order.selection_date >= last_month_start,
                                            Order.selection_date <= last_month_end
                                        ).count()
                                    else:
                                        lm_micro_orders = db_lm_micro.query(Order).filter(
                                            Order.order_id.in_(micro_oids_lm),
                                            Order.type == biz_type,
                                            Order.selection_date >= last_month_start,
                                            Order.selection_date <= last_month_end
                                        ).count()
                                else:
                                    lm_micro_orders = 0
                            finally:
                                db_lm_micro.close()

                            for item, div_type, div_name in avg_config:
                                if item not in df_lm_biz.columns:
                                    continue
                                total_lm = df_lm_biz[item].sum()
                                if div_type == 'orders':
                                    divisor = lm_orders
                                elif div_type == 'erxiao':
                                    divisor = lm_erxiao_cnt
                                elif div_type == 'wedding':
                                    divisor = lm_wedding_cnt
                                elif div_type == 'micro':
                                    divisor = lm_micro_orders
                                elif div_type == 'area':
                                    divisor = lm_area_cnt
                                else:
                                    divisor = lm_qty
                                divisor_label = f"{div_name}: {_fmt_div(divisor)}"
                                lm_avgs[item] = total_lm / divisor if divisor else 0
                                lm_divisor_info[item] = divisor_label

                    final_rows = []
                    for idx_row, (item, div_type, div_name) in enumerate(avg_config):
                        curr_avg = calc_avg(item, div_type)
                        divisor_val = (
                            order_cnt if div_type == 'orders'
                            else erxiao_cnt if div_type == 'erxiao'
                            else wedding_cnt if div_type == 'wedding'
                            else micro_cnt if div_type == 'micro'
                            else area_cnt if div_type == 'area'
                            else total_orders
                        )
                        row = [item, f"¥{curr_avg:,.2f}", f"{div_name}: {_fmt_div(divisor_val)}"]

                        ly_avg = ly_avgs.get(item, None)
                        if ly_avg is not None:
                            diff = curr_avg - ly_avg
                            pct = diff / ly_avg * 100 if ly_avg else 0
                            row.extend([f"¥{ly_avg:,.2f}", f"{diff:+,.2f}", f"{pct:+.1f}%"])
                        else:
                            row.extend(["无数据", "—", "—"])
                        if last_month_start and last_month_end:
                            lm_avg = lm_avgs.get(item, None)
                            if lm_avg is not None:
                                diff_m = curr_avg - lm_avg
                                pct_m = diff_m / lm_avg * 100 if lm_avg else 0
                                lm_div_label = lm_divisor_info.get(item, "—")
                                row.extend([f"¥{lm_avg:,.2f}", f"{lm_div_label}", f"{diff_m:+,.2f}", f"{pct_m:+.1f}%"])
                            else:
                                row.extend(["无数据", "—", "—", "—"])
                        final_rows.append(row)

                    cols = ['费用项', '本期均价', '除数', '去年同期均价', '同比差异', '同比变化']
                    if last_month_start and last_month_end:
                        cols += ['上月均价', '上月除数', '环比差异', '环比变化']
                    df_avg = pd.DataFrame(final_rows, columns=cols)
                    if not can_see:
                        df_avg = mask_dataframe(df_avg)
                    st.table(df_avg)

                # ==================== 拍摄费用明细分析（含同比、环比） ====================
                st.divider()
                with st.expander(f"📸 {biz_type}拍摄费用明细分析（含同比）", expanded=False):
                    st.caption("💡 看表须知：这里的「均价」是把这项费用平摊到当月所有订单上算出来的，没产生这笔费用的订单也算进去了。"
                               "所以订单变多、发生这笔费用的订单变少，均价都会自然变小 —— 看到涨跌不能直接当成涨价或降价，"
                               "建议对照页面顶部的订单总数一同判断。")
                    db2 = SessionLocal()
                    if biz_type == "全部业务":
                        biz_orders = db2.query(Order.order_id).filter(
                            Order.selection_date >= period_start,
                            Order.selection_date <= period_end
                        ).all()
                    elif biz_type == '新疆':
                        biz_orders = db2.query(Order.order_id).filter(
                            Order.set_name.contains('新疆'),
                            Order.selection_date >= period_start,
                            Order.selection_date <= period_end
                        ).all()
                    else:
                        biz_orders = db2.query(Order.order_id).filter(
                            Order.type == biz_type,
                            Order.selection_date >= period_start,
                            Order.selection_date <= period_end
                        ).all()
                    biz_order_ids = [o[0] for o in biz_orders]
                    shoot_records = db2.query(ActualDirectCost).filter(
                        ActualDirectCost.cost_item == '拍摄费用',
                        ActualDirectCost.order_id.in_(biz_order_ids)
                    ).all()
                    if last_month_start and last_month_end:
                        if biz_type == "全部业务":
                            lm_orders = db2.query(Order.order_id).filter(
                                Order.selection_date >= last_month_start,
                                Order.selection_date <= last_month_end
                            ).all()
                        elif biz_type == '新疆':
                            lm_orders = db2.query(Order.order_id).filter(
                                Order.set_name.contains('新疆'),
                                Order.selection_date >= last_month_start,
                                Order.selection_date <= last_month_end
                            ).all()
                        else:
                            lm_orders = db2.query(Order.order_id).filter(
                                Order.type == biz_type,
                                Order.selection_date >= last_month_start,
                                Order.selection_date <= last_month_end
                            ).all()
                        lm_order_ids = [o[0] for o in lm_orders]
                        lm_shoot_records = db2.query(ActualDirectCost).filter(
                            ActualDirectCost.cost_item == '拍摄费用',
                            ActualDirectCost.order_id.in_(lm_order_ids)
                        ).all()
                    else:
                        lm_shoot_records = []
                    db2.close()

                    def build_detail_rows(records, order_count):
                        detail_sums = {}
                        for rec in records:
                            if rec.remark and '||解析:' in rec.remark:
                                parts = rec.remark.split('||解析:')
                                if len(parts) > 1:
                                    parsed = parts[1]
                                    for item in parsed.split('; '):
                                        if ':' in item:
                                            key, val_str = item.split(':', 1)
                                            try:
                                                val = float(val_str)
                                                detail_sums[key] = detail_sums.get(key, 0) + val
                                            except: pass
                            else:
                                detail_sums['其他'] = detail_sums.get('其他', 0) + rec.amount
                        total = sum(rec.amount for rec in records)
                        parsed_total = sum(detail_sums.values())
                        if parsed_total != total:
                            diff = total - parsed_total
                            if '摄化费用' in detail_sums:
                                detail_sums['摄化费用'] += diff
                            else:
                                detail_sums['摄化费用'] = diff
                        avg_dict = {k: v / order_count if order_count else 0 for k, v in detail_sums.items()}
                        return avg_dict, total

                    curr_detail, curr_total = build_detail_rows(shoot_records, total_orders)
                    curr_shoot_avg = curr_total / total_orders if total_orders else 0

                    lm_detail, lm_total = {}, 0
                    if last_month_start and last_month_end:
                        lm_order_count = len(lm_orders) if lm_orders else 0
                        if lm_order_count > 0:
                            lm_detail, lm_total = build_detail_rows(lm_shoot_records, lm_order_count)
                            lm_shoot_avg = lm_total / lm_order_count
                        else:
                            lm_shoot_avg = None
                    else:
                        lm_shoot_avg = None

                    ly_detail, ly_total = {}, 0
                    ly_order_count = 0
                    if last_year_start.year >= 2025:
                        db3 = SessionLocal()
                        if biz_type == "全部业务":
                            ly_orders = db3.query(Order).filter(
                                Order.selection_date >= last_year_start,
                                Order.selection_date <= last_year_end
                            ).all()
                        elif biz_type == '新疆':
                            ly_orders = db3.query(Order).filter(
                                Order.selection_date >= last_year_start,
                                Order.selection_date <= last_year_end,
                                Order.set_name.contains('新疆')
                            ).all()
                        else:
                            ly_orders = db3.query(Order).filter(
                                Order.type == biz_type,
                                Order.selection_date >= last_year_start,
                                Order.selection_date <= last_year_end
                            ).all()
                        ly_order_ids = [o.order_id for o in ly_orders]
                        ly_records = db3.query(ActualDirectCost).filter(
                            ActualDirectCost.cost_item == '拍摄费用',
                            ActualDirectCost.order_id.in_(ly_order_ids)
                        ).all()
                        ly_order_count = len(ly_orders)
                        if ly_order_count > 0:
                            ly_detail, ly_total = build_detail_rows(ly_records, ly_order_count)
                            ly_shoot_avg = ly_total / ly_order_count
                        else:
                            ly_shoot_avg = None
                        db3.close()
                    else:
                        ly_shoot_avg = None

                    all_keys = sorted(set(list(curr_detail.keys()) + list(lm_detail.keys()) + list(ly_detail.keys())))
                    detail_rows = []
                    for key in all_keys:
                        curr_avg = curr_detail.get(key, 0)
                        ly_avg = ly_detail.get(key, None)
                        lm_avg = lm_detail.get(key, None)
                        row = [key, f"¥{curr_avg:,.2f}"]
                        if ly_avg is not None:
                            diff_ly = curr_avg - ly_avg
                            pct_ly = diff_ly / ly_avg * 100 if ly_avg else 0
                            row.extend([f"¥{ly_avg:,.2f}", f"{diff_ly:+,.2f}", f"{pct_ly:+.1f}%"])
                        else:
                            row.extend(["无数据", "—", "—"])
                        if lm_avg is not None:
                            diff_lm = curr_avg - lm_avg
                            pct_lm = diff_lm / lm_avg * 100 if lm_avg else 0
                            row.extend([f"¥{lm_avg:,.2f}", f"{diff_lm:+,.2f}", f"{pct_lm:+.1f}%"])
                        else:
                            row.extend(["无数据", "—", "—"])
                        detail_rows.append(row)

                    total_row = ['📌 拍摄费用均价', f"¥{curr_shoot_avg:,.2f}"]
                    if ly_shoot_avg is not None:
                        diff_total_ly = curr_shoot_avg - ly_shoot_avg
                        pct_total_ly = diff_total_ly / ly_shoot_avg * 100 if ly_shoot_avg else 0
                        total_row.extend([f"¥{ly_shoot_avg:,.2f}", f"{diff_total_ly:+,.2f}", f"{pct_total_ly:+.1f}%"])
                    else:
                        total_row.extend(["无数据", "—", "—"])
                    if lm_shoot_avg is not None:
                        diff_total_lm = curr_shoot_avg - lm_shoot_avg
                        pct_total_lm = diff_total_lm / lm_shoot_avg * 100 if lm_shoot_avg else 0
                        total_row.extend([f"¥{lm_shoot_avg:,.2f}", f"{diff_total_lm:+,.2f}", f"{pct_total_lm:+.1f}%"])
                    else:
                        total_row.extend(["无数据", "—", "—"])
                    detail_rows.append(total_row)

                    detail_cols = ['明细项目', '本期均价', '去年同期均价', '同比差异', '同比变化',
                                   '上月均价', '环比差异', '环比变化']
                    detail_df = pd.DataFrame(detail_rows, columns=detail_cols)
                    if not can_see:
                        detail_df = mask_dataframe(detail_df)
                    st.table(detail_df)

                # ==================== 人工成本分析（含同比、环比） ====================
                with st.expander(f"💼 {biz_type}人工成本分析（部门均价同比）", expanded=False):
                    salary_cols = [c for c in df_biz.columns if c.endswith('工资')]
                    if salary_cols and order_cnt > 0:
                        curr_salary = {}
                        for col in salary_cols:
                            total_s = df_biz[col].sum()
                            # 分摊口径：除以下单订单数；实际口径：除以选片订单数
                            if promo_mode == 'allocation':
                                avg_s = total_s / order_cnt if order_cnt else 0
                            else:
                                avg_s = total_s / total_orders if total_orders else 0
                            curr_salary[col.replace('工资', '')] = avg_s
                        curr_total_avg = sum(curr_salary.values())
                        curr_salary['合计'] = curr_total_avg

                        ly_salary = {}
                        if last_year_start.year >= 2025:
                            if biz_type == '旅拍':
                                ly_order_cnt = travel_order_cnt_last_year
                            elif biz_type == '婚礼':
                                ly_order_cnt = wedding_order_cnt_last_year
                            else:
                                ly_order_cnt = travel_order_cnt_last_year + wedding_order_cnt_last_year
                            if ly_order_cnt == 0:
                                ly_order_cnt = 1
                            db_ly = SessionLocal()
                            df_ly = generate_profit_report(last_year_start.strftime('%Y-%m-%d'), last_year_end.strftime('%Y-%m-%d'), promo_mode)
                            db_ly.close()
                            if not df_ly.empty and ly_order_cnt > 0:
                                df_ly_data = df_ly[df_ly['业务类型'] != '合计'].copy()
                                if biz_type == "全部业务":
                                    df_ly_biz = df_ly_data
                                elif biz_type == "新疆":
                                    df_ly_biz = df_ly_data[df_ly_data['套系'].str.contains('新疆', na=False)]
                                else:
                                    df_ly_biz = df_ly_data[df_ly_data['业务类型'] == biz_type]
                                for col in salary_cols:
                                    if col in df_ly_biz.columns:
                                        total_ly = df_ly_biz[col].sum()
                                        if promo_mode == 'allocation':
                                            avg_ly = total_ly / ly_order_cnt if ly_order_cnt else 0
                                        else:
                                            ly_total_orders = df_ly_biz['套系数量'].sum() if not df_ly_biz.empty else 1
                                            avg_ly = total_ly / ly_total_orders if ly_total_orders else 0
                                        ly_salary[col.replace('工资', '')] = avg_ly
                                    else:
                                        ly_salary[col.replace('工资', '')] = None
                                if ly_salary:
                                    ly_total_avg = sum(v for v in ly_salary.values() if v is not None)
                                    ly_salary['合计'] = ly_total_avg
                            else:
                                ly_salary = None

                        lm_salary = {}
                        if last_month_start and last_month_end:
                            lm_month_str = last_month_start.strftime('%Y-%m')
                            db_lm_stats = SessionLocal()
                            try:
                                if biz_type == "全部业务":
                                    lm_travel = db_lm_stats.query(MonthlyStats).filter_by(period=lm_month_str, business_type='旅拍').first()
                                    lm_wedding = db_lm_stats.query(MonthlyStats).filter_by(period=lm_month_str, business_type='婚礼').first()
                                    lm_order_cnt = (lm_travel.order_count if lm_travel else 0) + (lm_wedding.order_count if lm_wedding else 0)
                                elif biz_type == "新疆":
                                    lm_xj = db_lm_stats.query(MonthlyStats).filter_by(period=lm_month_str, business_type='新疆').first()
                                    lm_order_cnt = lm_xj.order_count if lm_xj else 0
                                elif biz_type == "旅拍":
                                    lm_travel = db_lm_stats.query(MonthlyStats).filter_by(period=lm_month_str, business_type='旅拍').first()
                                    lm_order_cnt = lm_travel.order_count if lm_travel else 0
                                else:
                                    lm_wedding = db_lm_stats.query(MonthlyStats).filter_by(period=lm_month_str, business_type='婚礼').first()
                                    lm_order_cnt = lm_wedding.order_count if lm_wedding else 0
                            finally:
                                db_lm_stats.close()

                            df_lm = generate_profit_report(last_month_start.strftime('%Y-%m-%d'), last_month_end.strftime('%Y-%m-%d'), promo_mode)
                            if not df_lm.empty:
                                df_lm_data = df_lm[df_lm['业务类型'] != '合计'].copy()
                                if biz_type == "全部业务":
                                    df_lm_biz = df_lm_data
                                elif biz_type == "新疆":
                                    df_lm_biz = df_lm_data[df_lm_data['套系'].str.contains('新疆', na=False)]
                                else:
                                    df_lm_biz = df_lm_data[df_lm_data['业务类型'] == biz_type]
                                for col in salary_cols:
                                    if col in df_lm_biz.columns:
                                        total_lm = df_lm_biz[col].sum()
                                        if promo_mode == 'allocation':
                                            avg_lm = total_lm / lm_order_cnt if lm_order_cnt else 0
                                        else:
                                            lm_total_orders = df_lm_biz['套系数量'].sum() if not df_lm_biz.empty else 1
                                            avg_lm = total_lm / lm_total_orders if lm_total_orders else 0
                                        lm_salary[col.replace('工资', '')] = avg_lm
                                    else:
                                        lm_salary[col.replace('工资', '')] = None
                                if lm_salary:
                                    lm_total_avg = sum(v for v in lm_salary.values() if v is not None)
                                    lm_salary['合计'] = lm_total_avg

                        rows = []
                        all_depts = sorted(set(list(curr_salary.keys()) + list(ly_salary.keys() if ly_salary else []) + list(lm_salary.keys() if lm_salary else [])))
                        # 让"合计"始终排在最后
                        if '合计' in all_depts:
                            all_depts.remove('合计')
                            all_depts.append('合计')
                        for dept in all_depts:
                            curr_avg = curr_salary.get(dept, 0)
                            ly_avg = ly_salary.get(dept, None) if ly_salary else None
                            lm_avg = lm_salary.get(dept, None) if lm_salary else None
                            row = [dept, f"¥{curr_avg:,.2f}"]
                            if ly_avg is not None:
                                diff_ly = curr_avg - ly_avg
                                pct_ly = diff_ly / ly_avg * 100 if ly_avg else 0
                                row.extend([f"¥{ly_avg:,.2f}", f"{diff_ly:+,.2f}", f"{pct_ly:+.1f}%"])
                            else:
                                row.extend(["无数据", "—", "—"])
                            if lm_avg is not None:
                                diff_lm = curr_avg - lm_avg
                                pct_lm = diff_lm / lm_avg * 100 if lm_avg else 0
                                row.extend([f"¥{lm_avg:,.2f}", f"{diff_lm:+,.2f}", f"{pct_lm:+.1f}%"])
                            else:
                                row.extend(["无数据", "—", "—"])
                            rows.append(row)

                        labor_cols = ['部门', '本期均价', '去年同期均价', '同比差异', '同比变化',
                                      '上月均价', '环比差异', '环比变化']
                        salary_df = pd.DataFrame(rows, columns=labor_cols)
                        if not can_see:
                            salary_df = mask_dataframe(salary_df)
                        if promo_mode == 'allocation':
                            st.markdown(f"**{biz_type}人工成本均价同比（分摊口径，除数：下单订单数 {order_cnt}）**")
                        else:
                            st.markdown(f"**{biz_type}人工成本均价同比（实际口径，除数：选片订单总数 {int(total_orders)}）**")
                        st.table(salary_df)
                    else:
                        st.info(f"{biz_type}无人工成本数据或下单订单数为0")
                        salary_df = pd.DataFrame()


                    # ===== 工资变化归因分析 =====
                    st.divider()
                    st.markdown("##### 🔍 工资变化归因分析")
                    db_chk = SessionLocal()
                    try:
                        has_emp_data = db_chk.query(EmployeeSalary).count() > 0
                    finally:
                        db_chk.close()

                    if not has_emp_data:
                        st.info("尚未导入员工工资清单，无法进行归因分析。请先到「👥 员工工资管理」导入。")
                    else:
                        # 代表月统一取期间首月 'YYYY-MM'（与会话内 _conv_rep_month 同口径）。
                        # 不可用 period_month：多月模式下它是展示标签（如 '2026年1月-2026年8月'），
                        # 传给 analyze_salary_change 后 filter_by(period=...) 纯字符串匹配查不到数据，
                        # 会显示 ¥0 / 0 人的误导结果。
                        curr_p = period_months[0] if period_months else period_start.strftime('%Y-%m')
                        for label, base_p in [
                            ('环比（对比上月）', last_month_start.strftime('%Y-%m') if last_month_start else None),
                            ('同比（对比去年同期）', f"{last_year_start.year}-{last_year_start.month:02d}" if last_year_start else None),
                        ]:
                            if not base_p:
                                continue
                            try:
                                r = analyze_salary_change(curr_p, base_p)
                            except Exception as e:
                                st.warning(f"{label} 数据不足：{e}")
                                continue

                            with st.expander(f"📊 {label}（{curr_p} vs {base_p}）", expanded=False):
                                c1, c2, c3 = st.columns(3)
                                c1.metric("本期总工资", f"¥{r['curr_total']:,.0f} · {r['curr_count']}人")
                                c2.metric("对比期总工资", f"¥{r['base_total']:,.0f} · {r['base_count']}人")
                                delta = r['total_change']
                                pct = delta / r['base_total'] * 100 if r['base_total'] else 0
                                c3.metric("变化", f"¥{delta:+,.0f}", f"{pct:+.1f}%")

                                st.markdown(f"""
**📌 变化归因：**

1️⃣ **人员增减影响**：
- 新增 **{len(r['new_employees'])}** 人（贡献 ¥{r['new_contribution']:+,.0f}）
- 离职 **{len(r['left_employees'])}** 人（影响 ¥{r['left_contribution']:+,.0f}）
- 人员净影响：**¥{r['new_contribution'] + r['left_contribution']:+,.0f}**

2️⃣ **存量员工工资变化**（{r['curr_count'] - len(r['new_employees'])} 人）：
- 基本工资（调薪）：¥{r['staying_base_change']:+,.0f}
- **提成（业绩相关）：¥{r['staying_comm_change']:+,.0f}** ← 重点
- 绩效：¥{r['staying_perf_change']:+,.0f}
- 岗位薪资/补助：¥{r['staying_position_change']:+,.0f}
- 社保补贴：¥{r['staying_social_subsidy']:+,.0f}
- 加班补贴：¥{r['staying_overtime']:+,.0f}
- 社保公司部分：¥{r['staying_company_social']:+,.0f}
- 个税扣款：¥{r['staying_tax']:+,.0f}
- 保险扣款：¥{r['staying_insurance']:+,.0f}
- 罚款：¥{r['staying_fine']:+,.0f}
- **缺勤/迟到/未打卡等：¥{r['staying_absence']:+,.0f}** 
- 存量员工净影响：**¥{r['staying_base_change'] + r['staying_comm_change'] + r['staying_perf_change'] + r['staying_position_change'] + r['staying_social_subsidy'] + r['staying_overtime'] + r['staying_company_social'] + r['staying_tax'] + r['staying_insurance'] + r['staying_fine'] + r['staying_absence']:+,.0f}**
""")

                                if r['dept_changes']:
                                    df_dept = pd.DataFrame(r['dept_changes'])
                                    df_dept = df_dept.sort_values('合计变化', key=lambda x: x.abs(), ascending=False)
                                    st.markdown("**🏢 部门维度归因：**")
                                    st.dataframe(df_dept.style.format({
                                        '基本工资变化': '{:+,.0f}', '提成变化': '{:+,.0f}',
                                        '绩效变化': '{:+,.0f}', '岗位薪资变化': '{:+,.0f}',
                                        '社保补贴变化': '{:+,.0f}', '加班补贴变化': '{:+,.0f}',
                                        '社保公司部分变化': '{:+,.0f}', '个税扣款变化': '{:+,.0f}',
                                        '保险扣款变化': '{:+,.0f}', '罚款变化': '{:+,.0f}',
                                        '缺勤/迟到等变化': '{:+,.0f}', '合计变化': '{:+,.0f}',
                                    }), width='stretch')

                                    # ===== 销售部特殊提示 =====
                                    sales_depts = [d for d in r['dept_changes'] if d['部门'] and '销售' in d['部门']]
                                    if sales_depts:
                                        total_sales_base = sum(s['基本工资变化'] for s in sales_depts)
                                        total_sales_comm = sum(s['提成变化'] for s in sales_depts)
                                        st.warning(f"""
⚠️ **销售部基本工资变化说明**

销售部底薪由「**固定底薪 + 订单率底薪 + 转化率底薪**」组成。

本期销售部合计基本工资变化：**¥{total_sales_base:+,.0f}**

该变动**不一定是调薪**，可能部分来自：
- 📈 **订单率浮动**（订单达成率带来的底薪变化）
- 📈 **转化率浮动**（线索转化率带来的底薪变化）
- 💰 **真实调薪**（固定底薪部分变化）

请结合提成变化 **¥{total_sales_comm:+,.0f}** 一并解读。若需精确区分"调薪" vs "业绩浮动"，建议后续在工资表中增加「固定底薪 / 订单率底薪 / 转化率底薪」三列。
""")

                                if r['emp_changes']:
                                    df_chg = pd.DataFrame(r['emp_changes'])
                                    df_chg['绝对变化'] = df_chg['合计变化'].abs()
                                    top = df_chg.sort_values('绝对变化', ascending=False).head(15)
                                    cols_show = ['员工', '部门', '类型', '基本工资变化', '提成变化',
                                                 '绩效变化', '岗位薪资变化', '社保补贴变化', '加班补贴变化',
                                                 '社保公司部分变化', '个税扣款变化', '保险扣款变化',
                                                 '罚款变化', '缺勤/迟到等变化', '合计变化']
                                    cols_show = [c for c in cols_show if c in top.columns]
                                    st.markdown("**👤 变化最大的员工 TOP 15：**")
                                    st.caption("说明：销售部员工「基本工资变化」包含订单率/转化率浮动，其他部门默认为调薪。")
                                    if can_see:
                                        st.dataframe(top[cols_show].style.format({
                                            '基本工资变化': '{:+,.0f}', '提成变化': '{:+,.0f}',
                                            '绩效变化': '{:+,.0f}', '岗位薪资变化': '{:+,.0f}',
                                            '社保补贴变化': '{:+,.0f}', '加班补贴变化': '{:+,.0f}',
                                            '社保公司部分变化': '{:+,.0f}', '个税扣款变化': '{:+,.0f}',
                                            '保险扣款变化': '{:+,.0f}', '罚款变化': '{:+,.0f}',
                                            '缺勤/迟到等变化': '{:+,.0f}', '合计变化': '{:+,.0f}',
                                        }), width='stretch')
                                    else:
                                        st.dataframe(mask_dataframe(top[cols_show]), width='stretch')

                                total_staying = (r['staying_base_change'] + r['staying_comm_change']
                                                 + r['staying_perf_change'] + r['staying_position_change']
                                                 + r['staying_social_subsidy'] + r['staying_overtime']
                                                 + r['staying_company_social'] + r['staying_tax']
                                                 + r['staying_insurance'] + r['staying_fine']
                                                 + r['staying_absence'])
                                st.markdown(f"""
**💡 快速结论：**
- 人员变动影响占总变化：**{abs(r['new_contribution'] + r['left_contribution']) / abs(r['total_change']) * 100 if r['total_change'] else 0:.1f}%**
- 存量员工变动影响占总变化：**{abs(total_staying) / abs(r['total_change']) * 100 if r['total_change'] else 0:.1f}%**
""")

                # 保存当前业务类型的 DataFrames 供导出使用
                st.session_state[f'avg_df_{biz_type}'] = df_avg
                st.session_state[f'detail_df_{biz_type}'] = detail_df if 'detail_df' in locals() else pd.DataFrame()
                st.session_state[f'salary_df_{biz_type}'] = salary_df if 'salary_df' in locals() else pd.DataFrame()

    # ==================== 转化率展示区块（毛客数 ÷ 订单数） ====================
    st.divider()
    st.subheader("📊 转化率（成交订单数 ÷ 毛客数）")
    st.dataframe(conv_df.set_index('业务类型'), width='stretch')
    st.caption(
        f"口径：转化率 = 订单数(order_count) ÷ 毛客数(gross_leads)，以百分比展示；"
        f"代表月 → 本月 {_conv_cur_m}｜上月 {_conv_last_m}（环比）｜上年同期 {_conv_ly_m}（同比）。"
        f"— 表示无数据，或毛客数/订单数为 0。"
    )

    # ==================== 成本口径说明备注区块（纯展示，无计算逻辑） ====================
    st.info("""
📌 成本口径说明（直接费用 / 间接费用）
【直接费用】计入「总直接成本」列，包含：
· 推广费用（实际）（即"推广客资费（实际）"）
· 交付费用（场地）、交付费用（主持）、交付费用（搭建）
· 鲜花费用
· 微电影拍摄费用
· 拍摄费用（即"拍摄费用 (郭鹏)"）
· 二销选片费（即"门店二销款结算费"）
· 像素蛋糕修图费
· 微电影剪辑费用
· 后期修片费 (一销)、后期修片费 (二销)
· 工厂费用（一销）、工厂费用（二销）
【间接费用】计入「总间接成本」列，包含：
· 房租、水电、办公费等
· 税费及手续费
· 样片研发
· 场地铺设费
· 舆情处理
⚠️ 对账提示：当前系统计算时，「人工成本（工资，7 个部门）」也计入「总直接成本」，上表未单列——用你自己的表对账时，请把工资一并计入直接费用，否则两边会差一块。
""")

    # ==================== 准备导出报告数据（双口径，供前端交互实时切换） ====================
    def _apply_filter(src_df, opt):
        """按 filter_option 复用的四分支筛选逻辑；返回剔除「合计」行后的子集。
        与原始四分支结果完全一致，不改变现有 report_df 的取值。"""
        if opt == "全部":
            return src_df[src_df['业务类型'] != '合计'].copy()
        elif opt == "仅新疆地区":
            return src_df[(src_df['业务类型'] != '合计') & (src_df['套系'].str.contains('新疆', na=False))].copy()
        elif opt == "仅旅拍":
            return src_df[(src_df['业务类型'] != '合计') & (src_df['业务类型'] == '旅拍')].copy()
        elif opt == "仅婚礼":
            return src_df[(src_df['业务类型'] != '合计') & (src_df['业务类型'] == '婚礼')].copy()
        else:
            return src_df[src_df['业务类型'] != '合计'].copy()

    def _safe_float(v):
        """pandas NaN/NaT -> None，否则转 float；避免 json.dumps 输出非法 NaN 字面量导致浏览器白屏。"""
        if pd.isna(v):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _build_report_json(df):
        """把报表 DataFrame 序列化为 {"rows":[{中文列名:值,...}], "totals":{...}}。
        NaN/NaT 逐值转 None；数值列转 float，字符串列转 str（key 用中文列名原样）。"""
        rows = []
        for _, row in df.iterrows():
            rec = {}
            for col in df.columns:
                val = row[col]
                if pd.isna(val):
                    rec[col] = None
                elif isinstance(val, (int, float, np.integer, np.floating)):
                    rec[col] = float(val)
                else:
                    rec[col] = str(val)
            rows.append(rec)
        if df.empty:
            totals = {'总收入': None, '总直接成本': None, '总间接成本': None, '利润合计': None, '套系数量': None}
        else:
            totals = {
                '总收入': _safe_float(df['总收入'].sum()),
                '总直接成本': _safe_float(df['总直接成本'].sum()),
                '总间接成本': _safe_float(df['总间接成本'].sum()),
                '利润合计': _safe_float(df['利润合计'].sum()),
                '套系数量': _safe_float(df['套系数量'].sum()),
            }
        return {'rows': rows, 'totals': totals}

    report_df = _apply_filter(df_full, filter_option)
    snapshot_df = report_df.copy()

    if report_df.empty:
        st.warning("当前筛选条件下没有数据，无法生成利润表。")
        return

    # 计算另一口径报表（实际/分摊互换），generate_profit_report 带 @st.cache_data，重复调用开销可接受
    other_mode = 'allocation' if promo_mode == 'actual' else 'actual'
    other_full = generate_profit_report(period_start.strftime('%Y-%m-%d'), period_end.strftime('%Y-%m-%d'), other_mode)
    other_report_df = _apply_filter(other_full, filter_option)

    # 组装双口径数据：actual 键存实际口径，allocation 键存分摊口径
    actual_df = report_df if promo_mode == 'actual' else other_report_df
    allocation_df = other_report_df if promo_mode == 'actual' else report_df
    report_payload = {
        'actual': _build_report_json(actual_df),
        'allocation': _build_report_json(allocation_df),
    }
    report_data_json = json.dumps(report_payload, ensure_ascii=False)
    json.loads(report_data_json)  # 自检：确保序列化结果无 NaN/NaT 非法字面量

    if report_df.empty:
        st.warning("当前筛选条件下没有数据，无法生成利润表。")
        return

    total_inc = report_df['总收入'].sum()
    total_dir = report_df['总直接成本'].sum()
    total_ind = report_df['总间接成本'].sum()
    total_prof = report_df['利润合计'].sum()
    total_ord = report_df['套系数量'].sum()

    # ==================== 准备导出HTML ====================
    st.divider()

    export_biz_type = "全部业务" if filter_option == "全部" else ("新疆" if filter_option == "仅新疆地区" else ("旅拍" if filter_option == "仅旅拍" else "婚礼"))

    # 前端口径联动：注入除数（与口径无关，按筛选范围业务类型）
    # 人工成本、推广费用（实际）在分摊口径下除数应为「下单订单数」，实际口径下为「选片订单总数」
    if export_biz_type == "全部业务":
        _orders = (travel_order_cnt or 0) + (wedding_order_cnt or 0)
        _erxiao = (erxiao_travel_cnt or 0) + (erxiao_wedding_cnt or 0)
        _wedding = float(report_df[report_df['业务类型']=='婚礼']['套系数量'].sum() or 0)
    elif export_biz_type == "新疆":
        _orders = xinjiang_order_cnt or 0
        _erxiao = erxiao_xinjiang_cnt or 0
        # 新疆筛选范围内含「新疆婚礼」套系，婚礼订单数须按实际婚礼套系数取，不能恒为 0
        _wedding = float(report_df[report_df['业务类型']=='婚礼']['套系数量'].sum() or 0)
    elif export_biz_type == "旅拍":
        _orders = travel_order_cnt or 0
        _erxiao = erxiao_travel_cnt or 0
        _wedding = 0.0
    else:
        _orders = wedding_order_cnt or 0
        _erxiao = erxiao_wedding_cnt or 0
        _wedding = float(report_df[report_df['业务类型']=='婚礼']['套系数量'].sum() or 0)
    _area = _area_cnt_for_range(period_start, period_end, export_biz_type)
    report_payload['divisors'] = {
        'orders': float(_orders or 0),
        'qty': float(report_df['套系数量'].sum() or 0),
        'erxiao': float(_erxiao or 0),
        'wedding': float(_wedding or 0),
        'area': float(_area or 0),
    }
    # 重新序列化（含 divisors），供前端切换口径时重算人工/推广均价与除数
    report_data_json = json.dumps(report_payload, ensure_ascii=False)
    json.loads(report_data_json)  # 自检：确保序列化结果无 NaN/NaT 非法字面量

    avg_df = st.session_state.get(f'avg_df_{export_biz_type}', pd.DataFrame())
    detail_df = st.session_state.get(f'detail_df_{export_biz_type}', pd.DataFrame())
    salary_df = st.session_state.get(f'salary_df_{export_biz_type}', pd.DataFrame())

    def df_to_html_rows(df, columns):
        if df is None or df.empty:
            return '<tr><td colspan="{}">暂无数据</td></tr>'.format(len(columns))
        rows = ''
        for _, row in df.iterrows():
            cells = []
            for col in columns:
                if col in df.columns:
                    cells.append(f'<td>{row[col]}</td>')
                else:
                    cells.append('<td>—</td>')
            rows += '<tr>' + ''.join(cells) + '</tr>'
        return rows

    fee_table_html = ''
    for col_name, label, remark in fee_columns:
        amt = report_df[col_name].sum() if col_name in report_df.columns else 0
        pct = (amt / total_inc * 100) if total_inc else 0
        fee_table_html += f'<tr><td><strong>{label}</strong></td><td>¥{amt:,.2f}</td><td>{pct:.1f}%</td><td class="remark-cell">{remark}</td></tr>'
    total_cost = total_dir + total_ind
    fee_table_html += f'<tr style="background:#f1f5f9; font-weight:700;"><td>合计</td><td>¥{total_cost:,.2f}</td><td>{(total_cost/total_inc*100) if total_inc else 0:.1f}%</td><td></td></tr>'

    # 生成完整的套系利润明细表 HTML
    set_table_columns = list(report_df.columns)
    set_table_full_html = '<div class="table-wrapper"><table><thead id="setTableHead"><tr>'
    for col in set_table_columns:
        set_table_full_html += f'<th>{col}</th>'
    set_table_full_html += '</tr></thead><tbody id="setTableBody">'

    for _, row in report_df.iterrows():
        set_table_full_html += '<tr>'
        for col in set_table_columns:
            val = row[col]
            if pd.isna(val):
                cell = '—'
            elif isinstance(val, (int, float)):
                if col in ['套系数量']:
                    cell = f'{int(val)}'
                elif col in ['利润率']:
                    cell = f'{val:.1%}'
                elif col in ['利润合计']:
                    cell = f'<span class="{"loss" if val < 0 else "profit"}">¥{val:,.2f}</span>'
                else:
                    cell = f'¥{val:,.2f}'
            else:
                cell = str(val)
            set_table_full_html += f'<td>{cell}</td>'
        set_table_full_html += '</tr>'
    set_table_full_html += '</tbody></table></div>'

    avg_columns = ['费用项', '本期均价', '除数', '去年同期均价', '同比差异', '同比变化']
    if last_month_start and last_month_end:
        avg_columns += ['上月均价', '上月除数', '环比差异', '环比变化']
    avg_table_html = df_to_html_rows(avg_df, avg_columns)

    shoot_columns = ['明细项目', '本期均价', '去年同期均价', '同比差异', '同比变化']
    if last_month_start and last_month_end:
        shoot_columns += ['上月均价', '环比差异', '环比变化']
    shoot_table_html = df_to_html_rows(detail_df, shoot_columns)

    labor_columns = ['部门', '本期均价', '去年同期均价', '同比差异', '同比变化']
    if last_month_start and last_month_end:
        labor_columns += ['上月均价', '环比差异', '环比变化']
    labor_table_html = df_to_html_rows(salary_df, labor_columns)

    # ==================== 交互式 Plotly 图表（HTML + CDN，无需服务器安装字体） ====================
    def plotly_chart_html(div_id: str, traces: list, layout: dict, square: bool = False) -> str:
        """生成 Plotly 交互图表的 HTML 片段，浏览器通过 CDN 加载 Plotly.js 渲染（支持悬停/缩放，中文正常）。

        Args:
            div_id: 图表容器 div 的 id。
            traces: Plotly traces 列表。
            layout: Plotly layout 字典。
            square: 为 True 时，使用方形（aspect-ratio:1/1）容器，保证饼图始终为正圆；
                    为 False 时保持原 380px 固定高度容器（柱状图适用）。
        """
        data_json = json.dumps(traces, ensure_ascii=False)
        layout_json = json.dumps(layout, ensure_ascii=False)
        if square:
            # 方形容器：饼图在接近正方形的容器里才是正圆，避免被压成竖长椭圆
            return (
                f'<div style="width:100%;max-width:400px;aspect-ratio:1/1;margin:0 auto;">'
                f'<div id="{div_id}" style="width:100%;height:100%;"></div></div>'
                f'<script>Plotly.newPlot("{div_id}", {data_json}, {layout_json}, '
                f'{{"responsive": true, "displayModeBar": true}});</script>'
            )
        return (
            f'<div id="{div_id}" style="width:100%;height:380px;"></div>'
            f'<script>Plotly.newPlot("{div_id}", {data_json}, {layout_json}, '
            f'{{"responsive": true, "displayModeBar": true}});</script>'
        )

    # 推广费口径标签（来自函数入参 promo_mode），用于徽章与导出文件名
    promo_mode_label = "实际口径" if promo_mode == 'actual' else "分摊口径"

    # 图1 成本与利润结构：利润为负时用红色柱状，否则用饼图
    total_cost = total_dir + total_ind
    profit_color = "#10b981" if total_prof >= 0 else "#ef4444"
    font_cfg = {"family": "'PingFang SC','Microsoft YaHei',sans-serif"}
    if total_prof >= 0:
        cost_traces = [{
            "type": "pie",
            "labels": ["总成本", "利润"],
            "values": [total_cost, total_prof],
            "marker": {"colors": ["#ef4444", profit_color]},
            "textinfo": "label+percent",
            "hole": 0.0,
        }]
        cost_layout = {"title": {"text": "成本与利润结构"}, "font": font_cfg, "margin": {"t": 40, "b": 10, "l": 10, "r": 10}}
    else:
        cost_traces = [{
            "type": "bar",
            "x": ["总成本", "利润"],
            "y": [total_cost, total_prof],
            "marker": {"color": ["#ef4444", profit_color]},
            "hovertemplate": "%{x}<br>金额: ¥%{y:,.2f}<extra></extra>",
        }]
        cost_layout = {
            "title": {"text": "成本与利润结构（利润为负的说明）"},
            "yaxis": {"title": {"text": "金额 (元)"}},
            "shapes": [{"type": "line", "x0": -0.5, "x1": 1.5, "y0": 0, "y1": 0,
                        "line": {"color": "black", "width": 1}}],
            "font": font_cfg,
        }
    # 图1 始终使用方形容器（aspect-ratio:1/1），保证饼图为正圆、负利润柱状图也不被压扁
    cost_chart_html = plotly_chart_html("cost_chart", cost_traces, cost_layout, square=True)

    # 图2 各套系利润对比（正绿负红）
    set_names = [str(x) for x in report_df['套系'].tolist()]
    set_profits = [float(x) for x in report_df['利润合计'].tolist()]
    set_colors = ["#10b981" if p >= 0 else "#ef4444" for p in set_profits]
    profit_traces = [{
        "type": "bar",
        "x": set_names,
        "y": set_profits,
        "marker": {"color": set_colors},
        "hovertemplate": "%{x}<br>利润: ¥%{y:,.2f}<extra></extra>",
    }]
    profit_layout = {
        "title": {"text": "各套系利润对比"},
        "xaxis": {"tickangle": -45, "automargin": True, "tickfont": {"size": 10}},
        "yaxis": {"title": {"text": "利润 (元)"}},
        "font": font_cfg,
    }
    profit_chart_html = plotly_chart_html("profit_chart", profit_traces, profit_layout)

    # 图3 主要费用项均价（按订单数分摊，列不存在时跳过）
    avg_top_cols = ['拍摄费用', '样片研发', '推广费用（实际）', '人工成本', '微电影拍摄费用',
                    '二销选片费', '微电影剪辑费用']
    avg_present = []
    avg_values = []
    for col in avg_top_cols:
        if col in report_df.columns and total_ord:
            avg_present.append(col)
            avg_values.append(float(report_df[col].sum()) / float(total_ord))
    avg_traces = [{
        "type": "bar",
        "x": avg_present,
        "y": avg_values,
        "marker": {"color": "#4f46e5"},
        "hovertemplate": "%{x}<br>单均: ¥%{y:,.2f}<extra></extra>",
    }]
    avg_layout = {
        "title": {"text": "主要费用项均价（按订单数分摊）"},
        "xaxis": {"tickangle": -45, "automargin": True, "tickfont": {"size": 10}},
        "yaxis": {"title": {"text": "单均 (元/单)"}},
        "font": font_cfg,
    }
    avg_chart_html = plotly_chart_html("avg_chart", avg_traces, avg_layout)

    html_report = generate_html_report(
        report_df, total_inc, total_dir, total_ind, total_prof, total_ord,
        period_month, filter_option, fee_table_html, avg_table_html, shoot_table_html, labor_table_html,
        set_table_full_html, cost_chart_html, profit_chart_html, avg_chart_html,
        caliber_label=promo_mode_label, report_data_json=report_data_json
    )

    st.download_button(
        label="📥 下载HTML分析报告",
        data=html_report,
        file_name=f"利润分析_{period_month}_{filter_option}_{promo_mode_label}.html",
        mime="text/html"
    )

    add_log(st.session_state.user_id, st.session_state.username, "生成利润表", f"月份:{period_month}")
    period_mode_str = "按月" if month is not None else "按月"
    filter_label = filter_option if filter_option != "全部" else "全部"
    # 快照期间标签：period_month 已经是正确的展示标签
    #   - 单月模式：'2026-08'
    #   - 多月模式：'2026年1月-2026年8月'
    #   - 按年模式：'2026年'
    # ⚠️ 修复（2026-09-30）：原写法 `period_month if month is not None else f"{year}年"`
    #    在多月模式下 month 与 year 同时为 None，会生成字面量字符串 'None年'，
    #    污染快照标题（历史快照 ID:1/10/12 曾出现「None年 - 全部」）。
    #    改为直接使用 period_month，并对 None/空值做兜底。
    period_label = period_month if period_month else f"{period_start} ~ {period_end}"
    save_profit_snapshot(period_mode_str, filter_label, period_start, period_end, period_label, snapshot_df)

# ---------- 统一账单导入入口 ----------
def bill_import_main_page():
    st.header("📥 账单导入")
    import_type = st.selectbox("选择导入的账单类型", [
        "拍摄费用账单",
        "交付费用（主持/搭建/场地/鲜花）",
        "自租场地消耗",
        "微电影拍摄账单",
        "微电影剪辑账单",
        "二销选片账单（门店二销款结算费）",
        "修片账单",
        "工厂账单"
    ])
    if import_type == "拍摄费用账单":
        shooting_bill_import_page()
    elif import_type == "交付费用（主持/搭建/场地/鲜花）":
        delivery_cost_import_page()
    elif import_type == "自租场地消耗":
        venue_self_rent_import_page()
    elif import_type == "微电影拍摄账单":
        micro_film_shooting_import_page()
    elif import_type == "微电影剪辑账单":
        micro_film_editing_import_page()
    elif import_type == "二销选片账单（门店二销款结算费）":
        second_sales_import_page()
    elif import_type == "修片账单":
        retouch_bill_import_page()
    elif import_type == "工厂账单":
        factory_bill_import_page()
def import_income_page():
    st.header("📥 导入收入数据")
    module_name = "📥 导入收入数据"
    can_see = has_permission(module_name, st.session_state.role)
    with st.expander("📋 导入字段说明", expanded=False):
        st.markdown("""
        **必填字段：** `订单号`, `套系`, `选片时间`, `选片`, `加修`  
        **可选字段：** `套系金额`, `二销金额`, `客诉退款金额`, `客户姓名`, `拍照`（或`照片`）  
        **日期格式：** 自动识别 `2026/1/31 15:00`、`2026-01-31 18:00`、`2026-07-31` 等常见格式。
        """)
    uploaded = st.file_uploader("上传收入Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded, header=0)
        if '订单号' not in df.columns:
            df = pd.read_excel(uploaded, header=1)
        
        df.columns = [str(c).strip() for c in df.columns]
        
        if can_see:
            st.write("原始数据预览：", df.head())
        else:
            st.write("原始数据预览：", mask_dataframe(df.head()))
        
        mandatory = ['订单号', '套系', '选片时间', '选片', '加修']
        missing = [c for c in mandatory if c not in df.columns]
        if missing:
            st.error(f"缺少必填列：{missing}，请检查Excel列名（第一行应为列名）")
            return
        
        df = df[~df['订单号'].astype(str).str.strip().isin(['', 'nan', 'None', '订单号'])]
        df = df.dropna(subset=['订单号'])
        
        df["类型"] = df["套系"].map(SET_TYPE_MAP)
        for col in ['套系金额','二销金额','客诉退款金额','拍照','照片']:
            if col not in df.columns:
                df[col] = 0
        if '照片' in df.columns and '拍照' not in df.columns:
            df.rename(columns={'照片':'拍照'}, inplace=True)
        
        numeric_cols = ['套系金额','二销金额','客诉退款金额','选片','加修','拍照']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        if '客户姓名' not in df.columns:
            df['客户姓名'] = ''
        else:
            df['客户姓名'] = df['客户姓名'].fillna('')
        
        records = []
        parse_errors = []
        for _, row in df.iterrows():
            oid = str(row['订单号']).strip()
            # 日期解析增强
            time_raw = row['选片时间']
            sel_date = None
            if pd.notna(time_raw):
                if isinstance(time_raw, (pd.Timestamp, datetime, date)):
                    sel_date = pd.Timestamp(time_raw).date()
                else:
                    time_str = str(time_raw).strip().replace('\u3000', ' ').replace('\xa0', ' ').replace('\t', ' ')
                    # 尝试多种格式
                    for fmt in ['%Y/%m/%d %H:%M', '%Y/%m/%d', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y-%m-%d %H:%M:%S']:
                        try:
                            dt = pd.to_datetime(time_str, format=fmt, errors='raise')
                            sel_date = dt.date()
                            break
                        except:
                            continue
                    if sel_date is None:
                        dt = pd.to_datetime(time_str, errors='coerce', infer_datetime_format=True)
                        if pd.notna(dt):
                            sel_date = dt.date()
            if sel_date is None:
                parse_errors.append(oid)
            records.append({
                '订单号': oid,
                '套系': str(row['套系']).strip(),
                '类型': str(row['类型']),
                '套系金额': float(row['套系金额']),
                '二销金额': float(row['二销金额']),
                '客诉退款金额': float(row['客诉退款金额']),
                '选片': int(float(row['选片'])),
                '加修': int(float(row['加修'])),
                '拍照': int(float(row.get('拍照', 0))),
                '客户姓名': str(row.get('客户姓名', '')),
                '选片时间': sel_date
            })
        
        if parse_errors:
            if can_see:
                st.warning(f"以下订单的选片时间无法解析，请检查原始文件：{', '.join(parse_errors[:20])}")
            else:
                st.warning(f"有 {len(parse_errors)} 个订单的选片时间无法解析，请检查原始文件。")

        db = SessionLocal()
        try:
            oids = [r['订单号'] for r in records]
            existing_orders = db.query(Order).filter(Order.order_id.in_(oids)).all()
            existing_map = {o.order_id: o for o in existing_orders}
        finally:
            db.close()

        duplicates_info = []
        new_records = []
        for r in records:
            if r['订单号'] in existing_map:
                old = existing_map[r['订单号']]
                duplicates_info.append((r['订单号'], old.set_name, old.set_price, old.second_sales, old.refund,
                                        r['套系金额'], r['二销金额']))
            else:
                new_records.append(r)

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已存在，请选择处理方式：")
                dup_df = pd.DataFrame(duplicates_info, columns=['订单号', '已有套系', '已有套系金额', '已有二销', '已有退款', '本次套系金额', '本次二销'])
                st.dataframe(dup_df)
            else:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已存在，请选择处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", [
                "覆盖已有记录",
                "仅导入新记录（跳过已有）"
            ])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入收入数据"):
            db = SessionLocal()
            try:
                import_count = 0
                update_count = 0

                if handle_dup == "覆盖已有记录":
                    for r in records:
                        if r['订单号'] in existing_map:
                            existing = existing_map[r['订单号']]
                            existing.set_name = r['套系']
                            existing.type = r['类型']
                            existing.set_price = r['套系金额']
                            existing.second_sales = r['二销金额']
                            existing.refund = r['客诉退款金额']
                            existing.selected_photos = r['选片']
                            existing.extra_photos = r['加修']
                            existing.photo_count = r['拍照']
                            existing.customer_name = r['客户姓名']
                            # 只有当新日期不为空时才更新
                            if r['选片时间'] is not None:
                                existing.selection_date = r['选片时间']
                            update_count += 1
                        else:
                            new_order = Order(
                                order_id=r['订单号'], set_name=r['套系'], type=r['类型'],
                                set_price=r['套系金额'], second_sales=r['二销金额'],
                                refund=r['客诉退款金额'], selected_photos=r['选片'],
                                extra_photos=r['加修'], photo_count=r['拍照'],
                                customer_name=r['客户姓名'], selection_date=r['选片时间']
                            )
                            db.add(new_order)
                            import_count += 1
                else:
                    for r in new_records:
                        new_order = Order(
                            order_id=r['订单号'], set_name=r['套系'], type=r['类型'],
                            set_price=r['套系金额'], second_sales=r['二销金额'],
                            refund=r['客诉退款金额'], selected_photos=r['选片'],
                            extra_photos=r['加修'], photo_count=r['拍照'],
                            customer_name=r['客户姓名'], selection_date=r['选片时间']
                        )
                        db.add(new_order)
                        import_count += 1

                db.commit()
                msg = f"成功处理 {import_count} 个新订单" if can_see else "成功处理 *** 个新订单"
                if update_count > 0:
                    msg += f"，更新 {update_count} 个已有订单"
                st.success(msg)
                add_log(st.session_state.user_id, st.session_state.username, "导入收入数据",
                        f"新{import_count}条, 更新{update_count}条" if can_see else "导入收入数据")
            except Exception as e:
                db.rollback()
                st.error(f"导入出错：{e}")
            finally:
                db.close()

def maintain_std_cost_page():
    st.header("📋 套系标准成本库")
    module_name = "📋 维护标准成本"
    can_see = has_permission(module_name, st.session_state.role)
    with st.expander("📋 导入字段说明", expanded=False):
        st.markdown("""
        **Excel格式：** 列名 `套系`, `费用项代码`, `金额`  
        **常见费用项：** `工厂费用`, `微电影剪辑费`, `修片单价`, `拍摄费用` 等
        """)
    db = SessionLocal()
    costs = db.query(SetStandardCost).all()
    if costs:
        df_costs = pd.DataFrame([{'套系':c.set_name,'费用项':c.cost_item,'金额':c.amount} for c in costs])
        if can_see:
            st.write("当前数据：", df_costs)
        else:
            st.write("当前数据：", mask_dataframe(df_costs))
    db.close()
    uploaded = st.file_uploader("上传标准成本Excel", type="xlsx")
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write(df.head())
        else:
            st.write(mask_dataframe(df.head()))
        if st.button("替换全部标准成本"):
            db = SessionLocal()
            try:
                db.query(SetStandardCost).delete()
                for _, row in df.iterrows():
                    db.add(SetStandardCost(set_name=row['套系'], cost_item=row['费用项代码'], amount=row['金额']))
                db.commit()
                msg = f"标准成本已更新" if can_see else "标准成本已更新（数量已隐藏）"
                st.success(msg)
                add_log(st.session_state.user_id, st.session_state.username, "维护标准成本", f"更新{len(df)}条" if can_see else "更新***条")
            except Exception as e:
                db.rollback()
                st.error(f"错误：{e}")
            finally:
                db.close()

def import_operation_cost_page():
    st.markdown("### 📊 导入运营成本")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传 Excel 文件，需包含列：`年月`（如 2026年7月 或 2026-07）、`账号`、`求和项:订单成本`（或类似金额列）。  
    - 系统会自动按**年月**汇总各账号金额，存储为独立数据，不参与利润计算。  
    - 重复导入同一个月会覆盖该月的运营成本记录。
    """)
    uploaded = st.file_uploader("上传运营成本Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        # 自动识别列名
        col_map = {}
        for col in df.columns:
            col_str = str(col).strip()
            if '年月' in col_str or '日期' in col_str:
                col_map['年月'] = col
            elif '账号' in col_str:
                col_map['账号'] = col
            elif '订单成本' in col_str or '金额' in col_str or '成本' in col_str:
                col_map['金额'] = col
        missing = [k for k in ['年月', '账号', '金额'] if k not in col_map]
        if missing:
            st.error(f"缺少必要列：{missing}，请检查Excel列名是否包含“年月”、“账号”、“订单成本”等关键词")
            return
        df = df.rename(columns={v: k for k, v in col_map.items()})
        df['金额'] = pd.to_numeric(df['金额'], errors='coerce').fillna(0)
        df['年月'] = df['年月'].apply(standardize_period)  # 转为 2026-07 格式
        # 保留账号与金额明细，不汇总（查询时可按账号查看）
        records = df[['年月', '账号', '金额']].dropna(subset=['年月'])
        if records.empty:
            st.warning("没有有效数据")
            return

        if st.button("确认导入运营成本"):
            db = SessionLocal()
            try:
                # 删除所有 business_type='运营' 的旧记录
                db.query(IndirectCost).filter_by(business_type='运营').delete()
                for _, row in records.iterrows():
                    db.add(IndirectCost(
    period=row['年月'],
    cost_item=str(row['账号']),   # 账号存入 cost_item 字段
    business_type='运营',
    amount=float(row['金额'])
))
                db.commit()
                msg = f"成功导入 {len(records)} 条运营成本记录" if can_see else "成功导入 *** 条"
                st.success(msg)
                add_log(st.session_state.user_id, st.session_state.username, "导入运营成本", f"共{len(records)}条" if can_see else "导入运营成本")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def operation_cost_query_page():
    st.markdown("### 📋 运营成本查询")
    module_name = "📋 数据查询"
    can_see = has_permission(module_name, st.session_state.role)

    db = SessionLocal()
    try:
        # 获取所有运营成本的年月列表
        periods = db.query(IndirectCost.period).filter(IndirectCost.business_type == '运营').distinct().all()
        period_options = [p[0] for p in periods if p[0]]
        if not period_options:
            st.info("暂无运营成本数据")
            return
        selected_period = st.selectbox("选择年月", sorted(period_options, reverse=True))
        records = db.query(IndirectCost).filter(
            IndirectCost.business_type == '运营',
            IndirectCost.period == selected_period
        ).order_by(IndirectCost.id).all()
        if records:
            df = pd.DataFrame([{
                '年月': r.period,
                '账号': r.cost_item,  # 注意：我们将账号存在 cost_item 字段
                '金额': r.amount
            } for r in records])
            if can_see:
                st.dataframe(df, width='stretch')
            else:
                st.dataframe(mask_dataframe(df), width='stretch')
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 导出查询结果", csv, f"运营成本_{selected_period}.csv", "text/csv")
        else:
            st.info("该月份暂无数据")
    finally:
        db.close()

def import_actual_cost_page():
    st.header("💰 导入实际直接成本（支持重复检测，覆盖/合并）")
    module_name = "💰 导入实际直接成本"
    can_see = has_permission(module_name, st.session_state.role)
    with st.expander("📋 导入格式说明", expanded=False):
        st.markdown("""
        **宽表格式（推荐）：** 列名如 `搭建费(实际)`, `主持费(实际)` 等，需包含 `订单号`  
        **明细格式：** 三列 `订单号`, `费用项`, `金额`  
        > ⚠️ **拍摄费用**请使用「📸 拍摄账单导入」处理，否则会丢失费用解析详情。
        """)
    uploaded = st.file_uploader("上传实际成本Excel", type=["xlsx"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("文件预览：", df.head())
        else:
            st.write("文件预览：", mask_dataframe(df.head()))
        possible_wide = ["搭建费(实际)", "主持费(实际)", "场地费(实际)", "鲜花费（实际）",
                         "微电影拍摄费用(实际)", "微电影剪辑费用（实际）", "二销选片费(实际)",
                         "拍摄费用(实际)", "工厂成本（实际）", "像素蛋糕费用", "修片费(实际)"]
        is_wide = sum(col in df.columns for col in possible_wide) >= 2 and "费用项" not in df.columns
        records = []
        retouch_updates = {}
        NAME_MAP = {
            "搭建费(实际)": "搭建", "搭建费": "搭建",
            "主持费(实际)": "主持", "主持费": "主持",
            "场地费(实际)": "场地", "场地费": "场地",
            "鲜花费（实际）": "鲜花费用", "鲜花费(实际)": "鲜花费用", "鲜花费": "鲜花费用",
            "微电影拍摄费用(实际)": "微电影拍摄费用", "微电影拍摄费用": "微电影拍摄费用",
            "微电影剪辑费用（实际）": "微电影剪辑费用", "微电影剪辑费用(实际)": "微电影剪辑费用",
            "微电影剪辑费用": "微电影剪辑费用",
            "二销选片费(实际)": "二销选片费", "二销选片费": "二销选片费",
            "拍摄费用(实际)": "拍摄费用", "拍摄费": "拍摄费用",
            "郭鹏拍摄费": "拍摄费用",
            "工厂成本（实际）": "工厂费用", "工厂成本(实际)": "工厂费用", "工厂成本": "工厂费用",
            "像素蛋糕费用": "像素蛋糕修图费", "像素蛋糕": "像素蛋糕修图费",
            "门店二销款": "二销选片费", "门店二销款结算费": "二销选片费",
        }
        if is_wide:
            retouch_cols = [c for c in df.columns if "修片费" in c and "微电影" not in c]
            col_mapping = {}
            for col in df.columns:
                if col == '订单号' or col in retouch_cols: continue
                if col in NAME_MAP: col_mapping[col] = NAME_MAP[col]
                elif col not in ['备注', '导入时间']: col_mapping[col] = col
            for rc in retouch_cols:
                for _, row in df.iterrows():
                    if pd.notna(row[rc]):
                        oid = str(row['订单号'])
                        retouch_updates[oid] = float(row[rc])
            for _, row in df.iterrows():
                oid = str(row['订单号'])
                for col, std_name in col_mapping.items():
                    val = row[col]
                    if pd.notna(val) and val != '':
                        try:
                            amount = float(val)
                            if amount != 0:
                                if std_name == '拍摄费用':
                                    st.warning(f"订单 {oid} 的「拍摄费用」已跳过，请使用「📸 拍摄账单导入」处理。")
                                    continue
                                records.append((oid, std_name, amount))
                        except ValueError: pass
        else:
            if "订单号" not in df.columns or "费用项" not in df.columns or "金额" not in df.columns:
                st.error("需要订单号、费用项、金额三列")
                return
            for _, row in df.iterrows():
                oid = str(row['订单号'])
                raw_item = str(row['费用项']).strip()
                std_item = NAME_MAP.get(raw_item, raw_item)
                try: amt = float(row['金额'])
                except: continue
                if amt != 0:
                    if std_item == '拍摄费用':
                        st.warning(f"订单 {oid} 的「拍摄费用」已跳过，请使用「📸 拍摄账单导入」处理。")
                        continue
                    records.append((oid, std_item, amt))

        if not records and not retouch_updates:
            st.warning("没有有效数据")
            return
        db = SessionLocal()
        try:
            oids = list({r[0] for r in records})
            items = list({r[1] for r in records})
            if oids and items:
                existing_rows = db.query(ActualDirectCost).filter(
                    ActualDirectCost.order_id.in_(oids),
                    ActualDirectCost.cost_item.in_(items)
                ).all()
                for row in existing_rows:
                    db.delete(row)
            for oid, item, amt in records:
                db.add(ActualDirectCost(order_id=oid, cost_item=item, amount=amt))
            for oid, fee in retouch_updates.items():
                order = db.query(Order).filter_by(order_id=oid).first()
                if order: order.actual_retouch_fee = fee
                else: db.add(Order(order_id=oid, actual_retouch_fee=fee))
            db.commit()
            msg = f"成功导入/更新 {len(records)} 条成本记录，修片费更新 {len(retouch_updates)} 个订单" if can_see else "成功导入/更新 *** 条成本记录，修片费更新 *** 个订单"
            st.success(msg)
            add_log(st.session_state.user_id, st.session_state.username, "导入实际成本", f"成本{len(records)}条" if can_see else "成本***条")
        except Exception as e:
            db.rollback()
            st.error(f"导入失败：{e}")
        finally:
            db.close()

def import_cherry_cost_page():
    st.header("🏭 导入樱桃云产品成本")
    module_name = "🏭 导入樱桃云产品成本"
    can_see = has_permission(module_name, st.session_state.role)
    with st.expander("📋 导入字段说明", expanded=False):
        st.markdown("""
        **必须列：** `订单号`, `产品成本`
        """)
    uploaded = st.file_uploader("上传产品成本Excel", type="xlsx")
    batch_id = st.text_input("批次号", value=datetime.now().strftime("%Y%m%d%H%M"))
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("原始预览：", df.head())
        else:
            st.write("原始预览：", mask_dataframe(df.head()))

        # ---------- 校验列名 ----------
        df.columns = [str(c).strip() for c in df.columns]
        missing = [c for c in ['订单号', '产品成本'] if c not in df.columns]
        if missing:
            st.error(f"❌ 缺少必要列：{missing}。当前文件的列名为：{list(df.columns)}")
            st.info("请确保 Excel 首行包含「订单号」和「产品成本」两列。")
            return

        df = df.dropna(subset=['订单号'])
        df['产品成本'] = pd.to_numeric(df['产品成本'], errors='coerce').fillna(0)
        df = df[df['产品成本'].apply(lambda x: isinstance(x, (int, float)))]
        grouped = df.groupby('订单号')['产品成本'].sum().reset_index()
        if can_see:
            st.write("求和后预览：", grouped.head())
        else:
            st.write("求和后预览：", mask_dataframe(grouped.head()))
        if st.button("导入为最新批次"):
            db = SessionLocal()
            try:
                for _, row in grouped.iterrows():
                    db.add(CherryProductCost(batch_id=batch_id, order_id=str(row['订单号']), product_total_cost=float(row['产品成本'])))
                db.commit()
                msg = f"批次 {batch_id} 已导入 {len(grouped)} 条（已按订单号求和）" if can_see else f"批次 {batch_id} 已导入 *** 条（已按订单号求和）"
                st.success(msg)
                add_log(st.session_state.user_id, st.session_state.username, "导入产品成本", f"批次{batch_id}，导入{len(grouped)}条" if can_see else f"批次{batch_id}，导入***条")
            except Exception as e:
                db.rollback()
                st.error(f"失败：{e}")
            finally:
                db.close()

def indirect_cost_page():
    st.header("📈 间接成本录入（按月，支持 Excel 批量导入多期数据）")
    module_name = "📈 录入间接成本"
    can_see = has_permission(module_name, st.session_state.role)

    # ---------- 期间下拉框：自动列出数据库中已有的期间 ----------
    db_periods = SessionLocal()
    try:
        period_rows = db_periods.query(IndirectCost.period).distinct().all()
        existing_periods = sorted({p[0] for p in period_rows if p[0]}, reverse=True)
    finally:
        db_periods.close()

    current_month = date.today().strftime('%Y-%m')
    # 把当前月也加进候选（避免数据库为空时下拉框没内容）
    if current_month not in existing_periods:
        existing_periods = [current_month] + existing_periods

    period = st.selectbox(
        "当前选择期间",
        options=existing_periods,
        index=0,   # 默认选最新的期间
        key='indirect_cost_period'
    )
    db = SessionLocal()
    saved = {}
    try:
        rows = db.query(IndirectCost).filter_by(period=period).all()
        for r in rows:
            if r.cost_item == '推广费':
                if r.business_type == '旅拍': saved['travel_promo'] = r.amount
                elif r.business_type == '婚礼': saved['wedding_promo'] = r.amount
            else:
                key = (r.cost_item, r.business_type)
                saved[key] = r.amount
    except: pass
    finally: db.close()

    if not can_see:
        st.warning("您没有编辑权限，仅可查看脱敏数据。")

    with st.expander("📤 工资批量导入（年月-部门-旅拍-婚礼，可多期）", expanded=False):
        st.markdown("""
        **文件格式**：列名必须为 `年月`、`部门`、`旅拍`、`婚礼`  
        部门如：策划部、销售部等，系统将自动加上“工资”后缀存储。
        """)
        uploaded_salary = st.file_uploader("上传工资Excel", type="xlsx", key="salary_upload")
        if uploaded_salary:
            df_s = pd.read_excel(uploaded_salary)
            if can_see:
                st.write("预览：", df_s.head())
            else:
                st.write("预览：", mask_dataframe(df_s.head()))
            if all(c in df_s.columns for c in ['年月', '部门', '旅拍', '婚礼']):
                if st.button("导入工资（多期）"):
                    db = SessionLocal()
                    try:
                        grouped = df_s.groupby(['年月', '部门']).agg({'旅拍': 'sum', '婚礼': 'sum'}).reset_index()
                        for _, row in grouped.iterrows():
                            p = standardize_period(row['年月'])
                            dept = str(row['部门']) + '工资'
                            db.query(IndirectCost).filter_by(period=p, cost_item=dept).delete()
                        db.commit()
                        for _, row in grouped.iterrows():
                            p = standardize_period(row['年月'])
                            dept = str(row['部门']) + '工资'
                            travel_amt = float(row['旅拍']) if pd.notna(row['旅拍']) else 0
                            wedding_amt = float(row['婚礼']) if pd.notna(row['婚礼']) else 0
                            for bt, amt in [('旅拍', travel_amt), ('婚礼', wedding_amt)]:
                                if amt != 0:
                                    db.add(IndirectCost(period=p, cost_item=dept, business_type=bt, amount=amt))
                        db.commit()
                        msg = "工资批量导入成功！" if can_see else "工资批量导入成功（数量已隐藏）"
                        st.success(msg)
                        st.rerun()
                    except Exception as e:
                        db.rollback(); st.error(f"导入失败：{e}")
                    finally: db.close()
            else:
                st.error("缺少必要列：年月、部门、旅拍、婚礼")

    with st.expander("📤 间接费用批量导入（期间-业务类型-费用项-金额，可多期）", expanded=False):
        st.markdown("""
        **文件格式**：必须包含列 `期间`、`业务类型`（旅拍/婚礼）、`费用项`、`金额`  
        费用项可使用：`房租、水电、办公费等`、`税费及手续费`、`样片研发`、`场地铺设费`、`舆情处理` 等。
        """)
        st.warning(
            "**「样片研发」只填新疆以外的拍样费用。** "
            "新疆拍样费用请**单独**到「🏜️ 新疆拍样/报销费用」导入"
            "（要走 `business_type='新疆'`，系统才能按新疆订单数分摊到旅拍与婚礼）。"
            "两者填错会出现重复计算或漏算。", icon="⚠️")
        st.download_button("📥 下载间接费用导入模板", 
                           pd.DataFrame(columns=['期间', '业务类型', '费用项', '金额']).to_csv(index=False).encode('utf-8-sig'),
                           "间接费用模板.csv", "text/csv")
        uploaded_indirect = st.file_uploader("上传间接费用Excel", type="xlsx", key="indirect_upload")
        if uploaded_indirect:
            df_i = pd.read_excel(uploaded_indirect)
            if can_see:
                st.write("预览：", df_i.head())
            else:
                st.write("预览：", mask_dataframe(df_i.head()))
            required = ['期间', '业务类型', '费用项', '金额']
            if all(col in df_i.columns for col in required):
                if st.button("导入间接费用（多期）"):
                    db = SessionLocal()
                    try:
                        for _, row in df_i.iterrows():
                            p = standardize_period(row['期间'])
                            bt = str(row['业务类型'])
                            item = str(row['费用项'])
                            db.query(IndirectCost).filter_by(period=p, cost_item=item, business_type=bt).delete()
                        db.commit()
                        for _, row in df_i.iterrows():
                            p = standardize_period(row['期间'])
                            bt = str(row['业务类型'])
                            item = str(row['费用项'])
                            amt = float(row['金额'])
                            if bt not in ('旅拍', '婚礼'): continue
                            db.add(IndirectCost(period=p, cost_item=item, business_type=bt, amount=amt))
                        db.commit()
                        msg = "间接费用批量导入成功！" if can_see else "间接费用批量导入成功（数量已隐藏）"
                        st.success(msg)
                        st.rerun()
                    except Exception as e:
                        db.rollback(); st.error(f"导入失败：{e}")
                    finally: db.close()
            else:
                st.error(f"缺少必要列：{required}")

    st.subheader("旅拍 & 婚礼 分项间接费用")
    st.caption(
        "⚠️ **「样片研发」只填新疆以外的拍样费用**；新疆拍样费用请走「🏜️ 新疆拍样/报销费用」单独导入。")
    cost_items = ['策划部工资','销售部工资','运营部工资','综合部工资','总经办工资',
                  '售后服务工资','AI与数据中心工资','房租、水电、办公费等','税费及手续费',
                  '样片研发','场地铺设费','舆情处理']
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 📸 旅拍")
        travel_vals = {item: st.number_input(f"{item}", min_value=0.0, value=saved.get((item, '旅拍'), 0.0), step=100.0, key=f"t_{item}", disabled=not can_see) for item in cost_items}
    with col2:
        st.markdown("#### 💒 婚礼")
        wedding_vals = {item: st.number_input(f"{item}", min_value=0.0, value=saved.get((item, '婚礼'), 0.0), step=100.0, key=f"w_{item}", disabled=not can_see) for item in cost_items}
    st.divider()
    st.subheader("📢 推广费（总额，系统按加权订单数自动拆分）")
    promo_total = st.number_input("推广费总额", min_value=0.0, value=float(saved.get('travel_promo',0)+saved.get('wedding_promo',0)), step=100.0, key="promo_total", disabled=not can_see)
    db = SessionLocal()
    ratio_rule = db.query(AllocationRule).filter_by(rule_type='promotion_wedding_ratio').first()
    wedding_ratio = float(ratio_rule.param1) if ratio_rule else 1.0
    stats_travel = db.query(MonthlyStats).filter_by(period=period, business_type='旅拍').first()
    stats_wedding = db.query(MonthlyStats).filter_by(period=period, business_type='婚礼').first()
    db.close()
    if stats_travel and stats_wedding:
        t_orders = stats_travel.order_count
        w_orders = stats_wedding.order_count
        weighted_total = t_orders + w_orders * wedding_ratio
        if weighted_total > 0:
            travel_promo_preview = promo_total * (t_orders / weighted_total)
            wedding_promo_preview = promo_total - travel_promo_preview
            if can_see:
                st.caption(f"当月旅拍订单数：{t_orders}，婚礼订单数：{w_orders}，婚礼占比：{wedding_ratio*100:.0f}%")
                st.caption(f"加权总订单数：{weighted_total:.1f}，预计拆分：旅拍 ¥{travel_promo_preview:,.0f}，婚礼 ¥{wedding_promo_preview:,.0f}")
            else:
                st.caption("订单数及拆分预览已隐藏")
    else:
        st.warning("请先在「📊 录入月度基础数据」中录入本月的订单数，以便自动拆分推广费。")
    db = SessionLocal()
    existing = db.query(IndirectCost).filter_by(period=period).all()
    db.close()
    if existing:
        st.subheader("📋 本月已保存的间接费用记录")
        df_exist = pd.DataFrame([{'费用项': e.cost_item, '业务类型': e.business_type or '推广费', '金额': e.amount} for e in existing])
        if can_see:
            st.dataframe(df_exist, width='stretch')
        else:
            st.dataframe(mask_dataframe(df_exist), width='stretch')
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("💾 保存手动录入（增量更新）", disabled=not can_see):
            db = SessionLocal()
            try:
                for item, amt in travel_vals.items():
                    exist_row = db.query(IndirectCost).filter_by(period=period, cost_item=item, business_type='旅拍').first()
                    if exist_row: exist_row.amount = amt
                    else: db.add(IndirectCost(period=period, cost_item=item, business_type='旅拍', amount=amt))
                for item, amt in wedding_vals.items():
                    exist_row = db.query(IndirectCost).filter_by(period=period, cost_item=item, business_type='婚礼').first()
                    if exist_row: exist_row.amount = amt
                    else: db.add(IndirectCost(period=period, cost_item=item, business_type='婚礼', amount=amt))
                db.query(IndirectCost).filter_by(period=period, cost_item='推广费').delete()
                if promo_total > 0 and stats_travel and stats_wedding:
                    weighted_total = t_orders + w_orders * wedding_ratio
                    if weighted_total > 0:
                        travel_promo = promo_total * (t_orders / weighted_total)
                        wedding_promo = promo_total - travel_promo
                        db.add(IndirectCost(period=period, cost_item='推广费', business_type='旅拍', amount=travel_promo))
                        db.add(IndirectCost(period=period, cost_item='推广费', business_type='婚礼', amount=wedding_promo))
                db.commit()
                st.success("保存成功！"); st.rerun()
                add_log(st.session_state.user_id, st.session_state.username, "录入间接成本", f"期间{period}")
            except Exception as e:
                db.rollback(); st.error(f"保存失败：{e}")
            finally: db.close()
    with col_btn2:
        if st.button("🔄 仅更新推广费拆分", disabled=not can_see):
            db = SessionLocal()
            try:
                db.query(IndirectCost).filter_by(period=period, cost_item='推广费').delete()
                if promo_total > 0 and stats_travel and stats_wedding:
                    weighted_total = t_orders + w_orders * wedding_ratio
                    if weighted_total > 0:
                        travel_promo = promo_total * (t_orders / weighted_total)
                        wedding_promo = promo_total - travel_promo
                        db.add(IndirectCost(period=period, cost_item='推广费', business_type='旅拍', amount=travel_promo))
                        db.add(IndirectCost(period=period, cost_item='推广费', business_type='婚礼', amount=wedding_promo))
                db.commit()
                st.success("推广费已重新拆分"); st.rerun()
            except Exception as e:
                db.rollback(); st.error(f"更新失败：{e}")
            finally: db.close()

def monthly_stats_page():
    st.header("📊 录入月度基础数据（毛客资、订单数、推广费）")
    module_name = "📊 录入月度基础数据"
    can_see = has_permission(module_name, st.session_state.role)

    db_periods = SessionLocal()
    try:
        period_rows = db_periods.query(MonthlyStats.period).distinct().all()
        existing_periods = sorted({p[0] for p in period_rows if p[0]}, reverse=True)
    finally:
        db_periods.close()

    current_month = date.today().strftime('%Y-%m')
    if current_month not in existing_periods:
        existing_periods = [current_month] + existing_periods

    period = st.selectbox(
        "当前期间",
        options=existing_periods,
        index=0,
        key='monthly_stats_period'
    )
    if not can_see:
        st.warning("您没有编辑权限，仅可查看脱敏数据。")

    with st.expander("📤 Excel 批量导入毛客资/订单数（覆盖已有，可多期）", expanded=False):
        st.markdown("""
        **文件格式**：必须包含列：`期间`、`业务类型`（旅拍/婚礼/新疆）、`毛客资`、`订单数`
        """)
        uploaded_stats = st.file_uploader("上传月度基础数据Excel", type=["xlsx", "xls"], key="stats_upload")
        if uploaded_stats:
            df_s = pd.read_excel(uploaded_stats)
            if can_see:
                st.write("预览：", df_s.head())
            else:
                st.write("预览：", mask_dataframe(df_s.head()))
            required = ['期间', '业务类型', '毛客资', '订单数']
            if all(col in df_s.columns for col in required):
                if st.button("导入毛客资/订单数（覆盖）", key="confirm_stats_import", disabled=not can_see):
                    db = SessionLocal()
                    try:
                        for _, row in df_s.iterrows():
                            p = standardize_period(row['期间'])
                            bt = str(row['业务类型'])
                            db.query(MonthlyStats).filter_by(period=p, business_type=bt).delete()
                        db.commit()
                        for _, row in df_s.iterrows():
                            p = standardize_period(row['期间'])
                            bt = str(row['业务类型'])
                            gross = int(row['毛客资'])
                            orders = int(row['订单数'])
                            if bt not in ('旅拍', '婚礼', '新疆'):
                                st.warning(f"业务类型需为旅拍/婚礼/新疆，跳过：{row.to_dict()}")
                                continue
                            db.add(MonthlyStats(period=p, business_type=bt, gross_leads=gross, order_count=orders))
                        db.commit()
                        msg = "毛客资/订单数导入成功！" if can_see else "毛客资/订单数导入成功（数量已隐藏）"
                        st.success(msg)
                        st.rerun()
                    except Exception as e:
                        db.rollback(); st.error(f"导入失败：{e}")
                    finally: db.close()
            else:
                st.error(f"缺少必要列：{required}")

    with st.expander("📢 Excel 批量导入推广费（自动拆分，覆盖已有）", expanded=False):
        st.markdown("""
        **文件格式**：必须包含列：`期间`、`推广费总额`  
        系统将根据当月旅拍/婚礼订单数及设置的**婚礼占比**自动拆分推广费。  
        **前提**：对应月份的毛客资/订单数必须已导入。
        """)
        st.download_button("📥 下载推广费导入模板",
                           pd.DataFrame(columns=['期间', '推广费总额']).to_csv(index=False).encode('utf-8-sig'),
                           "推广费模板.csv", "text/csv")
        uploaded_promo = st.file_uploader("上传推广费Excel", type=["xlsx", "xls"], key="promo_upload")
        if uploaded_promo:
            df_p = pd.read_excel(uploaded_promo)
            if can_see:
                st.write("预览：", df_p.head())
            else:
                st.write("预览：", mask_dataframe(df_p.head()))
            required_p = ['期间', '推广费总额']
            if all(col in df_p.columns for col in required_p):
                if st.button("导入推广费（自动拆分）", key="confirm_promo_import", disabled=not can_see):
                    db = SessionLocal()
                    try:
                        ratio_rule = db.query(AllocationRule).filter_by(rule_type='promotion_wedding_ratio').first()
                        wedding_ratio = float(ratio_rule.param1) if ratio_rule else 1.0
                        success_count = 0
                        for _, row in df_p.iterrows():
                            p = standardize_period(row['期间'])
                            total_promo = float(row['推广费总额'])
                            travel_stats = db.query(MonthlyStats).filter_by(period=p, business_type='旅拍').first()
                            wedding_stats = db.query(MonthlyStats).filter_by(period=p, business_type='婚礼').first()
                            t_orders = travel_stats.order_count if travel_stats else 0
                            w_orders = wedding_stats.order_count if wedding_stats else 0
                            if can_see:
                                st.write(f"📌 {p}：旅拍订单数 {t_orders}，婚礼订单数 {w_orders}")
                            if not travel_stats or not wedding_stats:
                                st.error(f"❌ {p} 缺少旅拍或婚礼订单数记录，请先导入该月的毛客资和订单数！")
                                continue
                            weighted_total = t_orders + w_orders * wedding_ratio
                            if weighted_total == 0:
                                st.error(f"❌ {p} 旅拍和婚礼订单数均为0，无法拆分推广费")
                                continue
                            travel_promo = total_promo * (t_orders / weighted_total)
                            wedding_promo = total_promo - travel_promo
                            db.query(IndirectCost).filter_by(period=p, cost_item='推广费').delete()
                            db.add(IndirectCost(period=p, cost_item='推广费', business_type='旅拍', amount=travel_promo))
                            db.add(IndirectCost(period=p, cost_item='推广费', business_type='婚礼', amount=wedding_promo))
                            success_count += 1
                            if can_see:
                                st.success(f"✅ {p} 推广费拆分：旅拍 ¥{travel_promo:,.2f}，婚礼 ¥{wedding_promo:,.2f}")
                        db.commit()
                        if success_count > 0:
                            msg = f"🎉 成功导入 {success_count} 个月的推广费！" if can_see else f"🎉 成功导入 *** 个月的推广费！"
                            st.success(msg)
                            st.rerun()
                        else:
                            st.warning("没有导入任何推广费，请检查订单数是否已录入。")
                    except Exception as e:
                        db.rollback()
                        st.error(f"导入失败：{e}")
                    finally:
                        db.close()
            else:
                st.error(f"缺少必要列：{required_p}")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.subheader("旅拍")
        travel_gross = st.number_input("旅拍毛客资", min_value=0, value=0, step=1, key="tg", disabled=not can_see)
        travel_orders = st.number_input("旅拍订单数", min_value=0, value=0, step=1, key="to", disabled=not can_see)
    with col2:
        st.subheader("婚礼")
        wedding_gross = st.number_input("婚礼毛客资", min_value=0, value=0, step=1, key="wg", disabled=not can_see)
        wedding_orders = st.number_input("婚礼订单数", min_value=0, value=0, step=1, key="wo", disabled=not can_see)
    with col3:
        st.subheader("新疆")
        xj_gross = st.number_input("新疆毛客资", min_value=0, value=0, step=1, key="xg", disabled=not can_see)
        xj_orders = st.number_input("新疆订单数", min_value=0, value=0, step=1, key="xo", disabled=not can_see)

    db = SessionLocal()
    saved_stats = db.query(MonthlyStats).filter_by(period=period).all()
    db.close()
    if saved_stats:
        for s in saved_stats:
            if s.business_type == '旅拍':
                travel_gross = s.gross_leads; travel_orders = s.order_count
            elif s.business_type == '婚礼':
                wedding_gross = s.gross_leads; wedding_orders = s.order_count
            elif s.business_type == '新疆':
                xj_gross = s.gross_leads; xj_orders = s.order_count
        st.subheader("📋 本月已保存的基础数据")
        df_ss = pd.DataFrame([{'业务类型': s.business_type, '毛客资': s.gross_leads, '订单数': s.order_count} for s in saved_stats])
        if can_see:
            st.dataframe(df_ss, width='stretch')
        else:
            st.dataframe(mask_dataframe(df_ss), width='stretch')

    if st.button("保存手动录入", disabled=not can_see):
        db = SessionLocal()
        try:
            db.query(MonthlyStats).filter_by(period=period).delete()
            db.add(MonthlyStats(period=period, business_type='旅拍', gross_leads=travel_gross, order_count=travel_orders))
            db.add(MonthlyStats(period=period, business_type='婚礼', gross_leads=wedding_gross, order_count=wedding_orders))
            db.add(MonthlyStats(period=period, business_type='新疆', gross_leads=xj_gross, order_count=xj_orders))
            db.commit()
            st.success("月度数据已保存"); st.rerun()
            add_log(st.session_state.user_id, st.session_state.username, "录入月度基础数据", f"期间{period}")
        except Exception as e:
            db.rollback(); st.error(e)
        finally: db.close()

def allocation_rule_page():
    st.header("⚙️ 推广费分摊设置（婚礼占比）")
    module_name = "⚙️ 推广费分摊设置"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("设置婚礼推广费相对于旅拍的权重。例如：200% 表示一个婚礼订单的推广成本相当于 2 个旅拍订单。")
    db = SessionLocal()
    rule = db.query(AllocationRule).filter_by(rule_type='promotion_wedding_ratio').first()
    current_ratio = float(rule.param1) if rule else 1.0
    wedding_ratio = st.number_input("婚礼推广费占比", min_value=0.0, value=current_ratio, step=0.1,
                                    help="例如输入 2.0 表示婚礼权重是旅拍的 2 倍，即占比 200%", disabled=not can_see)
    if st.button("保存设置", disabled=not can_see):
        try:
            db.query(AllocationRule).filter_by(rule_type='promotion_wedding_ratio').delete()
            db.add(AllocationRule(rule_type='promotion_wedding_ratio', param1=str(wedding_ratio), ratio=0))
            db.commit()
            st.success(f"婚礼占比已更新为 {wedding_ratio*100:.0f}%" if can_see else "婚礼占比已更新")
            add_log(st.session_state.user_id, st.session_state.username, "修改推广费占比", f"婚礼{wedding_ratio}" if can_see else "婚礼***")
        except Exception as e:
            db.rollback(); st.error(e)
    db.close()

def order_query_page():
    st.header("🔍 选片订单查询（绿色=实际，红色=计算）")
    module_name = "🔍 选片订单查询"
    can_see = has_permission(module_name, st.session_state.role)
    db = SessionLocal()
    total = db.query(func.count(Order.order_id)).scalar()
    if can_see:
        st.info(f"数据库共 **{total}** 条选片订单，请使用筛选条件查询（建议选择日期范围）")
    else:
        st.info("数据库共 *** 条选片订单，请使用筛选条件查询")
    db.close()

    # ===== 新增：快速修正选片日期（放在最前面，方便直接使用） =====
    st.subheader("📅 快速修正选片日期")
    if can_see:
        with st.form(key="quick_fix_date"):
            quick_oid = st.text_input("输入订单号", value="")
            quick_date = st.date_input("选择日期", value=date.today())
            quick_submit = st.form_submit_button("更新日期")
            if quick_submit:
                if quick_oid.strip():
                    db_quick = SessionLocal()
                    try:
                        target = db_quick.query(Order).filter_by(order_id=quick_oid.strip()).first()
                        if target:
                            target.selection_date = quick_date
                            db_quick.commit()
                            st.success(f"✅ 订单 {quick_oid.strip()} 日期已更新为 {quick_date}")
                            # 提交后自动刷新，以便在下方查询结果中看到最新日期
                            st.rerun()
                        else:
                            st.error("未找到该订单，请检查订单号是否正确。")
                    except Exception as e:
                        st.error(f"更新失败：{e}")
                    finally:
                        db_quick.close()
                else:
                    st.warning("请输入订单号")
    else:
        st.info("您没有权限修改日期，请联系管理员。")
    st.divider()

    # ===== 原有查询功能 =====
    col1, col2 = st.columns(2)
    with col1:
        order_id_search = st.text_input("订单号（精确匹配，可选）")
    with col2:
        use_date = st.checkbox("按选片日期筛选", value=True)
        if use_date:
            start = st.date_input("开始日期", value=date.today().replace(day=1) - timedelta(days=1))
            end = st.date_input("结束日期", value=date.today())
    if st.button("🔍 查询选片订单"):
        if not order_id_search.strip() and not use_date:
            st.warning("请至少输入订单号或选择日期范围")
            return
        db = SessionLocal()
        try:
            q = db.query(Order)
            if order_id_search.strip():
                q = q.filter(Order.order_id == order_id_search.strip())
            if use_date:
                q = q.filter(Order.selection_date >= start, Order.selection_date <= end)
            orders = q.order_by(Order.selection_date.desc()).all()
            if not orders:
                st.warning("无结果")
                return

            # ----- 构建查询结果表格（原有逻辑不变）-----
            set_names = list({o.set_name for o in orders if o.set_name})
            std_costs = db.query(SetStandardCost).filter(SetStandardCost.set_name.in_(set_names)).all()
            std_dict = {}
            for sc in std_costs:
                if sc.set_name not in std_dict:
                    std_dict[sc.set_name] = {}
                std_dict[sc.set_name][sc.cost_item] = sc.amount
            order_ids = [o.order_id for o in orders]
            actual_costs = db.query(ActualDirectCost).filter(ActualDirectCost.order_id.in_(order_ids)).all()
            act_dict = {}
            actual_existing = set()
            for ac in actual_costs:
                    if ac.order_id not in act_dict:
                        act_dict[ac.order_id] = {}
                    # 同一订单同一费用项取最大值，避免0覆盖非0
                    old_val = act_dict[ac.order_id].get(ac.cost_item, 0)
                    act_dict[ac.order_id][ac.cost_item] = max(old_val or 0, ac.amount or 0)
                    actual_existing.add((ac.order_id, ac.cost_item))
            latest_batch = db.query(
                CherryProductCost.batch_id,
                func.max(CherryProductCost.import_time).label('max_time')
            ).group_by(CherryProductCost.batch_id).order_by(func.max(CherryProductCost.import_time).desc()).first()
            product_costs = {}
            if latest_batch:
                batch_costs = db.query(
                    CherryProductCost.order_id,
                    func.sum(CherryProductCost.product_total_cost).label('total_cost')
                ).filter(CherryProductCost.batch_id == latest_batch.batch_id).group_by(CherryProductCost.order_id).all()
                for pc in batch_costs:
                    product_costs[pc.order_id] = pc.total_cost or 0
            display_items = [
                "微电影拍摄费用", "微电影剪辑费用", "主持", "场地", "搭建",
                "鲜花费用", "拍摄费用", "像素蛋糕修图费", "二销选片费",
                "工厂费用（一销）", "工厂费用（二销）"
            ]
            extra_actual_items = sorted({ac.cost_item for ac in actual_costs} - set(display_items))

            def new_retouch_price(biz, set_price, second_sales):
                if second_sales > 6000:
                    return 15
                if biz == '旅拍':
                    if set_price >= 10980 and second_sales == 0:
                        return 10
                    elif second_sales < 3000:
                        return 9
                    else:
                        return 11
                elif biz == '婚礼':
                    if set_price >= 16980 and second_sales == 0:
                        return 11
                    elif second_sales < 3000:
                        return 10
                    else:
                        return 12
                return 9

            table_data = []
            actual_retouch_orders = set()
            for order in orders:
                set_std = std_dict.get(order.set_name, {})
                order_act = act_dict.get(order.order_id, {})
                cherry_total = product_costs.get(order.order_id, 0) or 0
                selected = order.selected_photos or 0
                extra = order.extra_photos or 0
                photo_cnt = order.photo_count or 0
                base_photos = selected - extra
                if base_photos < 0:
                    base_photos = 0
                total_photos = base_photos + extra
                actual_retouch = order.actual_retouch_fee or 0
                if actual_retouch > 0:
                    actual_retouch_orders.add(order.order_id)
                if actual_retouch > 0 and total_photos > 0:
                    ratio_first = base_photos / total_photos
                    retouch_first = actual_retouch * ratio_first
                    retouch_second = actual_retouch - retouch_first
                else:
                    sel_date = order.selection_date
                    if sel_date and pd.Timestamp(sel_date) >= pd.Timestamp('2026-04-01'):
                        unit_price = new_retouch_price(order.type, order.set_price or 0, order.second_sales or 0)
                    else:
                        default_price = 9 if order.type == '旅拍' else 10
                        unit_price = set_std.get('修片单价', default_price)
                    retouch_first = base_photos * unit_price
                    retouch_second = extra * unit_price
                sel_date = order.selection_date
                if sel_date and pd.Timestamp(sel_date) >= pd.Timestamp('2026-04-01'):
                    pixel_cake = photo_cnt * 0.55 * 0.08
                else:
                    pixel_cake = photo_cnt * 0.55 * 0.1
                factory_std = set_std.get('工厂费用', 0) or 0
                if (order.order_id, '工厂费用') in actual_existing:
                    actual_factory = order_act.get('工厂费用', 0)
                    total_factory = actual_factory if actual_factory > 0 else cherry_total
                else:
                    total_factory = cherry_total
                if total_factory == 0:
                    total_factory = factory_std
                factory_first = min(total_factory, factory_std)
                factory_second = total_factory - factory_first
                clip_std = set_std.get('微电影剪辑费用', 0) or set_std.get('微电影剪辑费', 0)
                if (order.order_id, '微电影剪辑费用') in actual_existing:
                    actual_val = order_act.get('微电影剪辑费用', 0)
                    row_val = actual_val if actual_val > 0 else clip_std
                else:
                    row_val = clip_std
                row = {
                    "订单号": order.order_id,
                    "客户姓名": order.customer_name,
                    "业务类型": order.type,
                    "套系名称": order.set_name,
                    "套系金额": order.set_price,
                    "二销金额": order.second_sales,
                    "客诉退款": order.refund,
                    "选片时间": order.selection_date.strftime("%Y-%m-%d") if order.selection_date else "",
                    "选片张数": selected,
                    "加修张数": extra,
                    "拍照张数": photo_cnt,
                    "实际修片费(整单)": actual_retouch,
                    "一销修片费": round(retouch_first, 2),
                    "二销修片费": round(retouch_second, 2),
                    "像素蛋糕修图费": round(pixel_cake, 2),
                    "微电影剪辑费用": round(row_val, 2),
                    "工厂费用（一销）": round(factory_first, 2),
                    "工厂费用（二销）": round(factory_second, 2),
                }
                for item in display_items:
                    if item in ["工厂费用（一销）", "工厂费用（二销）", "微电影剪辑费用", "像素蛋糕修图费"]:
                        continue
                    row[item] = order_act.get(item, 0)
                for item in extra_actual_items:
                    row[item] = order_act.get(item, 0)
                table_data.append(row)

            df_result = pd.DataFrame(table_data)
            cost_cols = display_items + extra_actual_items + ['一销修片费', '二销修片费', '像素蛋糕修图费']
            cost_cols = [c for c in cost_cols if c in df_result.columns]

            def is_actual(oid, col_name):
                if col_name == '像素蛋糕修图费':
                    return False
                if col_name in ('一销修片费', '二销修片费'):
                    return oid in actual_retouch_orders
                if col_name in ('工厂费用（一销）', '工厂费用（二销）'):
                    return (oid, '工厂费用') in actual_existing
                return (oid, col_name) in actual_existing

            def color_cells(df):
                style_df = pd.DataFrame('', index=df.index, columns=df.columns)
                for col in df.columns:
                    if col in cost_cols:
                        for idx in df.index:
                            oid = df.at[idx, '订单号']
                            if is_actual(oid, col):
                                style_df.at[idx, col] = 'color: green'
                            else:
                                style_df.at[idx, col] = 'color: red'
                return style_df

            if can_see:
                styled_df = df_result.style.apply(color_cells, axis=None)
                st.dataframe(styled_df, width='stretch')
            else:
                st.dataframe(mask_dataframe(df_result), width='stretch')

            st.success(f"共 {len(orders)} 个选片订单" if can_see else "共 *** 个选片订单")
            csv_data = df_result.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 导出查询结果为CSV", csv_data, f"选片订单查询_{datetime.now().strftime('%Y%m%d%H%M')}.csv", "text/csv")

            add_log(st.session_state.user_id, st.session_state.username, "选片订单查询", f"结果{len(orders)}条" if can_see else "结果***条")
        finally:
            db.close()

def render_shoot_coverage_check(imported_order_ids):
    """导入完成后，自动检查导入订单所在期间的全部订单拍摄费用覆盖率，
    并提供缺失订单的 CSV 下载按钮。"""
    if not imported_order_ids:
        return

    from calendar import monthrange

    db = SessionLocal()
    try:
        # 1. 找出本次导入订单的选片月份和业务类型
        imported_orders = db.query(Order).filter(
            Order.order_id.in_(list(imported_order_ids))
        ).all()
        if not imported_orders:
            return

        months = sorted({o.selection_date.strftime('%Y-%m')
                         for o in imported_orders if o.selection_date})
        if not months:
            return

        # 2. 确定日期范围
        min_m = months[0]
        max_m = months[-1]
        y1, m1 = map(int, min_m.split('-'))
        y2, m2 = map(int, max_m.split('-'))
        start = date(y1, m1, 1)
        end = date(y2, m2, monthrange(y2, m2)[1])

        # 3. 判断业务类型（依据本次导入订单的套系/type）
        xj_count = sum(1 for o in imported_orders if '新疆' in (o.set_name or ''))
        if xj_count == len(imported_orders):
            biz_label = '新疆'
            def biz_filter(q):
                return q.filter(Order.set_name.contains('新疆'))
        else:
            travel_count = sum(1 for o in imported_orders if o.type == '旅拍')
            wedding_count = sum(1 for o in imported_orders if o.type == '婚礼')
            if travel_count == len(imported_orders):
                biz_label = '旅拍'
                def biz_filter(q):
                    return q.filter(Order.type == '旅拍')
            elif wedding_count == len(imported_orders):
                biz_label = '婚礼'
                def biz_filter(q):
                    return q.filter(Order.type == '婚礼')
            else:
                biz_label = '全部'
                def biz_filter(q):
                    return q

        # 4. 查询该期间所有订单
        q = db.query(Order).filter(
            Order.selection_date >= start,
            Order.selection_date <= end
        )
        q = biz_filter(q)
        period_orders = q.all()
        if not period_orders:
            return

        order_map = {o.order_id: o for o in period_orders}
        oids = set(order_map.keys())

        # 5. 查询拍摄费用记录
        shoot_records = db.query(ActualDirectCost).filter(
            ActualDirectCost.cost_item == '拍摄费用',
            ActualDirectCost.order_id.in_(oids)
        ).all()
        with_shoot = {r.order_id for r in shoot_records}

        missing = sorted(oids - with_shoot)
        total = len(period_orders)
        covered = len(with_shoot)
        coverage = covered / total * 100 if total else 0

        # 6. 展示检查结果
        st.divider()
        st.subheader("📊 拍摄费用覆盖率检查")
        col1, col2, col3 = st.columns(3)
        col1.metric("期间订单总数", total)
        col2.metric("已有拍摄费用", covered)
        col3.metric("缺少拍摄费用", len(missing),
                    delta=f"覆盖率 {coverage:.1f}%", delta_color="off")
        st.caption(f"检查范围：{start} ~ {end} · {biz_label}")

        if missing:
            st.warning(f"⚠️ 仍有 {len(missing)} 个订单缺少拍摄费用，请检查是否漏导。")
            df_missing = pd.DataFrame([{
                '订单号': oid,
                '套系': order_map[oid].set_name or '',
                '业务类型': order_map[oid].type or '',
                '选片日期': order_map[oid].selection_date,
            } for oid in missing])
            st.dataframe(df_missing, width='stretch')
            csv = df_missing.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                "📥 下载缺失拍摄费用的订单 CSV",
                csv,
                f"缺少拍摄费用订单_{start}_{end}_{biz_label}.csv",
                "text/csv",
                key=f"missing_shoot_download_{min_m}_{biz_label}"
            )
        else:
            st.success(f"✅ 所有 {total} 个订单都有拍摄费用")
    finally:
        db.close()

def shooting_bill_import_page():
    st.markdown("### 📸 拍摄费用账单")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 支持两种格式的拍摄账单（格式A/格式B）。  
    - 鲜花费用自动并入“赠送”。  
    - **重复处理新增“合并金额”选项**：选择后会将新旧金额相加，适用于退款或追加费用。
    """)
    with st.expander("📋 格式说明", expanded=False):
        st.markdown("""
        **格式B（推荐）** 示例列：`订单号`、`结算金额`、`基础金额`、`餐费`、`仪式补助`、`住宿`、`赠送`、`仪式`、`上山补助`、`景点1`~`景点4`、`减费用`  
        **格式A** 示例列：`订单号`、`结算金额`、`结算详情`（如 `2499+180+云杉坪1200`）
        """)
    uploaded = st.file_uploader("上传拍摄账单Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        for col in df.select_dtypes(include=['object', 'string']).columns:
            df[col] = df[col].astype(str)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        if '订单号' not in df.columns or '结算金额' not in df.columns:
            st.error("缺少必要列：订单号、结算金额")
            return
        df['结算金额'] = pd.to_numeric(df['结算金额'], errors='coerce')
        df = df.dropna(subset=['订单号', '结算金额'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[~df['订单号'].isin(['', 'nan', 'None', 'NaN', 'null'])]
        df = df.dropna(subset=['订单号'])

        detail_cols = ['基础金额', '餐费', '仪式补助', '住宿', '赠送', '仪式', '上山补助', '减费用']
        has_detail_cols = any(c in df.columns for c in detail_cols)

        spot_names = [
            "丽江古城", "哈里谷", "东巴谷", "情人湖", "雪山远景", "云上牧场", "有你牧场",
            "东巴大峡谷", "夕禾马场", "66号公路", "雪山马场", "原野牧场", "山野牧场", "玉湖",
            "光头强牧场", "姊妹湖", "甘海子", "文海", "拉市海", "龙门镖局", "七号营地",
            "S弯公路", "莫奈庄园", "蔓禾婚礼主题", "法缦主题", "法缦外景", "西西里",
            "法缦雪山庄园", "糊涂窝", "艾洛可公路", "艾洛可庄园", "听花谷", "日照金山",
            "地中海", "蓝月谷", "阿美泽婚礼教堂", "云杉坪", "蓝月谷+云杉坪", "麓雪秘境",
            "九子海小木屋", "木府", "一拍即合基地", "漫步时光", "玉湖秘境", "云上山下",
            "璞野牧场", "乌龙坝", "木莲公园", "云顶基地二期", "喜洲麦田", "丽舍庄园",
            "咸甜餐厅", "半山悦", "者摩山", "天空之城", "垒翠园", "海西里里", "花醉艺术空间",
            "罗荃半岛", "天镜湾", "归鲤小镇", "云想山", "白马艺术庄园", "童话山海基地",
            "苍山植物园", "云顶婚礼主题", "山茶园", "半山半岛", "华彬基地", "华尔兹",
            "秘密空间", "苍山高尔夫", "猫咪花园", "森林剧场", "大理蓝月谷", "赛湖",
            "库尔德宁", "恰西", "赛湖道具车", "果子沟骑马", "库尔德宁/恰西骑马", "将军沟",
            "果子沟小树林", "海颂", "果子沟", "罗荃云顶", "归鲤", "阿美泽", "归鲤云顶",
            "艾洛克", "赛里木湖", "法缦", "栖溪谷", "罗荃"
        ]

        def extract_numbers(s):
            if pd.isna(s) or str(s).strip() == '':
                return 0.0
            nums = re.findall(r'\d+\.?\d*', str(s))
            return sum(float(n) for n in nums)

        records = []
        duplicates_info = []
        db = SessionLocal()
        try:
            for _, row in df.iterrows():
                oid = row['订单号']
                if not oid: continue
                total_settlement = float(row['结算金额'])
                original_detail = str(row.get('结算详情', ''))
                detail_map = {}

                if has_detail_cols:
                    if '基础金额' in df.columns:
                        val = extract_numbers(row['基础金额'])
                        if val > 0: detail_map['摄化费用'] = val
                    if '餐费' in df.columns:
                        val = extract_numbers(row['餐费'])
                        if val > 0: detail_map['餐费'] = val
                    for col in ['仪式补助', '上山补助']:
                        if col in df.columns:
                            val = extract_numbers(row[col])
                            if val > 0:
                                detail_map['补助费用'] = detail_map.get('补助费用', 0) + val
                    if '住宿' in df.columns:
                        val = extract_numbers(row['住宿'])
                        if val > 0: detail_map['酒店成本'] = val
                    if '赠送' in df.columns:
                        val = extract_numbers(row['赠送'])
                        if val > 0: detail_map['赠送'] = val
                    if '仪式' in df.columns:
                        val = extract_numbers(row['仪式'])
                        if val > 0: detail_map['仪式费用'] = val
                    for i in range(1, 5):
                        col_name = f'景点{i}'
                        if col_name in df.columns:
                            val = extract_numbers(row[col_name])
                            if val > 0:
                                detail_map['景点费'] = detail_map.get('景点费', 0) + val
                    if '减费用' in df.columns:
                        deduct = extract_numbers(row['减费用'])
                        if deduct > 0:
                            detail_map['摄化费用'] = detail_map.get('摄化费用', 0) - deduct
                    if detail_map.get('摄化费用', 0) < 0:
                        detail_map['摄化费用'] = 0
                    parsed_sum = sum(detail_map.values())
                    if total_settlement != parsed_sum:
                        detail_map['摄化费用'] = detail_map.get('摄化费用', 0) + (total_settlement - parsed_sum)
                else:
                    if pd.notna(original_detail) and original_detail.strip():
                        processed = str(original_detail).replace('-', '+-')
                        if processed.startswith('+'): processed = processed[1:]
                        parts = [p.strip() for p in processed.split('+') if p.strip()]
                        for part in parts:
                            is_negative = part.startswith('-')
                            if is_negative: part = part[1:]
                            mul_match = re.search(r'(\d+)\*(\d+)', part)
                            if mul_match:
                                mul_val = float(mul_match.group(1)) * float(mul_match.group(2))
                                desc = part[:mul_match.start()].strip() + part[mul_match.end():].strip()
                                amount = mul_val * (-1 if is_negative else 1)
                            else:
                                nums = re.findall(r'\d+', part)
                                if not nums: continue
                                amount = float(nums[-1]) * (-1 if is_negative else 1)
                                desc = re.sub(r'\d+', '', part).strip().lstrip('-').strip()
                            if desc == '' and abs(amount) == 180:
                                key = '酒店成本'
                            elif '鲜花' in desc:
                                key = '鲜花费用'
                            elif any(spot in desc for spot in spot_names):
                                key = '景点费'
                            elif '酒店' in desc or '住宿' in desc or '房差' in desc:
                                key = '酒店成本'
                            elif '上山补助' in desc or '补助' in desc:
                                key = '补助费用'
                            elif '仪式' in desc:
                                key = '仪式费用'
                            elif desc == '':
                                if abs(amount) > 720: key = '摄化费用'
                                else: key = '其他'
                            else:
                                key = '其他'
                            detail_map[key] = detail_map.get(key, 0) + amount
                    if '鲜花费用' in detail_map:
                        flower_val = detail_map.pop('鲜花费用')
                        detail_map['赠送'] = detail_map.get('赠送', 0) + flower_val
                    parsed_sum = sum(detail_map.values())
                    if total_settlement != parsed_sum:
                        detail_map['摄化费用'] = detail_map.get('摄化费用', 0) + (total_settlement - parsed_sum)

                existing = db.query(ActualDirectCost).filter(
                    ActualDirectCost.order_id == oid,
                    ActualDirectCost.cost_item == '拍摄费用'
                ).first()
                if existing:
                    duplicates_info.append((oid, existing.amount, total_settlement))
                detail_str = f"原始:{original_detail}||解析:" + '; '.join([f"{k}:{v}" for k, v in detail_map.items()])
                records.append((oid, total_settlement, detail_str))
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有拍摄费用，请选择：")
                dup_df = pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额'])
                st.dataframe(dup_df)
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", [
                "覆盖已有记录",
                "仅导入新记录（跳过已有）",
                "合并金额（新旧相加）"
            ])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入"):
            db = SessionLocal()
            try:
                dup_oids = {d[0] for d in duplicates_info}

                if handle_dup == "覆盖已有记录":
                    # 先删除所有已存在的记录
                    for oid in dup_oids:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='拍摄费用').delete()
                    db.commit()
                    # 然后插入全部新数据
                    for oid, amount, remark in records:
                        db.add(ActualDirectCost(order_id=oid, cost_item='拍摄费用', amount=amount, remark=remark))

                elif handle_dup == "合并金额（新旧相加）":
                    # 已存在的记录：删除后插入合并金额
                    for oid, old_amt, new_amt in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='拍摄费用').delete()
                        merged_amount = old_amt + new_amt
                        remark = next((r[2] for r in records if r[0] == oid), "")
                        db.add(ActualDirectCost(order_id=oid, cost_item='拍摄费用', amount=merged_amount, remark=remark))
                    # 新订单：直接插入
                    for oid, amount, remark in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='拍摄费用', amount=amount, remark=remark))

                else:  # 仅导入新记录
                    for oid, amount, remark in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='拍摄费用', amount=amount, remark=remark))

                db.commit()
                msg = f"成功导入 {len(records)} 条记录" if can_see else "成功导入 *** 条记录"
                st.success(msg)
                add_log(st.session_state.user_id, st.session_state.username, "导入拍摄账单", f"导入{len(records)}条" if can_see else "导入***条")
                # ===== 覆盖率检查 =====
                imported_oids = [r[0] for r in records]
                render_shoot_coverage_check(imported_oids)
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def delivery_cost_import_page():
    st.markdown("### 💒 交付费用（主持/搭建/场地/鲜花）")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传 Excel 文件，需包含列：`日期`、`订单号`、`搭建费`、`场地费`、`主持费`、`鲜花费`。  
    - 费用项将分别导入为 `搭建`、`场地`、`主持`、`鲜花费用`。  
    - 重复处理：覆盖 / 仅导入新记录 / 合并金额。  
    - 业务日期记录在备注中。
    """)
    uploaded = st.file_uploader("上传交付费用Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        col_map = {}
        for col in df.columns:
            col_str = str(col).strip()
            if '订单号' in col_str: col_map['订单号'] = col
            elif '搭建' in col_str and '费' in col_str: col_map['搭建费'] = col
            elif '场地' in col_str and '费' in col_str: col_map['场地费'] = col
            elif '主持' in col_str and '费' in col_str: col_map['主持费'] = col
            elif '鲜花' in col_str and '费' in col_str: col_map['鲜花费'] = col
            elif '日期' in col_str: col_map['日期'] = col
        missing = [k for k in ['订单号', '搭建费', '场地费', '主持费', '鲜花费'] if k not in col_map]
        if missing:
            st.error(f"缺少必要列：{missing}")
            return
        df = df.rename(columns={v: k for k, v in col_map.items()})
        for fee_col in ['搭建费', '场地费', '主持费', '鲜花费']:
            df[fee_col] = pd.to_numeric(df[fee_col], errors='coerce').fillna(0)
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[~df['订单号'].isin(['', 'nan', 'None', 'NaN', 'null'])]
        df = df.dropna(subset=['订单号'])
        if df.empty:
            st.warning("没有有效订单数据")
            return

        records = []
        for _, row in df.iterrows():
            oid = row['订单号']
            raw_date = row.get('日期', '')
            try:
                parsed_date = pd.to_datetime(raw_date).date().isoformat() if pd.notna(raw_date) and str(raw_date).strip() else ''
            except:
                parsed_date = str(raw_date).strip()
            remark = f"日期:{parsed_date}" if parsed_date else ""
            for fee_name, cost_item in [('搭建费', '搭建'), ('场地费', '场地'), ('主持费', '主持'), ('鲜花费', '鲜花费用')]:
                amt = row[fee_name]
                if amt != 0:
                    records.append((oid, cost_item, amt, remark))
        if not records:
            st.warning("无有效费用数据")
            return

        db = SessionLocal()
        try:
            oids = list({r[0] for r in records})
            items = list({r[1] for r in records})
            existing = db.query(ActualDirectCost).filter(
                ActualDirectCost.order_id.in_(oids),
                ActualDirectCost.cost_item.in_(items)
            ).all()
            existing_map = {(e.order_id, e.cost_item): e.amount for e in existing}
            duplicates_info = [(r[0], r[1], existing_map.get((r[0], r[1]), 0), r[2]) for r in records if (r[0], r[1]) in existing_map]
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个费用项已有记录，请选择：")
                st.dataframe(pd.DataFrame(duplicates_info, columns=['订单号', '费用项', '已有金额', '本次金额']))
            else:
                st.warning("检测到重复记录，请选择处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", ["覆盖已有记录", "仅导入新记录（跳过已有）", "合并金额（新旧相加）"])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入交付费用"):
            db = SessionLocal()
            try:
                dup_keys = {(d[0], d[1]) for d in duplicates_info}

                if handle_dup == "覆盖已有记录":
                    for (oid, item) in dup_keys:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item=item).delete()
                    db.commit()
                    for oid, item, amt, remark in records:
                        db.add(ActualDirectCost(order_id=oid, cost_item=item, amount=amt, remark=remark))

                elif handle_dup == "合并金额（新旧相加）":
                    for (oid, item, old_amt, new_amt) in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item=item).delete()
                        merged = old_amt + new_amt
                        remark = next((r[3] for r in records if r[0] == oid and r[1] == item), "")
                        db.add(ActualDirectCost(order_id=oid, cost_item=item, amount=merged, remark=remark))
                    for oid, item, amt, remark in records:
                        if (oid, item) in dup_keys:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item=item, amount=amt, remark=remark))

                else:  # 仅导入新记录
                    for oid, item, amt, remark in records:
                        if (oid, item) in dup_keys:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item=item, amount=amt, remark=remark))

                db.commit()
                st.success(f"成功导入 {len(records)} 条费用" if can_see else "成功导入 *** 条费用")
                add_log(st.session_state.user_id, st.session_state.username, "导入交付费用", f"共{len(records)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def venue_self_rent_import_page():
    st.markdown("### 🏟️ 自租场地消耗导入")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传 Excel 文件，需包含列：`订单号`、`成本`。  
    - 导入「场地」实际直接成本，与交付费用中的场地费合并。  
    - 重复处理：覆盖 / 仅导入新记录 / 合并金额。
    """)
    uploaded = st.file_uploader("上传自租场地消耗Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        oid_col = next((c for c in df.columns if '订单号' in str(c)), None)
        cost_col = next((c for c in df.columns if '成本' in str(c)), None)
        if not oid_col:
            st.error("缺少「订单号」列")
            return
        if not cost_col:
            st.error("缺少「成本」列")
            return
        df = df.rename(columns={oid_col: '订单号', cost_col: '成本'})
        df['成本'] = pd.to_numeric(df['成本'], errors='coerce').fillna(0)
        df = df.dropna(subset=['订单号', '成本'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[~df['订单号'].isin(['', 'nan', 'None', 'NaN'])]
        records = [(row['订单号'], float(row['成本'])) for _, row in df.iterrows()]
        if not records:
            st.warning("无有效数据")
            return
        st.info(f"共解析 {len(records)} 条记录" if can_see else "共解析 *** 条记录")

        db = SessionLocal()
        try:
            oids = [r[0] for r in records]
            existing = db.query(ActualDirectCost).filter(
                ActualDirectCost.order_id.in_(oids),
                ActualDirectCost.cost_item == '场地'
            ).all()
            existing_map = {e.order_id: e.amount for e in existing}
            duplicates_info = [(oid, existing_map.get(oid, 0), amt) for oid, amt in records if oid in existing_map]
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有场地费用，请选择：")
                st.dataframe(pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额']))
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", ["覆盖已有记录", "仅导入新记录（跳过已有）", "合并金额（新旧相加）"])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入自租场地费用"):
            db = SessionLocal()
            try:
                dup_oids = {d[0] for d in duplicates_info}

                if handle_dup == "覆盖已有记录":
                    for oid in dup_oids:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='场地').delete()
                    db.commit()
                    for oid, amt in records:
                        db.add(ActualDirectCost(order_id=oid, cost_item='场地', amount=amt, remark='自租场地'))

                elif handle_dup == "合并金额（新旧相加）":
                    for oid, old_amt, new_amt in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='场地').delete()
                        db.add(ActualDirectCost(order_id=oid, cost_item='场地', amount=old_amt + new_amt, remark='自租场地'))
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='场地', amount=amt, remark='自租场地'))

                else:
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='场地', amount=amt, remark='自租场地'))

                db.commit()
                st.success(f"成功导入 {len(records)} 条场地费用" if can_see else "成功导入 *** 条场地费用")
                add_log(st.session_state.user_id, st.session_state.username, "导入自租场地消耗", f"共{len(records)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def micro_film_shooting_import_page():
    st.markdown("### 🎬 微电影拍摄账单")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传供应商原始格式，自动提取订单号和价格。  
    - 取「价格合计」列；若为空则用「价格（主机）」+「价格(辅机)」。  
    - 重复处理：覆盖 / 仅导入新记录 / 合并金额。
    """)
    uploaded = st.file_uploader("上传微电影拍摄账单Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览（前5行）：", df.head())
        else:
            st.write("预览（前5行）：", mask_dataframe(df.head()))
        if '订单号' not in df.columns:
            st.error("缺少「订单号」列")
            return
        if '价格合计' not in df.columns and ('价格（主机）' not in df.columns or '价格(辅机）' not in df.columns):
            st.error("缺少价格列")
            return

        df = df.rename(columns={'价格(辅机）': '辅机', '价格（主机）': '主机', '价格合计': '合计'})
        for col in ['合计', '主机', '辅机']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0

        df['最终金额'] = df['合计']
        mask = df['最终金额'] == 0
        df.loc[mask, '最终金额'] = df.loc[mask, '主机'] + df.loc[mask, '辅机']
        df = df.dropna(subset=['订单号', '最终金额'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[~df['订单号'].isin(['', 'nan', 'None', 'NaN'])]
        df = df[df['最终金额'] > 0]

        records = [(row['订单号'], float(row['最终金额'])) for _, row in df.iterrows()]
        if not records:
            st.warning("无有效订单数据")
            return

        st.info(f"共解析 {len(records)} 条有效记录" if can_see else "共解析 *** 条有效记录")
        db = SessionLocal()
        try:
            oids = [r[0] for r in records]
            existing = db.query(ActualDirectCost).filter(
                ActualDirectCost.order_id.in_(oids),
                ActualDirectCost.cost_item == '微电影拍摄费用'
            ).all()
            existing_map = {e.order_id: e.amount for e in existing}
            duplicates_info = [(oid, existing_map.get(oid, 0), amt) for oid, amt in records if oid in existing_map]
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有拍摄费用，请选择：")
                st.dataframe(pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额']))
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", ["覆盖已有记录", "仅导入新记录（跳过已有）", "合并金额（新旧相加）"])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入微电影拍摄费用"):
            db = SessionLocal()
            try:
                dup_oids = {d[0] for d in duplicates_info}

                if handle_dup == "覆盖已有记录":
                    for oid in dup_oids:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='微电影拍摄费用').delete()
                    db.commit()
                    for oid, amt in records:
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影拍摄费用', amount=amt))

                elif handle_dup == "合并金额（新旧相加）":
                    for oid, old_amt, new_amt in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='微电影拍摄费用').delete()
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影拍摄费用', amount=old_amt + new_amt))
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影拍摄费用', amount=amt))

                else:
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影拍摄费用', amount=amt))

                db.commit()
                st.success(f"成功导入 {len(records)} 条微电影拍摄费用" if can_see else "成功导入 *** 条")
                add_log(st.session_state.user_id, st.session_state.username, "导入微电影拍摄账单", f"共{len(records)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def micro_film_editing_import_page():
    st.markdown("### ✂️ 微电影剪辑账单")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传供应商原始格式，自动提取订单号和价格。  
    - 重复处理：覆盖 / 仅导入新记录 / 合并金额。
    """)
    uploaded = st.file_uploader("上传微电影剪辑账单Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览（前5行）：", df.head())
        else:
            st.write("预览（前5行）：", mask_dataframe(df.head()))
        oid_col = next((c for c in df.columns if '订单号' in str(c)), None)
        price_col = next((c for c in df.columns if '价格' in str(c)), None)
        if not oid_col or not price_col:
            st.error("缺少必要列：订单号、价格")
            return
        df = df.rename(columns={oid_col: '订单号', price_col: '价格'})
        df['价格'] = pd.to_numeric(df['价格'], errors='coerce').fillna(0)
        df = df.dropna(subset=['订单号', '价格'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[df['订单号'] != '']
        df = df[df['价格'] > 0]

        records = [(row['订单号'], float(row['价格'])) for _, row in df.iterrows()]
        if not records:
            st.warning("无有效订单数据")
            return
        st.info(f"共解析 {len(records)} 条有效记录" if can_see else "共解析 *** 条")
        db = SessionLocal()
        try:
            oids = [r[0] for r in records]
            existing = db.query(ActualDirectCost).filter(
                ActualDirectCost.order_id.in_(oids),
                ActualDirectCost.cost_item == '微电影剪辑费用'
            ).all()
            existing_map = {e.order_id: e.amount for e in existing}
            duplicates_info = [(oid, existing_map.get(oid, 0), amt) for oid, amt in records if oid in existing_map]
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有剪辑费用，请选择：")
                st.dataframe(pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额']))
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", ["覆盖已有记录", "仅导入新记录（跳过已有）", "合并金额（新旧相加）"])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入微电影剪辑费用"):
            db = SessionLocal()
            try:
                dup_oids = {d[0] for d in duplicates_info}

                if handle_dup == "覆盖已有记录":
                    for oid in dup_oids:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='微电影剪辑费用').delete()
                    db.commit()
                    for oid, amt in records:
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影剪辑费用', amount=amt))

                elif handle_dup == "合并金额（新旧相加）":
                    for oid, old_amt, new_amt in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='微电影剪辑费用').delete()
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影剪辑费用', amount=old_amt + new_amt))
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影剪辑费用', amount=amt))

                else:
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='微电影剪辑费用', amount=amt))

                db.commit()
                st.success(f"成功导入 {len(records)} 条微电影剪辑费用" if can_see else "成功导入 *** 条")
                add_log(st.session_state.user_id, st.session_state.username, "导入微电影剪辑账单", f"共{len(records)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def second_sales_import_page():
    st.markdown("### 🛒 二销选片账单（门店二销款结算费）")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传供应商格式，提取「订单号」和「二销结算金额」。  
    - 导入「二销选片费」实际成本。  
    - 重复处理：覆盖 / 仅导入新记录 / 合并金额。
    """)
    uploaded = st.file_uploader("上传二销选片账单Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览（前5行）：", df.head())
        else:
            st.write("预览（前5行）：", mask_dataframe(df.head()))
        if '订单号' not in df.columns or '二销结算金额' not in df.columns:
            st.error("缺少必要列：订单号、二销结算金额")
            return
        df = df.dropna(subset=['订单号', '二销结算金额'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        records = [(row['订单号'], float(row['二销结算金额'])) for _, row in df.iterrows()]
        if not records:
            st.warning("无有效数据")
            return
        st.info(f"共解析 {len(records)} 条记录" if can_see else "共解析 *** 条记录")
        db = SessionLocal()
        try:
            oids = [r[0] for r in records]
            existing = db.query(ActualDirectCost).filter(
                ActualDirectCost.order_id.in_(oids),
                ActualDirectCost.cost_item == '二销选片费'
            ).all()
            existing_map = {e.order_id: e.amount for e in existing}
            duplicates_info = [(oid, existing_map.get(oid, 0), amt) for oid, amt in records if oid in existing_map]
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有二销选片费，请选择：")
                st.dataframe(pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额']))
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", ["覆盖已有记录", "仅导入新记录（跳过已有）", "合并金额（新旧相加）"])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入二销选片费"):
            db = SessionLocal()
            try:
                dup_oids = {d[0] for d in duplicates_info}

                if handle_dup == "覆盖已有记录":
                    for oid in dup_oids:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='二销选片费').delete()
                    db.commit()
                    for oid, amt in records:
                        db.add(ActualDirectCost(order_id=oid, cost_item='二销选片费', amount=amt))

                elif handle_dup == "合并金额（新旧相加）":
                    for oid, old_amt, new_amt in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='二销选片费').delete()
                        db.add(ActualDirectCost(order_id=oid, cost_item='二销选片费', amount=old_amt + new_amt))
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='二销选片费', amount=amt))

                else:
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='二销选片费', amount=amt))

                db.commit()
                st.success(f"成功导入 {len(records)} 条二销选片费" if can_see else "成功导入 *** 条")
                add_log(st.session_state.user_id, st.session_state.username, "导入二销选片账单", f"共{len(records)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def retouch_bill_import_page():
    st.markdown("### 🖼️ 修片账单导入")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传 Excel 文件，需包含列：`订单号`、`小计`（或"修片费"、"金额"）。  
    - 该费用将更新订单的「实际修片费(整单)」。  
    - 重复处理：覆盖 / 仅导入新记录 / 合并金额。
    """)
    uploaded = st.file_uploader("上传修片账单Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        oid_col = next((c for c in df.columns if '订单号' in str(c)), None)
        fee_col = next((c for c in df.columns if '小计' in str(c) or '修片费' in str(c) or '金额' in str(c)), None)
        if not oid_col:
            st.error("缺少「订单号」列")
            return
        if not fee_col:
            st.error("缺少修片费列")
            return
        df = df.rename(columns={oid_col: '订单号', fee_col: '修片费'})
        df['修片费'] = pd.to_numeric(df['修片费'], errors='coerce').fillna(0)
        df = df.dropna(subset=['订单号', '修片费'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[~df['订单号'].isin(['', 'nan', 'None', 'NaN'])]
        records = [(row['订单号'], float(row['修片费'])) for _, row in df.iterrows()]
        if not records:
            st.warning("无有效数据")
            return
        st.info(f"共解析 {len(records)} 条记录" if can_see else "共解析 *** 条记录")

        db = SessionLocal()
        try:
            oids = [r[0] for r in records]
            existing_orders = db.query(Order).filter(Order.order_id.in_(oids)).all()
            existing_map = {o.order_id: (o.actual_retouch_fee or 0) for o in existing_orders}
            duplicates_info = [(oid, existing_map.get(oid, 0), amt) for oid, amt in records if oid in existing_map]
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有修片费，请选择：")
                st.dataframe(pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额']))
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", ["覆盖已有记录", "仅导入新记录（跳过已有）", "合并金额（新旧相加）"])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入修片费"):
            db = SessionLocal()
            try:
                for oid, amt in records:
                    if handle_dup == "仅导入新记录（跳过已有）" and oid in existing_map:
                        continue
                    if handle_dup == "合并金额（新旧相加）" and oid in existing_map:
                        new_amt = existing_map[oid] + amt
                    else:  # 覆盖 或 新增
                        new_amt = amt
                    order = db.query(Order).filter_by(order_id=oid).first()
                    if order:
                        order.actual_retouch_fee = new_amt
                    else:
                        db.add(Order(order_id=oid, actual_retouch_fee=new_amt))
                db.commit()
                st.success(f"成功导入 {len(records)} 条修片费" if can_see else "成功导入 *** 条")
                add_log(st.session_state.user_id, st.session_state.username, "导入修片账单", f"共{len(records)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def factory_bill_import_page():
    st.markdown("### 🏭 工厂账单导入")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传 Excel 文件，需包含列：`订单号`、`合计`（或"金额"、"总价"）。  
    - 导入「工厂费用」实际直接成本。  
    - 重复处理：覆盖 / 仅导入新记录 / 合并金额。
    """)
    uploaded = st.file_uploader("上传工厂账单Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        oid_col = next((c for c in df.columns if '订单号' in str(c)), None)
        amt_col = next((c for c in df.columns if '合计' in str(c) or '金额' in str(c) or '总价' in str(c)), None)
        if not oid_col:
            st.error("缺少「订单号」列")
            return
        if not amt_col:
            st.error("缺少金额列")
            return
        df = df.rename(columns={oid_col: '订单号', amt_col: '工厂费用'})
        df['工厂费用'] = pd.to_numeric(df['工厂费用'], errors='coerce').fillna(0)
        df = df.dropna(subset=['订单号', '工厂费用'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[~df['订单号'].isin(['', 'nan', 'None', 'NaN'])]
        df = df[df['工厂费用'] > 0]

        records = [(row['订单号'], float(row['工厂费用'])) for _, row in df.iterrows()]
        if not records:
            st.warning("无有效数据")
            return
        st.info(f"共解析 {len(records)} 条记录" if can_see else "共解析 *** 条记录")

        db = SessionLocal()
        try:
            oids = [r[0] for r in records]
            existing = db.query(ActualDirectCost).filter(
                ActualDirectCost.order_id.in_(oids),
                ActualDirectCost.cost_item == '工厂费用'
            ).all()
            existing_map = {e.order_id: e.amount for e in existing}
            duplicates_info = [(oid, existing_map.get(oid, 0), amt) for oid, amt in records if oid in existing_map]
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有工厂费用，请选择：")
                st.dataframe(pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额']))
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", ["覆盖已有记录", "仅导入新记录（跳过已有）", "合并金额（新旧相加）"])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入工厂费用"):
            db = SessionLocal()
            try:
                dup_oids = {d[0] for d in duplicates_info}

                if handle_dup == "覆盖已有记录":
                    for oid in dup_oids:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='工厂费用').delete()
                    db.commit()
                    for oid, amt in records:
                        db.add(ActualDirectCost(order_id=oid, cost_item='工厂费用', amount=amt))

                elif handle_dup == "合并金额（新旧相加）":
                    for oid, old_amt, new_amt in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='工厂费用').delete()
                        db.add(ActualDirectCost(order_id=oid, cost_item='工厂费用', amount=old_amt + new_amt))
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='工厂费用', amount=amt))

                else:
                    for oid, amt in records:
                        if oid in dup_oids:
                            continue
                        db.add(ActualDirectCost(order_id=oid, cost_item='工厂费用', amount=amt))

                db.commit()
                st.success(f"成功导入 {len(records)} 条工厂费用" if can_see else "成功导入 *** 条")
                add_log(st.session_state.user_id, st.session_state.username, "导入工厂账单", f"共{len(records)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def xinjiang_expense_import_page():
    st.header("🏜️ 新疆拍样/报销费用导入（间接成本）")
    module_name = "🏜️ 新疆费用导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.info(
        "**这一页只导新疆的拍样费/报销费。** 新疆以外的拍样费用请到「📈 录入间接成本」页，"
        "填在「样片研发」项下——两处不要混填，否则会重复计算或漏算。", icon="ℹ️")
    st.markdown("""
    **说明**：  
    - 上传 Excel 文件，需包含列：`期间`（如 2026-07）、`费用项`、`金额`。  
    - 费用项填写：`新疆拍样费用` 或 `新疆报销费用`。  
    - 这些费用将存入间接费用表，在利润表中按新疆选片订单数分摊。  
    - 重复导入同一期间同费用项会自动覆盖。
    """)
    uploaded = st.file_uploader("上传新疆费用Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        required = ['期间', '费用项', '金额']
        if not all(col in df.columns for col in required):
            st.error("缺少必要列：期间、费用项、金额")
            return
        valid_items = ['新疆拍样费用', '新疆报销费用']
        df = df[df['费用项'].isin(valid_items)]
        if df.empty:
            st.warning("没有有效的新疆费用记录（费用项需为「新疆拍样费用」或「新疆报销费用」）")
            return
        if st.button("确认导入新疆费用"):
            db = SessionLocal()
            try:
                for _, row in df.iterrows():
                    p = standardize_period(row['期间'])
                    item = str(row['费用项'])
                    db.query(IndirectCost).filter_by(period=p, cost_item=item, business_type='新疆').delete()
                db.commit()
                for _, row in df.iterrows():
                    p = standardize_period(row['期间'])
                    item = str(row['费用项'])
                    amt = float(row['金额'])
                    db.add(IndirectCost(period=p, cost_item=item, business_type='新疆', amount=amt))
                db.commit()
                msg = f"成功导入 {len(df)} 条新疆费用" if can_see else "成功导入 *** 条新疆费用"
                st.success(msg)
                add_log(st.session_state.user_id, st.session_state.username, "导入新疆费用", f"共{len(df)}条" if can_see else "共***条")
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

# ==================== 员工工资管理 ====================
def parse_raw_salary_excel(uploaded_file):
    """解析原始工资表（多层表头）"""
    import openpyxl
    wb = openpyxl.load_workbook(uploaded_file, data_only=True)
    ws = wb.active

    COL_MAP = {
        '月份': '月份', '部门': '部门', '职务': '职务', '姓名': '姓名',
        '基本工资': '基本工资', '社保补贴': '社保补贴', '岗位薪资': '岗位薪资',
        '岗位补助': '岗位补助', '绩效': '绩效', '提成': '提成', '加班补贴': '加班补贴',
        '应发金额': '应发金额', '应发\n金额': '应发金额',
        '个税扣款': '个税扣款', '保险扣款': '保险扣款', '罚款': '罚款',
        '实发金额': '实发金额', '实发\n金额': '实发金额',
        '社保公司部分': '社保公司部分', '总工资': '总工资',
        '旅拍分摊金额': '旅拍分摊金额', '婚礼分摊金额': '婚礼分摊金额',
        '旅拍提成': '旅拍提成', '婚礼提成': '婚礼提成',
        '实际承担工资': '实际承担工资', '应发+公司社保': '总工资',
    }

    records = []
    current_month = None
    headers = []

    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        first_val = str(row[0]).strip() if row[0] else ''
        # 检测月份行
        if first_val.endswith('月') and any(str(c).strip() in ('部门', '职务', '姓名') for c in row[1:6] if c):
            for c in row:
                s = str(c).strip() if c else ''
                if s.endswith('月') and len(s) <= 5:
                    current_month = standardize_period(s)
                    break
            headers = [str(c).strip() if c else '' for c in row]
            continue

        # 跳过纯表头续行
        if first_val in ('部门', '月份', '合计', '') and not any(row[i] for i in range(2, min(8, len(row)))):
            for i, c in enumerate(row):
                s = str(c).strip() if c else ''
                if s and i < len(headers) and (not headers[i] or headers[i] == 'None'):
                    headers[i] = s
            continue

        if not current_month:
            continue

        row_dict = {'月份': current_month}
        for i, val in enumerate(row):
            if i >= len(headers):
                break
            col_name = headers[i]
            target = None
            for key, mapped in COL_MAP.items():
                if key in col_name:
                    target = mapped
                    break
            if target and target not in row_dict:
                row_dict[target] = val

        if row_dict.get('姓名') and str(row_dict['姓名']).strip() not in ('', 'None', '合计', 'nan', 'NaN'):
            records.append(row_dict)

    wb.close()
    if not records:
        raise ValueError("未能从原始工资表中解析出数据，请确认表头是否包含'姓名'和'基本工资'等列")
    return pd.DataFrame(records)


# ==================== 员工工资：同月同人多条合并规则 ====================
# 月标准列：反映「月度工资标准」，同月同人多条时不能相加，只能择一（取应发金额最大的那行的原值）。
MS_MONTH_STD_COLS = ['基本工资', '社保补贴', '岗位薪资', '岗位补助', '绩效', '提成']

# 发放列：反映「当月实际发放/承担」，同月同人多条时直接相加。
MS_PAYOUT_COLS = ['加班补贴', '应发金额', '个税扣款', '保险扣款', '罚款', '实发金额',
                  '社保公司部分', '总工资', '旅拍分摊金额', '婚礼分摊金额',
                  '旅拍提成', '婚礼提成']

# 数值列全集（月标准列 + 发放列）
MS_NUMERIC_COLS = MS_MONTH_STD_COLS + MS_PAYOUT_COLS


def _ms_norm_text(val):
    """工资合并期文本列的「空值归一化」：任何空值形态统一返回空字符串 ''。

    覆盖形态：None、float NaN / pd.NA（pd.isna 为真）、以及字符串化的
    'nan' / 'None' / 'NaN' / 纯空白。非空值返回 str(val).strip()。

    背景：真实工资文件部分记录「职务」列为空（如 2026-01 运营部），若直接
    str(NaN) 会得到字面量 'nan' 落库并展示为 nan，故统一归一化为 ''。
    """
    if val is None:
        return ''
    # pd.isna 对标量返回 bool；对数组返回数组，此处只处理标量故用 try 兜底
    try:
        if pd.isna(val):
            return ''
    except (TypeError, ValueError):
        pass
    text = str(val).strip()
    if text in ('', 'nan', 'None', 'NaN'):
        return ''
    return text


def merge_employee_salary_rows(df, adjust_mask=None):
    """合并同月同人多条工资记录，返回 (合并后 DataFrame, 完全重复组列表)。

    业务规则（用户拍板）：
    1. 「月标准列」(6 列：基本工资/社保补贴/岗位薪资/岗位补助/绩效/提成) 与
       「发放列」(12 列：加班补贴/应发金额/个税扣款/保险扣款/罚款/实发金额/
       社保公司部分/总工资/旅拍分摊金额/婚礼分摊金额/旅拍提成/婚礼提成) 分开处理。
    2. 发放列：直接相加（保持原有行为）。
    3. 月标准列：取「A 类行中应发金额最大的那一行」的**原始标准值**，不做任何比例倒挤；
       若该列本身为空则该列为 0。
       其中——
       - A 类行（月标准行）：不是调整行的行（试用/转正分段、多方案择高等）。
       - B 类行（调整行）：6 个月标准列**原始值全部为空**且 `应发金额 < 0`。
         这类行只把发放列累加进合计，不参与月标准取值（成本分摊冲减、离职一次性清算）。
    4. 职务：取 A 类行中**最后一行**的值（转正后才是当前职务）。
    5. 备注：保持现有拼接行为（'; '.join 非空值）。

    ⚠️ 关键实现细节（易错点）：
       B 类行判定必须基于**原始单元格是否为空**（NaN）。若调用方在此之前已执行
       `fillna(0)`，则必须通过 `adjust_mask` 参数显式传入预先算好的布尔掩码
       （基于 fillna 之前的 NaN 状态计算），否则本函数在数值层面无法区分
       「原始为空」与「原始为 0」，会把调整行误判为月标准行。

    Args:
        df: 待合并的工资明细 DataFrame，需含「月份」「部门」「姓名」三列分组键。
        adjust_mask: 可选的 pd.Series/array[bool]，与 df 同索引，True 表示该行是
            调整行（B 类）。为 None 时本函数会依据当前值自行推断
            （6 个月标准列均 == 0 且 应发金额 < 0），仅适用于「未做过 fillna、
            且原始空值为 NaN/0」的输入。

    Returns:
        (merged_df, duplicate_groups)：
        - merged_df: 合并后的 DataFrame，一人一月一条。
        - duplicate_groups: 列表，每项为 dict，描述「所有列完全一致」的重复组
          {'月份','部门','姓名','应发金额','出现次数'}。
    """
    df = df.copy()

    # ---- 列准备：保证所有数值列存在 ----
    for col in MS_NUMERIC_COLS:
        if col not in df.columns:
            df[col] = 0

    # ---- 完全重复行检测（在合并之前，按原始全部列逐字段比对）----
    # 注意：使用全部列参与比对，含月份/部门/职务/姓名/全部数值列/备注。
    compare_cols = list(df.columns)
    duplicate_groups = []
    dup_mask = df.duplicated(subset=compare_cols, keep=False)
    if dup_mask.any():
        for _, grp in df[dup_mask].groupby(compare_cols, dropna=False, sort=False):
            duplicate_groups.append({
                '月份': grp['月份'].iloc[0],
                '部门': grp['部门'].iloc[0] if '部门' in grp.columns else '',
                '姓名': grp['姓名'].iloc[0],
                '应发金额': pd.to_numeric(grp['应发金额'], errors='coerce').fillna(0).iloc[0],
                '出现次数': int(len(grp)),
            })

    # ---- 调整行（B 类）掩码：优先使用调用方基于原始 NaN 传入的掩码 ----
    if adjust_mask is not None:
        is_adjust = pd.Series(adjust_mask, index=df.index).fillna(False).astype(bool)
    else:
        # 回退推断（无原始 NaN 信息时）：月标准列全为 0 且应发金额为负
        month_vals = df[MS_MONTH_STD_COLS].apply(pd.to_numeric, errors='coerce').fillna(0)
        gross_vals = pd.to_numeric(df['应发金额'], errors='coerce').fillna(0)
        is_adjust = (month_vals.abs().sum(axis=1) == 0) & (gross_vals < 0)
    df['_is_adjust_row'] = is_adjust.values

    # ---- 数值列统一为数值型（此后 NaN -> 0，仅用于合计运算）----
    for col in MS_NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # ---- 职务列预归一化（用于取靠后行）----
    if '职务' not in df.columns:
        df['职务'] = ''
    # ---- 备注列预归一化（用于拼接非空值）----
    if '备注' not in df.columns:
        df['备注'] = ''
    df['备注'] = df['备注'].apply(lambda v: '' if v is None or str(v).strip() in ('', 'nan', 'None') else str(v))

    group_cols = ['月份', '部门', '姓名']
    merged_records = []
    for _, grp in df.groupby(group_cols, as_index=False, sort=False):
        rec = {c: grp[c].iloc[0] for c in group_cols}

        # A 类行：非调整行；若整组都是调整行，则退化为用全部行取标准值
        a_rows = grp[~grp['_is_adjust_row']]
        if a_rows.empty:
            a_rows = grp

        # 月标准列：取 A 类行中「应发金额最大」的那一行的原值（并列取靠后行）
        max_gross = a_rows['应发金额'].max()
        cand = a_rows[a_rows['应发金额'] == max_gross]
        std_row = cand.iloc[-1]  # 并列时取行号靠后者（更可能是转正后）
        for col in MS_MONTH_STD_COLS:
            rec[col] = float(std_row[col])

        # 发放列：整组相加（含 B 类调整行）
        for col in MS_PAYOUT_COLS:
            rec[col] = float(grp[col].sum())

        # 职务：取 A 类行中最后一行（转正后才是当前职务）；空值统一归一化为 ''
        rec['职务'] = _ms_norm_text(a_rows['职务'].iloc[-1])

        # 部门：分组键之一，本应非空；防御性归一化，避免空值被字符串化成 'nan'
        rec['部门'] = _ms_norm_text(rec['部门'])

        # 备注：拼接非空值（保持原行为）
        rec['备注'] = '; '.join(v for v in grp['备注'].tolist() if v)

        merged_records.append(rec)

    merged_df = pd.DataFrame(merged_records, columns=list(group_cols) + MS_NUMERIC_COLS + ['职务', '备注'])
    return merged_df, duplicate_groups


def import_employee_salary_page():
    st.header("👥 员工工资管理")
    module_name = "👥 员工工资管理"
    can_see = has_permission(module_name, st.session_state.role)

    st.markdown("""
    **用途**：导入员工工资清单，用于利润表的人工成本同比/环比归因分析。
    - **不参与利润表计算**，只用于分析
    - 按 **期间 + 部门 + 姓名** 覆盖：同一人同月重新导入会覆盖旧数据
    """)

    with st.expander("📥 下载标准导入模板", expanded=False):
        template_cols = ['月份','部门','职务','姓名','基本工资','社保补贴','岗位薪资','岗位补助',
                         '绩效','提成','加班补贴','应发金额','个税扣款','保险扣款','罚款',
                         '实发金额','社保公司部分','总工资','旅拍分摊金额','婚礼分摊金额',
                         '旅拍提成','婚礼提成','备注']
        st.download_button(
            "📥 下载模板",
            pd.DataFrame(columns=template_cols).to_csv(index=False).encode('utf-8-sig'),
            "员工工资清单模板.csv", "text/csv"
        )

    with st.expander("📤 上传员工工资清单", expanded=True):
        st.markdown("""
        **支持两种格式**：
        1. **标准模板**（推荐）：第一行为列名，直接对应模板列
        2. **原始工资表**：包含多层表头（如 `月份/部门/姓名/基本工资/提成/...`），系统自动解析
        """)
        parse_mode = st.radio("格式选择", ["自动识别", "标准模板", "原始工资表"], horizontal=True, key='emp_parse_mode')
        uploaded = st.file_uploader("选择Excel文件", type=["xlsx", "xls"], key="emp_salary_upload")

        if uploaded:
            try:
                if parse_mode == "标准模板":
                    df = pd.read_excel(uploaded, header=0)
                elif parse_mode == "原始工资表":
                    df = parse_raw_salary_excel(uploaded)
                else:
                    df_test = pd.read_excel(uploaded, header=0)
                    df_test.columns = [str(c).strip() for c in df_test.columns]
                    if '月份' in df_test.columns and '总工资' in df_test.columns:
                        df = df_test
                    else:
                        df = parse_raw_salary_excel(uploaded)
            except Exception as e:
                st.error(f"解析Excel失败：{e}")
                return

            df.columns = [str(c).strip() for c in df.columns]

            required = ['月份', '姓名']
            missing = [c for c in required if c not in df.columns]
            if missing:
                st.error(f"缺少必要列：{missing}。当前列：{list(df.columns)}")
                return

            df['月份'] = df['月份'].apply(standardize_period)
            if '部门' not in df.columns:
                df['部门'] = ''
            df['部门'] = df['部门'].astype(str).str.strip()
            df['姓名'] = df['姓名'].astype(str).str.strip()

            # ⚠️ 调整行（B 类：成本分摊冲减/离职清算）判定必须基于「原始单元格是否为空」。
            # 因此必须在下面的 fillna(0) 之前，用原始 NaN 状态先算出掩码。
            # 判定：6 个月标准列原始值全部为空 且 应发金额 < 0。
            _month_std_all_null = df[MS_MONTH_STD_COLS].apply(
                lambda s: pd.to_numeric(s, errors='coerce').isna()
            ).all(axis=1)
            _gross_raw = pd.to_numeric(df['应发金额'], errors='coerce')
            _adjust_mask = (_month_std_all_null & (_gross_raw < 0)).fillna(False)

            for col in MS_NUMERIC_COLS:
                if col not in df.columns:
                    df[col] = 0
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            mask = (df['总工资'] == 0) & (df['应发金额'] > 0)
            df.loc[mask, '总工资'] = df.loc[mask, '应发金额'] + df.loc[mask, '社保公司部分']

            df = df[~df['姓名'].isin(['', 'nan', 'None', '合计', '姓名', 'NaN'])]
            df = df.dropna(subset=['姓名'])

            # 同一人同月多条合并：
            # - 月标准列（基本工资/社保补贴/岗位薪资/岗位补助/绩效/提成）取「应发金额最大的那行」原值，不倒挤；
            # - 发放列（应发/实发/总工资/各类扣款与分摊提成等）直接相加。
            # 详见 merge_employee_salary_rows() 的规则说明。
            df, dup_groups = merge_employee_salary_rows(df, adjust_mask=_adjust_mask)

            if can_see:
                st.write("📊 数据预览（前20行）：", df.head(20))
                st.write(f"共 {len(df)} 条记录，覆盖期间：{sorted(df['月份'].unique().tolist())}")
            else:
                st.write("📊 数据预览（前20行）：", mask_dataframe(df.head(20)))

            # 完全重复行（所有列一致）必须由用户确认后才能继续导入
            dup_confirmed = True
            if dup_groups:
                dup_confirmed = False
                st.warning(
                    f"⚠️ 检测到 {len(dup_groups)} 组「完全一致」的重复工资记录"
                    "（所有列一模一样，无法自动区分是重复录入还是真实分月记录）。"
                    "请核对后勾选下方确认项再导入："
                )
                st.dataframe(pd.DataFrame(dup_groups), width='stretch')
                dup_confirmed = st.checkbox(
                    "我确认这些是不同月份的工资，继续导入", key='emp_dup_confirm'
                )

            if st.button("✅ 确认导入", disabled=(not can_see) or (not dup_confirmed)):
                db = SessionLocal()
                try:
                    combos = set(zip(df['月份'], df['部门'], df['姓名']))
                    for p, d, n in combos:
                        db.query(EmployeeSalary).filter_by(period=p, department=d, employee_name=n).delete()
                    db.commit()

                    for _, row in df.iterrows():
                        db.add(EmployeeSalary(
                            period=row['月份'],
                            department=row.get('部门', ''),
                            position=row.get('职务', ''),
                            employee_name=row['姓名'],
                            base_salary=float(row.get('基本工资', 0)),
                            social_subsidy=float(row.get('社保补贴', 0)),
                            position_salary=float(row.get('岗位薪资', 0)),
                            position_allowance=float(row.get('岗位补助', 0)),
                            performance=float(row.get('绩效', 0)),
                            commission=float(row.get('提成', 0)),
                            overtime_allowance=float(row.get('加班补贴', 0)),
                            gross_salary=float(row.get('应发金额', 0)),
                            tax_deduction=float(row.get('个税扣款', 0)),
                            insurance_deduction=float(row.get('保险扣款', 0)),
                            fine=float(row.get('罚款', 0)),
                            net_salary=float(row.get('实发金额', 0)),
                            company_social_security=float(row.get('社保公司部分', 0)),
                            total_salary=float(row.get('总工资', 0)),
                            travel_share=float(row.get('旅拍分摊金额', 0)),
                            wedding_share=float(row.get('婚礼分摊金额', 0)),
                            travel_commission=float(row.get('旅拍提成', 0)),
                            wedding_commission=float(row.get('婚礼提成', 0)),
                            remark=str(row.get('备注', ''))[:500],
                        ))
                    db.commit()
                    st.success(f"✅ 成功导入 {len(df)} 条员工工资记录，覆盖 {len(combos)} 个人员期间组合")
                    add_log(st.session_state.user_id, st.session_state.username, "导入员工工资清单", f"共{len(df)}条")
                    st.rerun()
                except Exception as e:
                    db.rollback()
                    st.error(f"导入失败：{e}")
                finally:
                    db.close()

    st.divider()
    st.subheader("🔍 查询已有工资清单")
    db = SessionLocal()
    try:
        period_rows = db.query(EmployeeSalary.period).distinct().all()
        periods = sorted({p[0] for p in period_rows if p[0]}, reverse=True)
    finally:
        db.close()

    if not periods:
        st.info("暂无已导入的工资清单")
        return

    st.write(f"已导入的期间：{', '.join(periods)}")
    q_period = st.selectbox("选择期间查询", periods, key='emp_query_period')
    db = SessionLocal()
    try:
        records = db.query(EmployeeSalary).filter_by(period=q_period).order_by(
            EmployeeSalary.department, EmployeeSalary.employee_name
        ).all()
        if records:
            df_show = pd.DataFrame([{
                '月份': r.period, '部门': r.department, '职务': r.position, '姓名': r.employee_name,
                '基本工资': r.base_salary, '社保补贴': r.social_subsidy,
                '岗位薪资': r.position_salary, '岗位补助': r.position_allowance,
                '绩效': r.performance, '提成': r.commission, '加班补贴': r.overtime_allowance,
                '应发金额': r.gross_salary, '个税扣款': r.tax_deduction,
                '保险扣款': r.insurance_deduction, '罚款': r.fine, '实发金额': r.net_salary,
                '社保公司部分': r.company_social_security, '总工资': r.total_salary,
                '旅拍分摊金额': r.travel_share, '婚礼分摊金额': r.wedding_share,
                '旅拍提成': r.travel_commission, '婚礼提成': r.wedding_commission,
                '备注': r.remark,
            } for r in records])
            if can_see:
                st.dataframe(df_show, width='stretch')
            else:
                st.dataframe(mask_dataframe(df_show), width='stretch')
            csv = df_show.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 导出该期间", csv, f"员工工资_{q_period}.csv", "text/csv")
    finally:
        db.close()


def analyze_salary_change(curr_period, base_period):
    """分析两个期间的工资变化原因"""
    db = SessionLocal()
    try:
        curr_records = db.query(EmployeeSalary).filter_by(period=curr_period).all()
        base_records = db.query(EmployeeSalary).filter_by(period=base_period).all()

        curr_map = {r.employee_name: r for r in curr_records}
        base_map = {r.employee_name: r for r in base_records}
        curr_names = set(curr_map.keys())
        base_names = set(base_map.keys())

        new_employees = curr_names - base_names
        left_employees = base_names - curr_names
        staying = curr_names & base_names

        curr_total = sum((r.total_salary or 0) for r in curr_records)
        base_total = sum((r.total_salary or 0) for r in base_records)

        new_contribution = sum((curr_map[n].total_salary or 0) for n in new_employees)
        left_contribution = -sum((base_map[n].total_salary or 0) for n in left_employees)

        staying_base_change = 0.0
        staying_comm_change = 0.0
        staying_perf_change = 0.0
        staying_position_change = 0.0
        staying_social_subsidy = 0.0
        staying_overtime = 0.0
        staying_company_social = 0.0
        staying_tax = 0.0
        staying_insurance = 0.0
        staying_fine = 0.0
        staying_absence = 0.0

        emp_changes = []
        for n in staying:
            c = curr_map[n]
            b = base_map[n]
            d_base = (c.base_salary or 0) - (b.base_salary or 0)
            d_comm = (c.commission or 0) - (b.commission or 0)
            d_perf = (c.performance or 0) - (b.performance or 0)
            d_pos = ((c.position_salary or 0) + (c.position_allowance or 0)) - \
                    ((b.position_salary or 0) + (b.position_allowance or 0))
            d_social_subsidy = (c.social_subsidy or 0) - (b.social_subsidy or 0)
            d_overtime = (c.overtime_allowance or 0) - (b.overtime_allowance or 0)
            d_company_social = (c.company_social_security or 0) - (b.company_social_security or 0)
            d_tax = (c.tax_deduction or 0) - (b.tax_deduction or 0)
            d_insurance = (c.insurance_deduction or 0) - (b.insurance_deduction or 0)
            d_fine = (c.fine or 0) - (b.fine or 0)
            # 剩余归入"缺勤/迟到/未打卡等"（数据库未单独存的扣款）
            d_absence = (c.total_salary or 0) - (b.total_salary or 0) \
                        - d_base - d_comm - d_perf - d_pos \
                        - d_social_subsidy - d_overtime - d_company_social \
                        - d_tax - d_insurance - d_fine

            staying_base_change += d_base
            staying_comm_change += d_comm
            staying_perf_change += d_perf
            staying_position_change += d_pos
            staying_social_subsidy += d_social_subsidy
            staying_overtime += d_overtime
            staying_company_social += d_company_social
            staying_tax += d_tax
            staying_insurance += d_insurance
            staying_fine += d_fine
            staying_absence += d_absence

            emp_changes.append({
                '员工': n,
                '部门': c.department or b.department,
                '基本工资变化': d_base,
                '提成变化': d_comm,
                '绩效变化': d_perf,
                '岗位薪资变化': d_pos,
                '社保补贴变化': d_social_subsidy,
                '加班补贴变化': d_overtime,
                '社保公司部分变化': d_company_social,
                '个税扣款变化': d_tax,
                '保险扣款变化': d_insurance,
                '罚款变化': d_fine,
                '缺勤/迟到等变化': d_absence,
                '合计变化': (c.total_salary or 0) - (b.total_salary or 0),
                '本期总工资': c.total_salary or 0,
                '上期总工资': b.total_salary or 0,
                '类型': '存量',
            })

        for n in new_employees:
            c = curr_map[n]
            emp_changes.append({
                '员工': n, '部门': c.department,
                '基本工资变化': c.base_salary or 0,
                '提成变化': c.commission or 0,
                '绩效变化': c.performance or 0,
                '岗位薪资变化': (c.position_salary or 0) + (c.position_allowance or 0),
                '社保补贴变化': c.social_subsidy or 0,
                '加班补贴变化': c.overtime_allowance or 0,
                '社保公司部分变化': c.company_social_security or 0,
                '个税扣款变化': c.tax_deduction or 0,
                '保险扣款变化': c.insurance_deduction or 0,
                '罚款变化': c.fine or 0,
                '缺勤/迟到等变化': 0,
                '合计变化': c.total_salary or 0,
                '本期总工资': c.total_salary or 0,
                '上期总工资': 0,
                '类型': '新增',
            })
        for n in left_employees:
            b = base_map[n]
            emp_changes.append({
                '员工': n, '部门': b.department,
                '基本工资变化': -(b.base_salary or 0),
                '提成变化': -(b.commission or 0),
                '绩效变化': -(b.performance or 0),
                '岗位薪资变化': -((b.position_salary or 0) + (b.position_allowance or 0)),
                '社保补贴变化': -(b.social_subsidy or 0),
                '加班补贴变化': -(b.overtime_allowance or 0),
                '社保公司部分变化': -(b.company_social_security or 0),
                '个税扣款变化': -(b.tax_deduction or 0),
                '保险扣款变化': -(b.insurance_deduction or 0),
                '罚款变化': -(b.fine or 0),
                '缺勤/迟到等变化': 0,
                '合计变化': -(b.total_salary or 0),
                '本期总工资': 0,
                '上期总工资': b.total_salary or 0,
                '类型': '离职',
            })

        dept_data = {}
        for e in emp_changes:
            dept = e['部门'] or '(未分配)'
            if dept not in dept_data:
                dept_data[dept] = {
                    '部门': dept, '新增人数': 0, '离职人数': 0,
                    '基本工资变化': 0.0, '提成变化': 0.0, '绩效变化': 0.0,
                    '岗位薪资变化': 0.0, '社保补贴变化': 0.0, '加班补贴变化': 0.0,
                    '社保公司部分变化': 0.0, '个税扣款变化': 0.0,
                    '保险扣款变化': 0.0, '罚款变化': 0.0,
                    '缺勤/迟到等变化': 0.0, '合计变化': 0.0,
                }
            d = dept_data[dept]
            if e['类型'] == '新增': d['新增人数'] += 1
            elif e['类型'] == '离职': d['离职人数'] += 1
            d['基本工资变化'] += e['基本工资变化']
            d['提成变化'] += e['提成变化']
            d['绩效变化'] += e['绩效变化']
            d['岗位薪资变化'] += e['岗位薪资变化']
            d['社保补贴变化'] += e.get('社保补贴变化', 0) or 0
            d['加班补贴变化'] += e.get('加班补贴变化', 0) or 0
            d['社保公司部分变化'] += e.get('社保公司部分变化', 0) or 0
            d['个税扣款变化'] += e.get('个税扣款变化', 0) or 0
            d['保险扣款变化'] += e.get('保险扣款变化', 0) or 0
            d['罚款变化'] += e.get('罚款变化', 0) or 0
            d['缺勤/迟到等变化'] += e.get('缺勤/迟到等变化', 0) or 0
            d['合计变化'] += e['合计变化']

        return {
            'curr_period': curr_period,
            'base_period': base_period,
            'curr_total': curr_total,
            'base_total': base_total,
            'total_change': curr_total - base_total,
            'curr_count': len(curr_records),
            'base_count': len(base_records),
            'count_change': len(curr_records) - len(base_records),
            'new_employees': sorted(list(new_employees)),
            'left_employees': sorted(list(left_employees)),
            'new_contribution': new_contribution,
            'left_contribution': left_contribution,
            'staying_base_change': staying_base_change,
            'staying_comm_change': staying_comm_change,
            'staying_perf_change': staying_perf_change,
            'staying_position_change': staying_position_change,
            'staying_social_subsidy': staying_social_subsidy,
            'staying_overtime': staying_overtime,
            'staying_company_social': staying_company_social,
            'staying_tax': staying_tax,
            'staying_insurance': staying_insurance,
            'staying_fine': staying_fine,
            'staying_absence': staying_absence,
            'emp_changes': emp_changes,
            'dept_changes': list(dept_data.values()),
        }
    finally:
        db.close()

# ============================================================================
# 【已下线】新疆拍摄费用导入（2026-09 起不再使用）
# ----------------------------------------------------------------------------
# 停用原因：新疆账单后期不再需要系统解析，人工整理好的数据已是可直接使用的格式，
#           统一走「拍摄费用账单」导入即可。
# 核验结论（停用前已逐项比对，两页功能完全等价）：
#   - 写入的费用项一致：cost_item 均为 '拍摄费用'
#   - 重复处理选项一致：覆盖 / 仅导入新记录 / 合并金额
#   - 明细列支持一致：基础金额、餐费、仪式补助、住宿、赠送、仪式、上山补助、景点1~4、减费用
#   - 覆盖率检查一致：均调用 render_shoot_coverage_check()
# 因此本函数保留代码但不挂任何入口（原本也从未挂过菜单）。如需恢复，
# 在 route 分支里加一行 elif 指向它即可。
# ============================================================================
def xinjiang_shooting_import_page():
    st.markdown("### 📸 新疆拍摄费用导入")
    module_name = "📥 账单导入"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    **说明**：  
    - 上传新疆订单的拍摄费用账单，解析逻辑与普通拍摄费用账单**完全一致**，费用统一计入「拍摄费用」。  
    - **格式B（推荐）**：包含列 `订单号`、`结算金额`、`基础金额`、`餐费`、`仪式补助`、`住宿`、`赠送`、`仪式`、`上山补助`、`景点1`~`景点4`、`减费用`  
    - **格式A**：包含列 `订单号`、`结算金额`、`结算详情`（如 `2499+180+云杉坪1200`）  
    - 鲜花费用自动并入「赠送」，减费用直接冲减「摄化费用」。  
    - 重复处理：支持覆盖已有记录、合并金额、仅导入新记录。
    """)
    uploaded = st.file_uploader("上传新疆拍摄费用Excel", type=["xlsx", "xls"])
    if uploaded:
        df = pd.read_excel(uploaded)
        for col in df.select_dtypes(include=['object', 'string']).columns:
            df[col] = df[col].astype(str)
        if can_see:
            st.write("预览：", df.head())
        else:
            st.write("预览：", mask_dataframe(df.head()))
        if '订单号' not in df.columns or '结算金额' not in df.columns:
            st.error("缺少必要列：订单号、结算金额")
            return
        df['结算金额'] = pd.to_numeric(df['结算金额'], errors='coerce')
        df = df.dropna(subset=['订单号', '结算金额'])
        df['订单号'] = df['订单号'].astype(str).str.strip()
        df = df[~df['订单号'].isin(['', 'nan', 'None', 'NaN', 'null'])]
        df = df.dropna(subset=['订单号'])

        detail_cols = ['基础金额', '餐费', '仪式补助', '住宿', '赠送', '仪式', '上山补助', '减费用']
        has_detail_cols = any(c in df.columns for c in detail_cols)

        spot_names = [
            "丽江古城", "哈里谷", "东巴谷", "情人湖", "雪山远景", "云上牧场", "有你牧场",
            "东巴大峡谷", "夕禾马场", "66号公路", "雪山马场", "原野牧场", "山野牧场", "玉湖",
            "光头强牧场", "姊妹湖", "甘海子", "文海", "拉市海", "龙门镖局", "七号营地",
            "S弯公路", "莫奈庄园", "蔓禾婚礼主题", "法缦主题", "法缦外景", "西西里",
            "法缦雪山庄园", "糊涂窝", "艾洛可公路", "艾洛可庄园", "听花谷", "日照金山",
            "地中海", "蓝月谷", "阿美泽婚礼教堂", "云杉坪", "蓝月谷+云杉坪", "麓雪秘境",
            "九子海小木屋", "木府", "一拍即合基地", "漫步时光", "玉湖秘境", "云上山下",
            "璞野牧场", "乌龙坝", "木莲公园", "云顶基地二期", "喜洲麦田", "丽舍庄园",
            "咸甜餐厅", "半山悦", "者摩山", "天空之城", "垒翠园", "海西里里", "花醉艺术空间",
            "罗荃半岛", "天镜湾", "归鲤小镇", "云想山", "白马艺术庄园", "童话山海基地",
            "苍山植物园", "云顶婚礼主题", "山茶园", "半山半岛", "华彬基地", "华尔兹",
            "秘密空间", "苍山高尔夫", "猫咪花园", "森林剧场", "大理蓝月谷", "赛湖",
            "库尔德宁", "恰西", "赛湖道具车", "果子沟骑马", "库尔德宁/恰西骑马", "将军沟",
            "果子沟小树林", "海颂", "果子沟", "罗荃云顶", "归鲤", "阿美泽", "归鲤云顶",
            "艾洛克", "赛里木湖", "法缦", "栖溪谷", "罗荃"
        ]

        def extract_numbers(s):
            if pd.isna(s) or str(s).strip() == '':
                return 0.0
            nums = re.findall(r'\d+\.?\d*', str(s))
            return sum(float(n) for n in nums)

        records = []
        duplicates_info = []
        db = SessionLocal()
        try:
            for _, row in df.iterrows():
                oid = row['订单号']
                if not oid: continue
                total_settlement = float(row['结算金额'])
                original_detail = str(row.get('结算详情', ''))
                detail_map = {}

                if has_detail_cols:
                    # ===== 格式B：明细列解析 =====
                    if '基础金额' in df.columns:
                        val = extract_numbers(row['基础金额'])
                        if val > 0: detail_map['摄化费用'] = val
                    if '餐费' in df.columns:
                        val = extract_numbers(row['餐费'])
                        if val > 0: detail_map['餐费'] = val
                    for col in ['仪式补助', '上山补助']:
                        if col in df.columns:
                            val = extract_numbers(row[col])
                            if val > 0:
                                detail_map['补助费用'] = detail_map.get('补助费用', 0) + val
                    if '住宿' in df.columns:
                        val = extract_numbers(row['住宿'])
                        if val > 0: detail_map['酒店成本'] = val
                    if '赠送' in df.columns:
                        val = extract_numbers(row['赠送'])
                        if val > 0: detail_map['赠送'] = val
                    if '仪式' in df.columns:
                        val = extract_numbers(row['仪式'])
                        if val > 0: detail_map['仪式费用'] = val
                    for i in range(1, 5):
                        col_name = f'景点{i}'
                        if col_name in df.columns:
                            val = extract_numbers(row[col_name])
                            if val > 0:
                                detail_map['景点费'] = detail_map.get('景点费', 0) + val
                    if '减费用' in df.columns:
                        deduct = extract_numbers(row['减费用'])
                        if deduct > 0:
                            detail_map['摄化费用'] = detail_map.get('摄化费用', 0) - deduct
                    if detail_map.get('摄化费用', 0) < 0:
                        detail_map['摄化费用'] = 0
                    parsed_sum = sum(detail_map.values())
                    if total_settlement != parsed_sum:
                        detail_map['摄化费用'] = detail_map.get('摄化费用', 0) + (total_settlement - parsed_sum)
                else:
                    # ===== 格式A：结算详情文本解析 =====
                    if pd.notna(original_detail) and original_detail.strip():
                        processed = str(original_detail).replace('-', '+-')
                        if processed.startswith('+'): processed = processed[1:]
                        parts = [p.strip() for p in processed.split('+') if p.strip()]
                        for part in parts:
                            is_negative = part.startswith('-')
                            if is_negative: part = part[1:]
                            mul_match = re.search(r'(\d+)\*(\d+)', part)
                            if mul_match:
                                mul_val = float(mul_match.group(1)) * float(mul_match.group(2))
                                desc = part[:mul_match.start()].strip() + part[mul_match.end():].strip()
                                amount = mul_val * (-1 if is_negative else 1)
                            else:
                                nums = re.findall(r'\d+', part)
                                if not nums: continue
                                amount = float(nums[-1]) * (-1 if is_negative else 1)
                                desc = re.sub(r'\d+', '', part).strip().lstrip('-').strip()
                            if desc == '' and abs(amount) == 180:
                                key = '酒店成本'
                            elif '鲜花' in desc:
                                key = '鲜花费用'
                            elif any(spot in desc for spot in spot_names):
                                key = '景点费'
                            elif '酒店' in desc or '住宿' in desc or '房差' in desc:
                                key = '酒店成本'
                            elif '上山补助' in desc or '补助' in desc:
                                key = '补助费用'
                            elif '仪式' in desc:
                                key = '仪式费用'
                            elif desc == '':
                                if abs(amount) > 720: key = '摄化费用'
                                else: key = '其他'
                            else:
                                key = '其他'
                            detail_map[key] = detail_map.get(key, 0) + amount
                    if '鲜花费用' in detail_map:
                        flower_val = detail_map.pop('鲜花费用')
                        detail_map['赠送'] = detail_map.get('赠送', 0) + flower_val
                    parsed_sum = sum(detail_map.values())
                    if total_settlement != parsed_sum:
                        detail_map['摄化费用'] = detail_map.get('摄化费用', 0) + (total_settlement - parsed_sum)

                # 检查重复
                existing = db.query(ActualDirectCost).filter(
                    ActualDirectCost.order_id == oid,
                    ActualDirectCost.cost_item == '拍摄费用'
                ).first()
                if existing:
                    duplicates_info.append((oid, existing.amount, total_settlement))
                detail_str = f"原始:{original_detail}||解析:" + '; '.join([f"{k}:{v}" for k, v in detail_map.items()])
                records.append((oid, total_settlement, detail_str))
        finally:
            db.close()

        if duplicates_info:
            if can_see:
                st.warning(f"检测到 {len(duplicates_info)} 个订单已有拍摄费用，请选择：")
                dup_df = pd.DataFrame(duplicates_info, columns=['订单号', '已有金额', '本次金额'])
                st.dataframe(dup_df)
            else:
                st.warning("检测到重复订单，请选择重复处理方式（数据已隐藏）")
            handle_dup = st.radio("重复处理", [
                "覆盖已有记录",
                "仅导入新记录（跳过已有）",
                "合并金额（新旧相加）"
            ])
        else:
            handle_dup = "仅导入新记录"

        if st.button("确认导入新疆拍摄费用"):
            db = SessionLocal()
            try:
                if handle_dup == "覆盖已有记录":
                    for oid, _, _ in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='拍摄费用').delete()
                elif handle_dup == "合并金额（新旧相加）":
                    for oid, old_amt, new_amt in duplicates_info:
                        db.query(ActualDirectCost).filter_by(order_id=oid, cost_item='拍摄费用').delete()
                        merged_amount = old_amt + new_amt
                        remark = next((r[2] for r in records if r[0] == oid), "")
                        db.add(ActualDirectCost(order_id=oid, cost_item='拍摄费用', amount=merged_amount, remark=remark))
                for oid, amount, remark in records:
                    if handle_dup == "仅导入新记录（跳过已有）" and any(oid == d[0] for d in duplicates_info):
                        continue
                    if handle_dup in ["覆盖已有记录", "合并金额（新旧相加）"] and any(oid == d[0] for d in duplicates_info):
                        continue
                    db.add(ActualDirectCost(order_id=oid, cost_item='拍摄费用', amount=amount, remark=remark))
                db.commit()
                msg = f"成功导入 {len(records)} 条新疆拍摄费用（已含解析明细）" if can_see else "成功导入 *** 条"
                st.success(msg)
                add_log(st.session_state.user_id, st.session_state.username, "导入新疆拍摄费用", f"共{len(records)}条")
                # ===== 覆盖率检查 =====
                imported_oids = [r[0] for r in records]
                render_shoot_coverage_check(imported_oids)
            except Exception as e:
                db.rollback()
                st.error(f"导入失败：{e}")
            finally:
                db.close()

def bill_query_page():
    st.header("📋 账单查询")
    module_name = "📋 账单查询"
    can_see = has_permission(module_name, st.session_state.role)
    bill_type = st.selectbox("选择账单类型", [
        "拍摄费用账单",
        "微电影拍摄账单",
        "微电影剪辑账单",
        "二销选片账单",
        "自租场地消耗"   # 新增选项
    ])
    cost_item_map = {
        "拍摄费用账单": "拍摄费用",
        "微电影拍摄账单": "微电影拍摄费用",
        "微电影剪辑账单": "微电影剪辑费用",
        "二销选片账单": "二销选片费",
        "自租场地消耗": "场地"   # 映射到“场地”费用项
    }
    selected_item = cost_item_map[bill_type]
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("选片开始日期", value=date.today().replace(day=1) - timedelta(days=1))
    with col2:
        end_date = st.date_input("选片结束日期", value=date.today())
    if st.button("查询账单", type="primary"):
        db = SessionLocal()
        try:
            orders = db.query(Order.order_id).filter(
                Order.selection_date >= start_date,
                Order.selection_date <= end_date
            ).all()
            order_ids = [o[0] for o in orders]
            records = db.query(ActualDirectCost).filter(
                ActualDirectCost.cost_item == selected_item,
                ActualDirectCost.order_id.in_(order_ids)
            ).all()
            data = []
            for rec in records:
                order = db.query(Order).filter_by(order_id=rec.order_id).first()
                customer = order.customer_name if order else ''
                set_name = order.set_name if order else ''
                sel_date = order.selection_date.strftime('%Y-%m-%d') if order and order.selection_date else ''
                row_dict = {
                    '订单号': rec.order_id,
                    '客户姓名': customer,
                    '套系': set_name,
                    '选片日期': sel_date,
                    '金额': rec.amount,
                }
                # 拍摄费用特殊处理，显示解析明细
                if selected_item == "拍摄费用" and rec.remark:
                    remark = rec.remark or ''
                    original_detail = ''
                    parsed_detail = ''
                    if '||解析:' in remark:
                        parts = remark.split('||解析:')
                        original_detail = parts[0].replace('原始:', '')
                        parsed_detail = parts[1] if len(parts) > 1 else ''
                    row_dict['原始结算详情'] = original_detail
                    row_dict['解析明细'] = parsed_detail
                    if parsed_detail:
                        for item in parsed_detail.split('; '):
                            if ':' in item:
                                k, v = item.split(':', 1)
                                try: row_dict[k] = float(v)
                                except: pass
                data.append(row_dict)
            df = pd.DataFrame(data)
            if df.empty:
                st.info("当前条件下无账单数据")
                return
            # 列顺序调整
            if selected_item == "拍摄费用":
                base_cols = ['订单号', '客户姓名', '套系', '选片日期', '金额', '原始结算详情', '解析明细']
                extra_cols = [c for c in df.columns if c not in base_cols]
                ordered_cols = base_cols + sorted(extra_cols)
                ordered_cols = [c for c in ordered_cols if c in df.columns]
                df = df[ordered_cols]
            else:
                base_cols = ['订单号', '客户姓名', '套系', '选片日期', '金额']
                df = df[base_cols]
            if can_see:
                st.success(f"共找到 {len(df)} 条记录")
                st.dataframe(df, width='stretch')
            else:
                st.success("共找到 *** 条记录")
                st.dataframe(mask_dataframe(df), width='stretch')
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 导出查询结果", csv, f"账单查询_{start_date}_{end_date}.csv", "text/csv")
        finally:
            db.close()

def data_query_page():
    st.header("📋 数据查询")
    module_name = "📋 数据查询"
    can_see = has_permission(module_name, st.session_state.role)
    data_type = st.selectbox("选择要查询的数据类型", ["收入订单", "实际直接成本", "间接费用", "月度基础数据（毛客资/订单数）"])
    db = SessionLocal()
    try:
        if data_type == "收入订单":
            orders = db.query(Order).order_by(Order.selection_date.desc()).limit(500).all()
            if orders:
                df = pd.DataFrame([{'订单号': o.order_id, '客户姓名': o.customer_name, '类型': o.type, '套系': o.set_name, '选片时间': o.selection_date} for o in orders])
                if can_see:
                    st.dataframe(df, width='stretch')
                else:
                    st.dataframe(mask_dataframe(df), width='stretch')
            else:
                st.info("暂无收入订单数据")
        elif data_type == "实际直接成本":
            costs = db.query(ActualDirectCost).order_by(ActualDirectCost.order_id).limit(5000).all()
            if costs:
                df = pd.DataFrame([{'订单号': c.order_id, '费用项': c.cost_item, '金额': c.amount} for c in costs])
                if can_see:
                    st.dataframe(df, width='stretch')
                else:
                    st.dataframe(mask_dataframe(df), width='stretch')
            else:
                st.info("暂无实际直接成本数据")
        elif data_type == "间接费用":
            indirects = db.query(IndirectCost).order_by(IndirectCost.period).limit(5000).all()
            if indirects:
                df = pd.DataFrame([{'期间': i.period, '业务类型': i.business_type, '费用项': i.cost_item, '金额': i.amount} for i in indirects])
                if can_see:
                    st.dataframe(df, width='stretch')
                else:
                    st.dataframe(mask_dataframe(df), width='stretch')
            else:
                st.info("暂无间接费用数据")
        elif data_type == "月度基础数据（毛客资/订单数）":
            stats = db.query(MonthlyStats).order_by(MonthlyStats.period).limit(500).all()
            if stats:
                df = pd.DataFrame([{'期间': s.period, '业务类型': s.business_type, '毛客资': s.gross_leads, '订单数': s.order_count} for s in stats])
                if can_see:
                    st.dataframe(df, width='stretch')
                else:
                    st.dataframe(mask_dataframe(df), width='stretch')
            else:
                st.info("暂无月度基础数据")
    finally:
        db.close()

def fix_historical_shooting_cost_page():
    st.header("🔧 修复历史拍摄费用解析")
    module_name = "🔧 修复历史拍摄费用解析"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    将数据库中所有**拍摄费用**记录的 **remark 字段为空或不包含 `||解析:`** 的数据，
    统一更新为：`原始:||解析:摄化费用:金额`。
    这样在账单查询和利润分析时就能正确显示“摄化费用”明细。
    > ⚠️ 操作不可撤销，建议先备份数据库。
    """)
    if st.button("开始修复历史数据", type="primary"):
        db = SessionLocal()
        try:
            records = db.query(ActualDirectCost).filter(
                ActualDirectCost.cost_item == '拍摄费用',
                (ActualDirectCost.remark == None) |
                (ActualDirectCost.remark == '') |
                (~ActualDirectCost.remark.contains('||解析:'))
            ).all()
            count = 0
            for rec in records:
                new_remark = f"原始:||解析:摄化费用:{rec.amount}"
                rec.remark = new_remark
                count += 1
            db.commit()
            msg = f"已成功修复 {count} 条历史记录！现在它们都归入「摄化费用」了。" if can_see else f"已成功修复 *** 条历史记录！现在它们都归入「摄化费用」了。"
            st.success(msg)
            add_log(st.session_state.user_id, st.session_state.username, "修复历史拍摄费用解析", f"共{count}条" if can_see else "共***条")
        except Exception as e:
            db.rollback()
            st.error(f"修复失败：{e}")
        finally:
            db.close()

def clean_duplicates_page():
    st.header("🧹 一键清理重复数据")
    module_name = "🧹 清理重复数据"
    can_see = has_permission(module_name, st.session_state.role)
    st.markdown("""
    点击下方按钮，系统将自动清理以下表中的重复记录：
    - **交付费用**（主持/搭建/场地/鲜花费用）：按 订单号 + 费用项 + 金额 + 业务日期 去重，保留最早记录  
      （不同日期相同金额将保留，同一天相同金额才删重）  
    - **其他实际直接成本**：按 订单号 + 费用项 + 金额 去重，保留最早记录  
    - **月度基础数据**：按 期间 + 业务类型 去重  
    - **间接费用**：按 期间 + 费用项 + 业务类型 去重（**新疆业务类型完全跳过，不清理**）

    > ⚠️ 去重操作不可逆，请确认后执行。
    """)
    if st.button("开始清理全部重复数据", type="primary"):
        db = SessionLocal()
        try:
            # 1. 交付费用去重（含日期）
            delivery_items = ['搭建', '主持', '场地', '鲜花费用']
            delivery_records = db.query(ActualDirectCost).filter(
                ActualDirectCost.cost_item.in_(delivery_items)
            ).order_by(ActualDirectCost.id).all()
            seen = set()
            to_delete_ids = []
            for rec in delivery_records:
                date_str = ''
                if rec.remark and rec.remark.startswith('日期:'):
                    date_str = rec.remark[3:]
                key = (rec.order_id, rec.cost_item, rec.amount, date_str)
                if key in seen:
                    to_delete_ids.append(rec.id)
                else:
                    seen.add(key)
            if to_delete_ids:
                db.query(ActualDirectCost).filter(
                    ActualDirectCost.id.in_(to_delete_ids)
                ).delete(synchronize_session='fetch')
            del1_delivery = len(to_delete_ids)

            # 2. 其他实际成本去重（不含交付费用）
            subq2 = db.query(
                ActualDirectCost.order_id,
                ActualDirectCost.cost_item,
                ActualDirectCost.amount,
                func.min(ActualDirectCost.id).label('keep_id')
            ).filter(
                ~ActualDirectCost.cost_item.in_(delivery_items)
            ).group_by(
                ActualDirectCost.order_id,
                ActualDirectCost.cost_item,
                ActualDirectCost.amount
            ).having(func.count() > 1).subquery()
            del2_other = db.query(ActualDirectCost).filter(
                ActualDirectCost.order_id.in_(db.query(subq2.c.order_id)),
                ActualDirectCost.cost_item.in_(db.query(subq2.c.cost_item)),
                ActualDirectCost.amount.in_(db.query(subq2.c.amount)),
                ~ActualDirectCost.id.in_(db.query(subq2.c.keep_id))
            ).delete(synchronize_session='fetch')

            # 3. 月度基础数据去重
            subq3 = db.query(
                MonthlyStats.period,
                MonthlyStats.business_type,
                func.min(MonthlyStats.id).label('keep_id')
            ).group_by(MonthlyStats.period, MonthlyStats.business_type).having(func.count() > 1).subquery()
            del3 = db.query(MonthlyStats).filter(
                MonthlyStats.period.in_(db.query(subq3.c.period)),
                MonthlyStats.business_type.in_(db.query(subq3.c.business_type)),
                ~MonthlyStats.id.in_(db.query(subq3.c.keep_id))
            ).delete(synchronize_session=False)

            # 4. 间接费用去重（明确排除 business_type='新疆'）
            subq4 = db.query(
                IndirectCost.period,
                IndirectCost.cost_item,
                IndirectCost.business_type,
                func.min(IndirectCost.id).label('keep_id')
            ).filter(
                IndirectCost.business_type != '新疆'
            ).group_by(IndirectCost.period, IndirectCost.cost_item, IndirectCost.business_type).having(func.count() > 1).subquery()
            del4 = db.query(IndirectCost).filter(
                IndirectCost.period.in_(db.query(subq4.c.period)),
                IndirectCost.cost_item.in_(db.query(subq4.c.cost_item)),
                IndirectCost.business_type.in_(db.query(subq4.c.business_type)),
                ~IndirectCost.id.in_(db.query(subq4.c.keep_id))
            ).delete(synchronize_session=False)

            db.commit()
            total_del = del1_delivery + del2_other
            msg = f"清理完成！交付费用：{del1_delivery} 条，其他实际成本：{del2_other} 条，月度基础数据：{del3} 条，间接费用：{del4} 条" if can_see else "清理完成！（数量已隐藏）"
            st.success(msg)
            add_log(st.session_state.user_id, st.session_state.username, "清理重复数据", f"成本{total_del}，基础{del3}，间接{del4}" if can_see else "清理完成")
        except Exception as e:
            db.rollback()
            st.error(f"清理失败：{e}")
        finally:
            db.close()

def rule_explanation_page():
    st.header("📖 利润计算规则说明")
    st.markdown("""
    ## 一、收入确认
    - **总收入** = 套系金额 + 二销金额 - 客诉退款金额。
    - 收入数据通过「📥 导入收入数据」导入，系统自动识别套系类型（旅拍/婚礼/新疆）。

    ## 二、直接成本计算
    ### 1. 固定直接成本
    - **搭建、主持、场地、鲜花费用、拍摄费用、微电影拍摄费用**：有实际导入则取实际值，无则为0。
    - **微电影剪辑费用**：优先取实际导入且金额>0的值，否则取标准成本中“微电影剪辑费用”或“微电影剪辑费”。
    - **像素蛋糕修图费**：计算公式为 `拍照张数 × 0.55 × 0.1（2026年4月前）` 或 `× 0.08（2026年4月起）`，不读取任何实际成本。

    ### 2. 修片费（一销/二销）
    - **一销张数** = 选片张数 - 加修张数（最小为0）
    - **二销张数** = 加修张数
    - **如果订单有实际修片费**（通过修片账单导入），则按张数比例拆分：
      - 一销修片费 = 实际修片费 × (一销张数 / 总张数)
      - 二销修片费 = 实际修片费 - 一销修片费
    - **如果没有实际修片费**，则按以下单价计算：
      - **选片日期 ≥ 2026-04-01**：
        - **二销金额 > 6000**：单价 **15 元/张**
        - 否则按套系类型和套系金额、二销金额细分：
          - 旅拍：套系金额≥10980且二销=0 → 10元；二销<3000 → 9元；其他 → 11元
          - 婚礼：套系金额≥16980且二销=0 → 11元；二销<3000 → 10元；其他 → 12元
      - **选片日期 < 2026-04-01**：旅拍默认9元/张，婚礼默认10元/张（或读取标准成本中的修片单价）
    - **一销修片费** = 一销张数 × 单价
    - **二销修片费** = 二销张数 × 单价

    ### 3. 工厂费用（一销/二销）
    - **总工厂成本**按以下优先级确定：
      1. 实际直接成本表中导入的 `工厂费用`（如存在且>0）
      2. 樱桃云产品成本（最新批次）
      3. 标准成本库中的 `工厂费用`
    - 拆分规则：
      - **工厂费用（一销）** = min(总工厂成本, 标准成本)
      - **工厂费用（二销）** = 总工厂成本 - 工厂费用（一销）
      - 若总工厂成本 ≤ 标准成本，则一销 = 总工厂成本，二销 = 0
      - 若总工厂成本 > 标准成本，则一销 = 标准成本，二销 = 总工厂成本 - 标准成本

    ### 4. 二销选片费（门店二销款结算费）
    - **优先取实际导入的二销选片费**（通过「🛒 二销选片账单导入」）
    - 如果没有实际导入或金额为0，则按默认规则：
      - **二销选片费 = 二销金额 × 0.45**

    ### 5. 动态直接成本
    - 任何在“实际直接成本”表中存在且未包含在上述固定项中的费用，系统会自动汇总并显示在对应套系的利润表中，计入直接成本。

    ## 三、间接成本分摊
    - **人工成本（工资）**：按业务类型（旅拍/婚礼）各自汇总所有部门工资，除以各自的选片订单数得到均价，再乘以每个套系的订单数进行分摊。新疆业务：新疆旅拍套系使用旅拍业务的部门工资均价，新疆婚礼套系使用婚礼业务的部门工资均价。
    - **推广费**：先根据“推广费分摊设置”中的婚礼占比，将总推广费拆分为旅拍推广费和婚礼推广费。旅拍推广费按旅拍选片订单数分摊；婚礼推广费按婚礼选片订单数分摊。新疆业务：新疆旅拍套系使用旅拍推广费均价，新疆婚礼套系使用婚礼推广费均价。
    - **其他间接费用（房租水电、税费及手续费、样片研发、场地铺设费、舆情处理）**：旅拍和婚礼分别汇总各自这些项目的金额。先按业务类型内部分摊（各自按选片订单数），再与新疆套系共同参与全局分摊。新疆业务：新疆旅拍使用旅拍该类费用的均价，新疆婚礼使用婚礼均价。**场地铺设费**：特指无法分入具体订单的场地公共费用，处理方式同上。
    - **新疆拍样费用**：单独汇总所有期间的新疆拍样费用（business_type='新疆'），按新疆选片订单总数（含所有新疆套系）进行均摊，并**合并入“样片研发”列**展示。新疆报销费用若导入，将作为单独列显示在利润表中，并按同样规则分摊。
    - **分摊特殊情况**：如果某业务类型（如旅拍）在所选期间内订单数为0，则其对应费用不分摊，避免除零错误。

    ## 四、推广费分摊设置
    - 在「⚙️ 推广费分摊设置」中可调整婚礼推广费相对于旅拍的权重。例如权重设为2.0，则一个婚礼订单的推广成本相当于2个旅拍订单。推广费总额可在间接成本页面手动录入，也可通过月度基础数据页面批量导入。

    ## 五、每月数据导入顺序建议
    为准确核算利润，请按以下顺序导入数据（每月或每批次）：
    1. **收入数据**（📥 导入收入数据）—— 必须最先导入，确保所有订单号、金额、日期正确。
    2. **标准成本**（📋 维护标准成本）—— 若有新增套系或费用调整，需在此更新。
    3. **实际直接成本** —— 按各供应商账单格式分别导入：拍摄费用、微电影拍摄费、微电影剪辑费、二销选片费、其他直接成本（如搭建、主持、场地等）。
    4. **樱桃云产品成本**（🏭 导入樱桃云产品成本）—— 通常按月导入最新批次。
    5. **间接成本**：工资、推广费、其他间接费用（房租水电、税费、样片研发、场地铺设费、舆情处理等）。
    6. **月度基础数据**（📊 录入月度基础数据）—— 导入毛客资、订单数（影响推广费拆分和均价计算）。
    7. **新疆专项费用**（🏜️ 新疆费用导入）—— 导入新疆拍样费、报销费（按期间录入，不关联订单）。

    > ⚠️ 注意：以上顺序可确保后续步骤能正确读取到前置数据。若发现利润表数据异常，请检查是否有遗漏导入项。

    ## 六、数据查询与修复
    - 所有已导入数据可在「📋 数据查询」中按类型查看。
    - 拍摄费用解析可通过「🔧 修复历史拍摄费用解析」一键将旧数据（无解析详情）归入“摄化费用”。
    - 重复数据可通过「🧹 清理重复数据」清理，保留最早记录。
    - 账单查询（📋 账单查询）支持按费用类型和日期范围筛选，并导出 CSV。

    ## 七、权限说明
    - 系统支持按模块设置可见权限，管理员可在「🔐 权限管理」中为各模块指定允许查看真实数据的角色。
    - 非授权角色进入页面后，所有数值将显示为 `***`，但仍可访问页面和进行导出操作（导出文件为原始数据，请谨慎使用）。
    """)

def user_management_page():
    st.header("👥 用户管理")
    module_name = "👥 用户管理"
    can_see = has_permission(module_name, st.session_state.role)
    db = SessionLocal()
    with st.expander("➕ 添加用户"):
        new_user = st.text_input("用户名")
        new_pwd = st.text_input("密码", type="password")
        role = st.selectbox("角色", ["editor", "admin"])
        if st.button("创建"):
            if new_user and new_pwd:
                db.add(User(username=new_user, password_hash=generate_password_hash(new_pwd), role=role))
                db.commit()
                st.success("创建成功")
                add_log(st.session_state.user_id, st.session_state.username, "创建用户", new_user)
            else: st.warning("请填写完整")
    users = db.query(User).all()
    if users:
        df_users = pd.DataFrame([{'ID':u.id,'用户名':u.username,'角色':u.role,'创建时间':u.created_at} for u in users])
        if can_see:
            st.dataframe(df_users, width='stretch')
        else:
            st.dataframe(mask_dataframe(df_users), width='stretch')
        user_to_del = st.selectbox("删除用户", [""] + [u.username for u in users if u.username != 'admin'])
        if user_to_del and st.button("确认删除"):
            db.query(User).filter_by(username=user_to_del).delete()
            db.commit()
            st.success("已删除")
            add_log(st.session_state.user_id, st.session_state.username, "删除用户", user_to_del)
    db.close()

def log_view_page():
    st.header("📜 操作日志")
    module_name = "📜 操作日志"
    can_see = has_permission(module_name, st.session_state.role)
    db = SessionLocal()
    col1, col2 = st.columns(2)
    with col1: filter_user = st.text_input("筛选用户")
    with col2: filter_action = st.selectbox("筛选操作", [""] + ["登录","导入收入数据","导入实际成本","生成利润表","维护标准成本","用户管理"])
    logs = db.query(OperationLog)
    if filter_user: logs = logs.filter(OperationLog.username.contains(filter_user))
    if filter_action: logs = logs.filter(OperationLog.action == filter_action)
    logs = logs.order_by(OperationLog.timestamp.desc()).limit(200).all()
    if logs:
        df_logs = pd.DataFrame([{'时间':l.timestamp,'用户':l.username,'操作':l.action,'详情':l.details} for l in logs])
        if can_see:
            st.dataframe(df_logs, width='stretch')
        else:
            st.dataframe(mask_dataframe(df_logs), width='stretch')
    else: st.info("无记录")
    db.close()

# 会话超时强制登出（加固）：登录态存在但超过 SESSION_TIMEOUT 则登出
# 已并入 _auth.check_session_timeout()（顶部调用），此处保留原逻辑位置说明。
if st.session_state.get('logged_in') and (time.time() - st.session_state.get('_login_ts', 0)) > SESSION_TIMEOUT:
    st.session_state.logged_in = False
    st.session_state._login_ts = 0
    _auth.clear_login()
    st.rerun()

# 主入口
if not st.session_state.logged_in:
    login_page()
else:
    menu = main_sidebar()
    if menu == "🏠 工作台": workbench_page()
    elif menu == "📥 数据导入中心":
        _ic_css(); import_center_page()
    elif menu == "📊 生成利润表": profit_report_page()
    elif menu == "📁 利润表历史": view_profit_history_page()
    elif menu == "📥 导入收入数据": import_income_page()
    elif menu == "📊 导入运营成本": import_operation_cost_page()
    elif menu == "📋 运营成本查询": operation_cost_query_page()
    elif menu == "📋 维护标准成本": maintain_std_cost_page()
    elif menu == "💰 导入实际直接成本": import_actual_cost_page()
    elif menu == "🏭 导入樱桃云产品成本": import_cherry_cost_page()
    elif menu == "📈 录入间接成本": indirect_cost_page()
    elif menu == "👥 员工工资管理": import_employee_salary_page()
    elif menu == "📊 录入月度基础数据": monthly_stats_page()
    elif menu == "⚙️ 推广费分摊设置": allocation_rule_page()
    elif menu == "🔍 选片订单查询": order_query_page()
    elif menu == "📥 账单导入": bill_import_main_page()
    elif menu == "🏜️ 新疆费用导入": xinjiang_expense_import_page()
    elif menu == "📋 账单查询": bill_query_page()
    elif menu == "📋 数据查询": data_query_page()
    elif menu == "🧹 清理重复数据": clean_duplicates_page()
    elif menu == "🔧 修复历史拍摄费用解析": fix_historical_shooting_cost_page()
    elif menu == "📖 规则说明": rule_explanation_page()
    elif menu == "🔐 权限管理": permission_management_page()
    elif menu == "👥 用户管理": user_management_page()
    elif menu == "📜 操作日志": log_view_page()