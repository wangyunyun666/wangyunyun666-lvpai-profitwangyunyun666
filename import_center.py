"""数据导入中心（新增模块）

把系统里原有的 17 个导入入口按**建议录入先后顺序**集中到一页，
用「手风琴」逐项展开——展开后就是原有页面的完整功能（**函数零改动，直接调用**）。

设计要点
--------
1. **不复制业务逻辑**：每一项都是直接调用原 app.py 里的页面函数，
   因此原页面升级后，这里自动跟着升级，不存在两份逻辑要同步的问题。
2. **原有菜单全保留**：本页只是"多一条路"，老路径照常可用。
3. **顺序即规范**：按「先定基准 → 再录收入 → 后录成本 → 最后月度口径」排列，
   每项标注所属阶段与前后依赖，降低漏录、错序的概率。
"""
from __future__ import annotations

import sys

import streamlit as st

# ============================================================================
# 导入项定义
# 字段：(序号, 标题, 一句话说明, app.py 中的函数名, 是否展开, 提示文案)
#   fn 可以是 str（页面函数）或 tuple[函数名, 子参数]（账单导入类的子页面）
# ============================================================================
_IC_STAGES = [
    (
        "① 打基础：先定标准与口径",
        "这三项是后续所有成本/利润计算的基准，**每个月第一次录入时先做**，没做会导致成本算不出来。",
        [
            ("📋 套系标准成本库", "维护各套系的标准成本，利润表按它核算",
             "maintain_std_cost_page", False,
             "新增套系或标准成本调整时才需要动，平时每月不必重复导入。"),
            ("📊 月度基础数据", "毛客资、订单数、推广费",
             "monthly_stats_page", False,
             "**每月必做**。推广费分摊、客资成本都依赖这里，缺了当月利润表会不准。"),
            ("⚙️ 推广费分摊设置", "设置婚礼占比等分摊比例",
             "allocation_rule_page", False,
             "分摊比例一般是固定的，只在比例调整时才需要修改。"),
        ],
    ),
    (
        "② 录收入：钱进来了",
        "收入是整个利润表的起点，**建议在录成本之前完成**，否则成本会挂不到订单上。",
        [
            ("📥 导入收入数据", "订单号、套系、选片时间、套系金额、二销金额等",
             "import_income_page", False,
             "**每月必做，且应最先做**。后续所有成本都靠订单号关联，收入没进来成本就是悬空的。"),
        ],
    ),
    (
        "③ 录成本：账单类（实际发生额）",
        "这 8 类都是实际账单。**建议在收入导入完成后进行**，系统才能把费用对应到订单。",
        [
            ("📸 拍摄费用账单", "含摄化、酒店、仪式等明细拆解（**新疆账单也走这里**）",
             "shooting_bill_import_page", False,
             "系统会从备注/明细列里解析出酒店成本、摄化费用、景点费、上山补助等，是利润表里最主要的成本来源之一。"
             "**新疆订单的账单同样用这一页导入**——现在的数据已经是可直接使用的格式，"
             "解析规则已完全涵盖（餐费、住宿、上山补助、景点1~4、减费用等），不需要单独的处理。"),
            ("💒 交付费用", "主持 / 搭建 / 场地 / 鲜花",
             "delivery_cost_import_page", False, None),
            ("🏟️ 自租场地消耗", "自租场地的消耗金额",
             "venue_self_rent_import_page", False, None),
            ("🎬 微电影拍摄账单", "微电影拍摄环节费用",
             "micro_film_shooting_import_page", False, None),
            ("✂️ 微电影剪辑账单", "微电影剪辑环节费用",
             "micro_film_editing_import_page", False, None),
            ("🛒 二销选片账单", "门店二销款结算费",
             "second_sales_import_page", False, None),
            ("🖼️ 修片账单", "修片环节费用",
             "retouch_bill_import_page", False, None),
            ("🏭 工厂账单", "工厂环节费用",
             "factory_bill_import_page", False, None),
        ],
    ),
    (
        "④ 录成本：其他成本项",
        "不属于账单类的成本，按性质归集。**可与 ③ 同步或稍后做**。",
        [
            ("💰 导入实际直接成本", "支持重复检测，可覆盖或合并",
             "import_actual_cost_page", False,
             "重复导入同一批数据时，系统会提示，注意选对「覆盖」还是「合并」。"),
            ("🏭 导入樱桃云产品成本", "樱桃云产品成本数据",
             "import_cherry_cost_page", False, None),
            ("📈 录入间接成本", "按月录入，支持 Excel 批量导入多期",
             "indirect_cost_page", False,
             "支持一次性导入多个月，补历史数据时很方便。"),
            ("🏜️ 新疆拍样/报销费用", "新疆拍样费、新疆报销费（**跨旅拍与婚礼**）",
             "xinjiang_expense_import_page", False,
             "**与旅拍、婚礼都有交叉，所以单独一页，不要混进其他费用里导。**"
             "收 `期间 / 费用项 / 金额` 三列，费用项填「新疆拍样费用」或「新疆报销费用」。"
             "这两项存入间接费用表，**按新疆订单数自动分摊到旅拍与婚礼两条业务线**"
             "（新疆套系名里带「新疆」的订单，旅拍和婚礼各有），你不需要手工拆分。"
             "重复导入同一期间同一费用项会自动覆盖。"),
        ],
    ),
    (
        "⑤ 收尾：运营口径与薪酬",
        "不影响利润表主口径，但影响运营分析。**放在最后做**，前面没录完不影响利润计算。",
        [
            ("📊 导入运营成本", "按年月汇总各账号金额",
             "import_operation_cost_page", False,
             "**不参与利润计算**，仅作独立数据留存，用于运营分析。重复导入同月会覆盖。"),
            ("👥 员工工资管理", "工资清单导入",
             "import_employee_salary_page", False, None),
        ],
    ),
]

