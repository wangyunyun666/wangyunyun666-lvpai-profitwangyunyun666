# database.py
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Boolean, ForeignKey, func, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "profit_system.db"))

# ---------- 套系名 → 业务类型 映射表（单源：app.py 导入收入与 profit_engine 内存归一共用） ----------
# 2026-11-24：26 年 10-11 月套餐改名+上新，新套系名既不在旧映射、名字里也无「婚礼/旅拍」
# 关键字，导致导入时类型为空、利润表直接排除这批订单（10 月曾漏算 1818 单/1474 万）。
# 注意：新疆是套系维度不是业务线，新疆套系的 type 仍是「旅拍」或「婚礼」，
# 引擎按套系名含「新疆」单独归入新疆栏，这里不要另设「新疆」类型。
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
    "13980丽江婚礼套系": "婚礼", "14980丽江婚礼套系": "婚礼", "14980丽江婚礼": "婚礼", "19980丽江婚礼": "婚礼", "16980大理婚礼": "婚礼",
    "19980大理婚礼": "婚礼", "19980丽江目的地婚礼": "婚礼", "16980丽江目的地": "婚礼",
    "29980丽江目的地婚礼": "婚礼", "20980新疆婚礼": "婚礼", "14980大理婚礼": "婚礼",
    "16980新疆婚礼": "婚礼", "9999纯拍婚礼丽江": "婚礼",
    # ---- 2026-10/11 改名后新套系名（对应《套餐明细（终）》26 个启用档）----
    "丽江限时特惠套系5980": "旅拍", "大理限时特惠套系5980": "旅拍",
    "丽江绮梦双影像套系6980": "旅拍", "大理绮梦双影像套系6980": "旅拍",
    "丽江一价省心套系9980": "旅拍", "大理一价省心套系8980": "旅拍",
    "丽江高定套系10980": "旅拍", "大理高定套系10980": "旅拍",
    "新疆库尔德宁套系9980": "旅拍", "新疆库尔德宁双影像套系12980": "旅拍",
    "新疆库尔德宁双影像12980": "旅拍", "新疆双影像套系9980": "旅拍",
    "新疆特惠款7980": "旅拍", "新疆特惠7980": "旅拍",
    "新疆仪式套系12980": "旅拍", "新疆网红款9980（纯拍）": "旅拍",
    "25980新疆婚礼": "婚礼",
}

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
else:
    # ===== PostgreSQL（Neon 等云库）连接 =====
    # 兼容性说明（修复 ModuleNotFoundError: No module named 'psycopg'）：
    # SQLAlchemy 2.x 对 "postgresql://" 会**先后探测 psycopg(3) 与 psycopg2**，
    # 若运行环境只装了其中之一、或探测顺序与安装情况不符，就会抛
    # ModuleNotFoundError。此处改为**主动挑选已安装的驱动**并显式指定，
    # 不再依赖 SQLAlchemy 的自动回退，保证在 psycopg / psycopg2 任一存在时都能连上。
    def _pick_pg_driver(url: str) -> str:
        """把裸 postgresql:// 补全为显式驱动的 URL。

        优先 psycopg（v3），回退 psycopg2；两者都缺时保留原样，
        由 SQLAlchemy 抛出原始错误，便于定位。
        """
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        # 已显式指定驱动（如 postgresql+psycopg2://）则原样返回
        if "+" in scheme:
            return url
        if scheme not in ("postgres", "postgresql"):
            return url
        try:
            import psycopg  # noqa: F401  psycopg 3
            return "postgresql+psycopg://" + rest
        except ImportError:
            pass
        try:
            import psycopg2  # noqa: F401  psycopg 2
            return "postgresql+psycopg2://" + rest
        except ImportError:
            return url

    _pg_url = _pick_pg_driver(DATABASE_URL)
    # Neon 为无服务器库，空闲连接会被断开；开启 pool_pre_ping 探活，
    # 连接失效时自动重连，避免 Streamlit Cloud 复用死连接抛 OperationalError
    engine = create_engine(
        _pg_url,
        pool_pre_ping=True,
        pool_recycle=280,
        connect_args={"connect_timeout": 10},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------- 用户表 ----------
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), default='editor')
    created_at = Column(DateTime, default=func.now())

# ---------- 订单表 ----------
class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(50), unique=True, nullable=False)
    set_name = Column(String(200))
    type = Column(String(20))
    set_price = Column(Float, default=0.0)
    second_sales = Column(Float, default=0.0)
    refund = Column(Float, default=0.0)
    selected_photos = Column(Integer, default=0)
    extra_photos = Column(Integer, default=0)
    photo_count = Column(Integer, default=0)
    actual_retouch_fee = Column(Float, default=0.0)
    customer_name = Column(String(100))
    selection_date = Column(Date)

# ---------- 套系标准成本 ----------
class SetStandardCost(Base):
    __tablename__ = 'set_standard_costs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    set_name = Column(String(200), nullable=False)
    cost_item = Column(String(100), nullable=False)
    amount = Column(Float, default=0.0)

