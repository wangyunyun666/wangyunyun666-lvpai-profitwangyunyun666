# 旅拍利润系统

基于 Streamlit 的旅拍/婚礼业务利润核算系统。支持收入、直接成本、间接成本、运营成本的全流程录入，按套系与业务线生成利润表。

## 功能概览

| 模块 | 说明 |
|------|------|
| 🏠 工作台 | 数据总览、待办提醒、快捷入口 |
| 📥 数据导入中心 | 18 个导入入口按 5 个阶段集中，手风琴逐项展开 |
| 📊 生成利润表 | 按期间生成旅拍/婚礼利润表 |
| 📁 利润表历史 | 历史利润快照查询与对比 |
| 📋 运营成本查询 | 运营成本明细查询 |
| 🔍 选片订单查询 | 选片订单数据查询 |
| 📋 账单查询 / 数据查询 | 原始数据检索 |
| 🧹 清理重复数据 | 重复记录检测与清理 |
| 🔧 修复历史拍摄费用解析 | 历史数据修复工具 |
| 📖 规则说明 | 成本口径与分摊规则文档 |
| 🔐 权限管理 / 👥 用户管理 / 📜 操作日志 | 系统管理 |

## 数据导入中心（5 个阶段）

导入入口按业务先后顺序编排，照着从上往下做即可：

1. **打基础** —— 套系标准成本库、月度基础数据、推广费分摊设置
2. **录收入** —— 导入收入数据
3. **录成本（账单类，8 项）** —— 拍摄费用、交付费用、自租场地消耗、微电影拍摄、微电影剪辑、二销选片、修片、工厂
4. **录成本（其他，4 项）** —— 实际直接成本、樱桃云产品成本、间接成本、新疆拍样/报销费用
5. **收尾** —— 运营成本、员工工资管理

> **新疆相关费用的两处区分（重要）**
> - **新疆拍摄账单** → 走「📸 拍摄费用账单」，与普通拍摄账单同一入口（数据已可直接使用，无需单独处理）
> - **新疆拍样费/报销费** → 走「🏜️ 新疆拍样/报销费用」单独入口。该项费用跨旅拍与婚礼两条业务线，需以 `business_type='新疆'` 写入，系统按新疆订单数自动分摊。
> - 两边不要混填，否则会重复计算或漏算。

## 快速开始

### 环境要求

- Python 3.11+
- 依赖见 `requirements.txt`

### 安装与启动

```bash
pip install -r requirements.txt
streamlit run app.py --server.port 8080 --server.headless true --server.address 0.0.0.0
```

浏览器打开 `http://localhost:8080`。

### 配置（`.streamlit/config.toml`）

仓库已包含必需配置，**部署时不可省略**：

```toml
[server]
headless = true
enableCORS = false
enableXsrfProtection = false

[browser]
gatherUsageStats = false
```

> ⚠️ `enableCORS = false` 是必需的。缺了它，Streamlit 的 `allowedOrigins` 白名单不含部署域名，
> 前端会拒绝建立 WebSocket 连接，表现为**整站空白**（无报错、无失败请求、WebSocket 零帧）。

### 可选环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SESSION_TIMEOUT_MIN` | `480` | 应用内会话超时（分钟） |
| `LOGIN_KEEP_DAYS` | `7` | 持久化登录有效期（天） |
| `AUTH_SECRET` | 自动派生 | 登录令牌签名密钥，**多实例部署必须显式设置且保持一致** |

## 持久化登录（`auth_persist.py`）

### 解决什么问题

用户「过一会不用就被踢回登录页」。**根因不是应用内超时**（默认 480 分钟 = 8 小时），
而是 Streamlit 的会话模型：`st.session_state` 只活在 WebSocket 连接期间。
连接一断（浏览器休眠、切标签、网络抖动、手机锁屏、服务端重启），
服务端 session 被销毁，`logged_in` 等键全部回到默认值 → 重连即显示登录页。

### 方案

把登录凭据持久化到**浏览器 cookie**，服务端每次脚本运行时从 `st.context.cookies` 读回并验签：

1. 登录成功 → 签发带签名的令牌（`itsdangerous.URLSafeTimedSerializer`）→ 写入 cookie
2. 之后打开/刷新 → 读回 → 验签 + 查有效期 → 自动恢复登录态
3. 退出登录 → 清 cookie，立刻失效

### 安全设计

- 令牌用 `URLSafeTimedSerializer` 签名（salt `lt-profit-login`），改动一字符即验签失败
- 有效期默认 7 天，过期自动失效
- **只存「用户名 + 角色 + user_id」，不存密码**
- 退出登录立即清 cookie
- 令牌不写入 URL、不写入日志

### 写入通道的选择（实测结论，勿改）

| 通道 | 结果 |
|------|------|
| `st.context.cookies` 读 cookie | ✅ 可行（Streamlit 1.64+ 原生支持） |
| `components.html` iframe 内 `document.cookie` 写 cookie | ✅ **唯一可行的写入通道** |
| `st.markdown(unsafe_allow_html=True)` 的 `<script>` / `<img onerror>` | ❌ 被清洗，不执行 |
| `st.html("<script>")` | ❌ 被清洗，不执行 |
| iframe `postMessage` 向父窗口传值 | ❌ 跨域隔离，收不到 |
| `st.context.headers` 下发 `Set-Cookie` | ❌ `StreamlitHeaders` 无 `set_cookie` |

**接入方式**（`app.py` 顶部，必须先于任何页面渲染）：

```python
_auth.try_restore_login()      # 从 cookie 恢复登录态
_auth.check_session_timeout()  # 应用内超时判断
_auth.flush_pending_cookie()   # 登录/退出后的 cookie 落盘
```

## 项目结构

```
.
├── app.py                    # 主应用（路由、页面、业务逻辑）
├── auth_persist.py           # 持久化登录（新增）
├── workbench.py              # 工作台页面
├── import_center.py          # 数据导入中心
├── profit_engine.py          # 利润计算引擎（核心算法，勿改）
├── database.py               # ORM 模型定义
├── requirements.txt
├── profit_system.db          # SQLite 数据库
└── .streamlit/
    └── config.toml           # 部署必需配置
```

## 成本口径与分摊规则

详见系统内「📖 规则说明」页面。核心要点：

- **套系标准成本库**：每个套系的基准成本，用于成本归集
- **推广费分摊**：按设置规则分摊到各订单
- **新疆拍样费用**：总额 ÷ 新疆订单总数 = 单均，再按旅拍/婚礼各自新疆订单数分摊到两条业务线
- **间接成本**：按配置的分摊规则分配到业务线

## 注意事项

- `profit_engine.py` 是利润计算核心，修改前务必确认影响范围
- 数据库为 SQLite，多人同时写入场景建议改为 PostgreSQL（`requirements.txt` 已含 `psycopg`）
- 生产部署建议通过 `.streamlit/secrets.toml` 或环境变量显式设置 `AUTH_SECRET`
