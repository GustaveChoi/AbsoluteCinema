# Campus Cinema · 校园影院管理系统

11 月 19 日初步思路汇报 Demo。Python 标准库 + SQLite + 原生 HTML/CSS/JavaScript，无第三方依赖，无需联网。所有电影、用户和性能数据集均为合成数据。

## GitHub 同步

首次在另一台电脑上开发时，在 VS Code 运行 **Git: Clone**，输入 `https://github.com/GustaveChoi/AbsoluteCinema.git` 并打开克隆后的文件夹。之后在 **Source Control** 暂存并提交改动，再点 **Push** 上传。如果 GitHub 上已有新的提交，先 **Pull** 同步，再推送本地提交。

## 运行

安装 Python 3.10 或更新版本。在 macOS 终端进入本目录后运行 `python3 app.py`。Windows 可以双击 `start.bat`，也可以在本目录运行 `python app.py`。

| 入口 | 地址 |
|---|---|
| 学生购票 | http://127.0.0.1:8766/ |
| 影院管理 | http://127.0.0.1:8766/admin |

两个入口共用同一个本地服务和数据库。请在浏览器打开地址。停止服务时按 Ctrl+C。如需换端口，在启动命令后加 `--port 8767`。

### 手机和电脑通过局域网购票

**双击启动局域网分享**：Mac 双击 `start-lan.command`，Windows 双击 `start-lan.bat`。脚本自动启用局域网模式。也可以在终端运行 `python3 app.py --lan`（Windows 使用 `python`）。已启动旧服务时先按 Ctrl+C。其他设备连接同一个 Wi-Fi 或热点，用浏览器打开终端的 **LAN name** 地址（包含端口）。Mac 使用系统的 Bonjour 本地主机名，例如 `http://campus-cinema.local:8766/`；实际名称以终端为准。Windows 使用电脑主机名，能否解析取决于网络和客户端。

如果想在 Mac 上使用上例的名字，可在「系统设置 → 通用 → 共享」中编辑本地主机名为 `campus-cinema`，再重启 Demo。名称需在网络内唯一。也可由路由器管理员为电脑设置固定 DHCP 地址和本地 DNS 名称。

名称打不开时，改用终端打印的 **LAN IP**，多网卡时选 Wi-Fi 的 IPv4 地址。若 IP 也打不开，检查防火墙是否允许 Python 在当前局域网接收入站连接，以及 Wi-Fi 是否隔离客户端。手机上的 127.0.0.1 指向手机自身。

演示时保持主机开机、服务运行。局域网模式通过 HTTP 开放所有 IPv4 网卡，只在可信局域网使用。学生按演示用户选择器操作，同一场次的订单与座位由主机数据库统一处理。管理页面继续需要登录，推荐在主机本地打开。

### 学生账号

打开学生购票首页，输入用户名和密码，点击「注册并登录」。下次用同一账号登录即可查看自己的订单。浏览器使用独立的学生登录会话，退出登录或服务重启后需重新登录。

数据库只保存独立随机盐和 PBKDF2-SHA256 密码哈希。旧版模拟用户及其订单保留，旧身份不能用来登录。局域网演示使用 HTTP，请使用专门的演示密码。

### 管理员账号

默认账号为 `admin`，固定演示密码为 `123456`，保存在本地配置文件 `demo-password.txt`。重启服务后即可访问 `/admin` 登录。设置了 `CINEMA_ADMIN_PASSWORD` 时优先使用环境变量。

也可以在同一个终端设置 `CINEMA_ADMIN_USER` 和 `CINEMA_ADMIN_PASSWORD` 后启动。只设置密码时继续使用默认账号。把示例密码替换为自己的演示密码。

macOS

```sh
export CINEMA_ADMIN_USER='admin'
export CINEMA_ADMIN_PASSWORD='your-demo-password'
python3 app.py
```

Windows PowerShell

```powershell
$env:CINEMA_ADMIN_USER = 'admin'
$env:CINEMA_ADMIN_PASSWORD = 'your-demo-password'
python app.py
```

管理员会话最长 8 小时。点击退出或重启服务会使相应会话失效。默认启动时服务只监听 `127.0.0.1`。

首次运行会自动创建 `cinema.db`，之后重启保留数据。默认包含 50 条历史演示用户记录、3 部虚构电影、1 个影厅、60 个座位、3 个场次。数据库金额用整数“分”存储。

## 功能

- 同一场次可同时选多个座位。确认区显示座位和总金额，任一座位发生冲突时整批下单回滚。
- 待支付购票单可整单模拟支付或取消。座位保留 15 分钟，到期自动释放并保留订单记录。
- 已支付的票可逐张退票。系统记录模拟退款流水并释放该票的座位，同一购票单的其他票继续有效。
- 电影新增/查询/修改/删除；已被场次引用的电影禁止删除。
- 影厅重命名；新增和修改场次；已有订单的场次不可修改。
- 全部订单查询与待支付订单状态维护。
- 核心业务 ER 图、退款流水字段及 SQL 示例。
- 性能实验：1 千 / 1 万 / 10 万订单，辅助索引有无，1 / 2 / 3 表查询，共 18 组。
- INSERT / UPDATE / DELETE 对照，以及 1 / 20 / 50 人抢同一座位。