# 展示用：阶段序号 → 该阶段内条目数（用于顶部进度概览）
_IC_TOTAL = sum(len(items) for _, _, items in _IC_STAGES)


def _ic_render_item(idx: int, title: str, desc: str, fn, expanded: bool, tip: str | None):
    """渲染单个导入项：标题 + 说明 + 提示，展开后直接调用原页面函数。

    注意：**不能用 `import app` 取函数**。app.py 是脚本式入口（模块级就会执行
    登录判断与路由分发），再次 import 会让整段入口逻辑跑第二遍，从而创建出
    第二个 `key='nav_menu'` 的控件并抛 StreamlitDuplicateElementKey。
    正确做法是从 `__main__`（当前正在运行的脚本本身）里取函数。
    """
    label = f"{idx}. {title}"
    with st.expander(label, expanded=expanded):
        st.caption(desc)
        if tip:
            st.info(tip, icon="💡")
        target = getattr(sys.modules.get("__main__"), fn, None)
        if target is None:
            st.error(f"未找到导入模块 `{fn}`，请联系管理员。")
            return
        try:
            # 直接调用原 app.py 中的页面函数（函数零改动）
            target()
        except Exception as e:  # noqa: BLE001 - 单个模块出错不应拖垮整页
            st.error(f"该模块加载失败：{e}")


def import_center_page():
    """数据导入中心首页。"""
    st.header("📥 数据导入中心")
    st.caption(
        "把系统里所有导入入口按**建议录入顺序**集中在这一页。"
        "点开哪一项就做哪一项，功能和原来完全一致；左侧菜单里的原入口也都在，习惯哪个用哪个。"
    )

    st.markdown(
        "<div class='ic-guide'>"
        "<b>推荐顺序</b>：① 打基础 → ② 录收入 → ③④ 录成本 → ⑤ 收尾。"
        "<br><span>原因很简单：成本要靠订单号才能挂到订单上，所以"
        "<b>收入必须先于成本导入</b>；而标准成本是算成本的依据，要最先定好。"
        "</span></div>",
        unsafe_allow_html=True,
    )

    st.markdown(f"共 **{_IC_TOTAL}** 个导入入口，分 **{len(_IC_STAGES)}** 个阶段：")

    n = 0
    for stage_title, stage_desc, items in _IC_STAGES:
        st.markdown(f"#### {stage_title}")
        st.caption(stage_desc)
        for title, desc, fn, expanded, tip in items:
            n += 1
            _ic_render_item(n, title, desc, fn, expanded, tip)
        st.divider()

    st.caption("💡 每一项都是直接调用原有页面，原有功能一点没改。左侧菜单的入口同样照常可用。")


# ============================================================================
# 样式（跟随系统主色，与工作台保持一致）
# ============================================================================
_IC_CSS = """
<style>
.ic-guide {
    background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%);
    border-left: 4px solid #667eea;
    border-radius: 8px;
    padding: 14px 18px;
    margin: 8px 0 18px 0;
    line-height: 1.8;
    font-size: 0.95rem;
}
.ic-guide b { color: #5a67d8; }
.ic-guide span { color: #4a5568; }
</style>
"""


def inject_ic_css():
    """注入导入中心样式（在页面渲染前调用）。"""
    st.markdown(_IC_CSS, unsafe_allow_html=True)