# ---------- 实际直接成本 ----------
class ActualDirectCost(Base):
    __tablename__ = 'actual_direct_cost'
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(50), nullable=False)
    cost_item = Column(String(100), nullable=False)
    amount = Column(Float, default=0.0)
    remark = Column(String(500))
    import_time = Column(DateTime, default=func.now())

# ---------- 樱桃云产品成本 ----------
class CherryProductCost(Base):
    __tablename__ = 'cherry_product_cost'
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(50), nullable=False)
    order_id = Column(String(50), nullable=False)
    product_total_cost = Column(Float, default=0.0)
    import_time = Column(DateTime, default=func.now())

# ---------- 间接费用 ----------
class IndirectCost(Base):
    __tablename__ = 'indirect_cost'
    id = Column(Integer, primary_key=True, autoincrement=True)
    period = Column(String(20), nullable=False)
    cost_item = Column(String(100), nullable=False)
    business_type = Column(String(20))
    amount = Column(Float, default=0.0)

# ---------- 分摊规则 ----------
class AllocationRule(Base):
    __tablename__ = 'allocation_rules'
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(String(50), nullable=False)
    param1 = Column(String(200))
    ratio = Column(Float, default=0.0)

# ---------- 操作日志 ----------
class OperationLog(Base):
    __tablename__ = 'operation_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    username = Column(String(50))
    action = Column(String(200))
    details = Column(String(500))
    timestamp = Column(DateTime, default=func.now())

# ---------- 月度基础数据 ----------
class MonthlyStats(Base):
    __tablename__ = 'monthly_stats'
    id = Column(Integer, primary_key=True, autoincrement=True)
    period = Column(String(20), nullable=False)
    business_type = Column(String(20))
    gross_leads = Column(Integer, default=0)
    order_count = Column(Integer, default=0)

# ---------- 模块权限 ----------
class ModulePermission(Base):
    __tablename__ = 'module_permissions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    module_name = Column(String(100), unique=True, nullable=False)
    allowed_roles = Column(String(200), default='admin')

# ---------- 利润表快照 ----------
class ProfitSnapshot(Base):
    __tablename__ = 'profit_snapshots'
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=func.now())
    period_mode = Column(String(10))
    filter_option = Column(String(50))
    period_start = Column(Date)
    period_end = Column(Date)
    period_label = Column(String(100))
    data_json = Column(Text)

# ---------- 员工工资明细表 ----------
class EmployeeSalary(Base):
    """员工工资明细（用于工资同比环比归因分析）"""
    __tablename__ = 'employee_salary'
    id = Column(Integer, primary_key=True)
    period = Column(String(20), index=True)          # 期间，如 2026-01
    department = Column(String(50), index=True)      # 部门
    position = Column(String(50))                    # 职务
    employee_name = Column(String(50), index=True)   # 姓名
    base_salary = Column(Float, default=0)           # 基本工资
    social_subsidy = Column(Float, default=0)        # 社保补贴
    position_salary = Column(Float, default=0)       # 岗位薪资
    position_allowance = Column(Float, default=0)    # 岗位补助
    performance = Column(Float, default=0)           # 绩效
    commission = Column(Float, default=0)            # 提成
    overtime_allowance = Column(Float, default=0)    # 加班补贴
    gross_salary = Column(Float, default=0)          # 应发金额
    tax_deduction = Column(Float, default=0)         # 个税扣款
    insurance_deduction = Column(Float, default=0)   # 保险扣款
    fine = Column(Float, default=0)                  # 罚款
    net_salary = Column(Float, default=0)            # 实发金额
    company_social_security = Column(Float, default=0)  # 社保公司部分
    total_salary = Column(Float, default=0)          # 总工资
    travel_share = Column(Float, default=0)          # 旅拍分摊金额
    wedding_share = Column(Float, default=0)         # 婚礼分摊金额
    travel_commission = Column(Float, default=0)     # 旅拍提成
    wedding_commission = Column(Float, default=0)    # 婚礼提成
    remark = Column(Text)                            # 备注
    import_time = Column(DateTime, default=func.now())

# ===== 创建所有表（必须放在所有模型定义之后）=====
Base.metadata.create_all(bind=engine)
print("数据库表已就绪")


def init_admin():
    """初始化管理员账号（从环境变量读取，避免硬编码可预测默认口令）。"""
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD")
    if not admin_pass:
        print("=" * 64)
        print("⚠️  安全警告：未设置环境变量 ADMIN_PASSWORD！")
        print("     为安全起见，系统未创建任何默认管理员账号。")
        print("     请设置 ADMIN_USERNAME / ADMIN_PASSWORD 后重新启动服务。")
        print("=" * 64)
        return
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(User(username=admin_user,
                        password_hash=generate_password_hash(admin_pass),
                        role='admin'))
            db.commit()
            print(f"✅ 已创建管理员账号：{admin_user}")
    finally:
        db.close()


# 启动时初始化管理员（仅当数据库中无任何用户时创建）
init_admin()