## 实验复现

登录管理端后进入“性能实验室”，点击“重新运行真实实验”；或在目录内运行：

```powershell
python experiments.py
python test_demo.py
```

实验使用独立临时数据库，不改动影院业务数据。`results.json` 保存环境、时间、SQL、执行计划和原始采样；`性能对比.csv` 保存查询对照表，可直接用 Excel 打开。每次实验覆盖这两个结果文件。报告中的数值为首次实测快照，重跑后以 JSON/网页最新结果为准。

查询每组先预热 3 次，再测量 21 次，输出中位数和最近秩 P95。计时包含 SQL 执行及 fetchall，不包含造数、建索引和网络。查询选择性约 1%，返回行数随总数据量增加。主键索引一直存在，“无索引”仅指 orders.user_id 没有额外辅助索引。实验关闭自动索引；辅助索引组始终后运行，存在运行顺序/缓存偏差，正式报告应交替顺序、多轮复测。

写操作每次回滚保持数据相同，只测 SQL 执行，不含提交持久化。UPDATE 修改非索引列 amount，DELETE/INSERT 涉及索引维护；不能把所有差异都归因于索引维护成本。

并发测试是同一 Python 进程的独立数据库连接，屏障同步起跑；不是 HTTP 负载测试。各组运行一次，总耗时包含线程池调度；单请求 P95 包含屏障等待。错误组特意强制所有人先读后写以复现竞态，不代表实际超售概率。SQLite 采用 WAL 业务数据库，性能查询使用独立 DELETE 日志模式数据库。

## 数据关系

```mermaid
erDiagram
    USERS ||--o{ ORDERS : places
    MOVIES ||--o{ SCREENINGS : shown_as
    HALLS ||--o{ SCREENINGS : hosts
    HALLS ||--o{ SEATS : contains
    SCREENINGS ||--o{ ORDERS : booked_for
    SEATS ||--o{ ORDERS : reserved_by
    ORDERS ||--o| REFUNDS : refunded_by
```

每条订单记录对应一张票，多座购票通过 `booking_id` 关联。订单使用 `(screening_id,hall_id)`、`(seat_id,hall_id)` 复合外键约束影厅。`pending/paid` 的 `(screening_id,seat_id)` 部分唯一索引防止重复占座。取消或退款后保留订单历史并释放座位。`refunds` 按票保存原订单编号、退款金额（分）和时间，每张票最多一条退款流水。启动时会自动升级已有数据库。

`POST /api/book` 接收 `{"screening":1,"seats":[1,2]}`，返回购票单首张票的 `id`、全部票号 `ids` 和总金额 `amount`（分）。例如，向 `POST /api/order` 提交 `{"id":42,"status":"refunded"}` 可为该用户退还已支付的 42 号票。

## 当前边界

本版本采用 SQLite，不是 MySQL 或 MongoDB。SQLite 仅支持单个同时进行的写事务，因此正确性实验不能当作 MySQL 行锁性能结论。SQLite 的事务和部分唯一索引机制参见[官方事务文档](https://www.sqlite.org/lang_transaction.html)与[部分索引文档](https://www.sqlite.org/partialindex.html)。

学生登录后可查看和操作自己的订单，管理员通过独立入口登录。支付和退款均为本地模拟。影厅增删和排片时间区间重叠检测仍是后续工作，目前同一影厅的开始时间必须唯一。演示场次在日期过去后仍可购票。

不部署公网。下一阶段可在保留页面的基础上迁移 MySQL、补充库存表与条件更新事务和更严格压测。MongoDB 可作为扩展对照；Hadoop 不属于本项目必要范围。

## 文件

| 文件 | 内容 |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | 版本与功能改动记录 |
| app.py | 本地 HTTP API、业务事务和初始化 |
| schema.sql | 可执行建表 SQL、外键及索引 |
| index.html | 学生购票与个人订单 |
| student_login.html | 学生注册和登录 |
| admin.html | 影院管理、全量订单、性能实验与 ER 图 |
| admin_login.html | 管理员登录页 |
| cinema.css | 共用的本地样式 |
| experiments.py | 数据生成、查询与写操作计时、并发实验 |
| test_demo.py | 独立临时数据库上的集成测试 |
| results.json | 真实结果、原始采样与执行计划 |
| 性能对比.csv | 可导入 Excel 的查询对比表 |
| 11.19汇报提纲.md | 汇报与演示顺序、首轮结果解读 |
