# 市场监测 · Markets Monitor

一个用 Python 写的本地行情监测面板：实时抓取 **A 股 / 港股 / 美股 / 期货 / ETF / 利率 / 汇率** 的核心指标，
同时把历史数据落到本地数据库，并用一个深色数据终端风格的网页界面呈现。

![预览](docs/preview.png)

![关联分析](docs/analysis.png)

## 特性

- **74 个核心标的，7 大类**：A 股 9 大指数、港股 4 项、美股 11 项、ETF 15 只（含 3 只国债 ETF）、期货 17 个（含国债期货）、利率 10 项（美债 / 中债各 4 个期限 + 2 个利差）、汇率 8 项。
- **实时多源互备**：新浪财经、腾讯行情、CNBC、东方财富四路并行抓取，按标的配置优先源；某一路失败时自动切到备用源，并保留上一笔价格、标记为过期，不会让界面塌掉。
- **历史数据下载**：**74 个标的全部本地化**，日 / 周 / 月三档齐全（源只给日线时按自然周、自然月本地聚合），共 27 万+ 根 K 线，统一写入 SQLite 并导出 CSV 到 `data/exports/`。
- **自建 Web 面板**：只用 Python 标准库起服务，前端 ECharts 图表 + 原生 JS，无构建步骤、无前端依赖。
- **来源可点击跳转**：页脚列出全部数据源（新浪、腾讯、CNBC、东财、美财政部、世行、纳斯达克、SGE、LBMA、CME、巴西央行、CBOE、欧洲央行），详情面板的「数据源」以及宏观 / 黄金 / 债券视图的副标题也都带官方地址链接，点击新标签页打开。
- **看的舒服的细节**：跑马灯、迷你走势、**每张卡片都带成交量**、**价格标注计价单位（RMB / 美元 / 港元 / %）**、涨跌闪烁、区间收益、CSV 导出、搜索过滤、`#标的代码` 直达链接、红涨绿跌。
- **首次启动自动建档**：本地库为空时，后台自动把全部标的历史拉一遍，不用先手动跑命令。
- **关联性分析**：任选 2~8 个标的，用 6 种相关性指标观察关联度随时间的变化，每个指标一条曲线；两标的额外给收益散点与回归线，多标的自动两两配对取平均并列出明细。
- **当日分时**：主图切到「分时」看逐分钟走势，带昨收基准线、分钟成交量和相对昨收的涨跌幅；A 股 / ETF / 港股 / 美股 / 期货 / 汇率按各自交易时段展示。
- **资金流**：按收盘价在当日高低区间的位置拆分成交额，得到每日流入 / 流出占比、5 日均线和累计净流入曲线；另有市场级面板横向对比 A 股 / 港股 / 美股 / **ETF(A股/美股/港股)** / 期货 / **债券** / **利率** 的资金强弱（时间轴覆盖全部历史，滚轮可缩放）（债券用国债期货 T/TF/TS + 国债 ETF 代表；利率按收益率日变动反向归一化，收益率下行视为净流入）。
- **个股下钻**：点击树图里的任意个股，右侧区域**就地切换**为个股视图（不弹窗、不跳转）——当日分时、日线 K 线（MA20 + 成交量）、资金流，左上「返回板块」回到热力图。
- **板块热力图**：新增「板块」视图，支持 **A股 / 美股** 切换——左侧是可展开的两级行业树，右侧是行业前 50 只个股的树图，**方块面积 = 总市值、颜色 = 涨跌幅**。
  - **A 股**支持两种口径，右上角切换：
    - **新浪行业**（默认）：49 个新浪行业，金融再拆成银行 / 证券 / 保险 / 多元金融，共 52 个；
    - **证监会**：读取 `data/industry_map.csv`（由证监会《上市公司行业分类结果》PDF 生成，5521 只股票），门类 → 次类 → 大类；
  - **点一级行业名称**看该门类的聚合热力图，点箭头只展开收起；
  - **三级细分**开关（记忆在 localStorage，**默认开启**）：对证监会口径生效——关闭时是「门类 → 次类」两级，打开后次类可再展开到大类，如制造业的「电气、电子及通讯」下含「计算机、通信和其他电子设备制造业」「电气机械和器材制造业」；证监会 PDF 只对制造业定义了次类，其余门类本身就是「门类 → 大类」两级；
  - **美股**按纳斯达克 sector 分类（13 个板块、近 7000 只股票）。
- **黄金现货**：伦敦金（LBMA）、纽约金（COMEX）、上海金 Au99.99 / T+D 四路现货报价（含涨跌幅与时间），并按「伦敦金 × USDCNY ÷ 31.1035」换算出人民币克价，给出**内外价差**。
- **央行黄金储备**：新增「黄金」视图——GDP 前 50 经济体的黄金储备（吨）、同比变化、占外储比例与储备市值，用「总储备 − 不含黄金储备」还原黄金美元价值，再按当年金价折算吨数；同比增减即**年度净买入 / 净卖出**记录。
- **债券市场**：新增「债券」视图——中国 / 美国国债收益率曲线（2Y / 5Y / 10Y / 30Y），以及美债月度发行量（按 Bill / Note / Bond 分类，单位十亿美元，数据来自美国财政部拍卖记录）。
- **全球宏观**：新增「宏观」视图——前 20 大经济体的 GDP、GNI、人均 GDP、GDP 同比、失业率、政府债务/GDP、广义货币/GDP、CPI、政策利率，按 GDP 排序、点表头可换列排序。债务数据优先取各国官方源（美国财政部 FiscalData、巴西央行 SGS），带「官」标记即为官方口径；也支持把你从各国财政部导出的 CSV 放进 `data/sovereign_debt/` 覆盖（标记「本」）。

![宏观](docs/macro.png)

![板块](docs/sectors.png)

![美股板块](docs/sectors-us.png)

![债券](docs/bonds.png)

![资金流](docs/flow.png)

![分时](docs/intraday.png)

## 快速开始

**Windows 安装包（推荐）**：运行 `dist\MarketMonitor-Setup.exe`，选一个目录安装，完成后从桌面 / 开始菜单启动。无需安装 Python。

**免安装**：解压 `dist\MarketMonitor-portable.zip`，双击里面的 `MarketMonitor.exe`。

```bash
# 源码方式
pip install -r requirements.txt
python run.py            # 网页版，自动打开 http://127.0.0.1:8765/
python desktop.py        # 桌面版，弹出独立应用窗口
```

## 安装包（Windows）

`dist\MarketMonitor-Setup.exe`（24.9 MB）是一个原生 .NET 安装程序，不依赖 Python，也不会把自己解压到临时目录。

| 步骤 | 说明 |
| --- | --- |
| 1 | 双击 `MarketMonitor-Setup.exe` |
| 2 | 选择安装目录，默认 `%LOCALAPPDATA%\Programs\MarketMonitor`（普通用户权限即可，不需要管理员） |
| 3 | 勾选桌面 / 开始菜单快捷方式、安装后立即启动 |
| 4 | 点「安装」，完成后即可从快捷方式启动 |

- 安装内容就是便携版目录：`MarketMonitor.exe` + `_internal\`，数据随后写在 `<安装目录>\data\`。
- **不要选盘符根目录**（如 `F:\MarketMonitor`）或 `C:\Program Files` 这类受保护目录：部分卷的根目录由系统托管，普通用户没有创建权限。安装器会显示系统返回的具体原因，并提供「改用推荐目录」（`%LOCALAPPDATA%\Programs\MarketMonitor`）的一键入口。
- 想要“所有用户可用”的安装位置，需要提权：安装器会弹窗确认，可直接以管理员身份重启并自动继续安装。
- 若真的装进了只读目录，程序运行时会把数据写到 `%LOCALAPPDATA%\MarketMonitor\data`（程序目录只读时的自动回退），卸载时这份数据需要手动删除。
- 卸载：安装目录里的 `uninstall.bat`，或「设置 → 应用」里的「市场监测」（会一并删除 `data\`）。
- 静默安装（批量部署）：
  ```bat
  MarketMonitor-Setup.exe --silent --dir "D:\Apps\MarketMonitor" --no-launch
  ```
  可选参数：`--no-desktop`、`--no-startmenu`、`--log setup.log`。
- 受限环境（组策略/沙箱）下如果写不了桌面快捷方式或 HKCU 注册表，安装**不会中断**，只跳过对应步骤并在日志里说明。
- 安装界面路径框右侧有「检测」按钮：显示当前用户、是否管理员、目录是否存在、卷是否为只读、写入测试结果与系统返回，便于排查装不进去的目录。

## 便携版（免安装）

`dist\MarketMonitor-portable.zip` 解压后是一个完整目录，双击 `MarketMonitor.exe` 运行：

```
MarketMonitor\
├─ MarketMonitor.exe     ← 双击这个
├─ _internal\            ← 运行时依赖，不要动
└─ data\                 ← 首次运行自动生成
   ├─ market.db          # K 线历史库
   ├─ exports\           # CSV 导出
   ├─ app.log            # 运行日志
   └─ ui-profile\        # 应用窗口的浏览器配置
```

- **完全绿色**：数据、日志、导出、窗口配置全部在 exe 同级的 `data\` 里；`%LOCALAPPDATA%`、`%TEMP%` 都不再写入。
- **不做临时解压**：采用 PyInstaller 的 onedir 模式，exe 直接加载 `_internal\` 里的运行时，不会出现 “Could not create temporary directory”。
- 窗口用 **Edge / Chrome 的应用模式**打开：无地址栏、无标签页、独立任务栏图标；机器上没有 Chromium 浏览器时退回默认浏览器。
- 若整个目录被放在只读位置（如 Program Files），数据会自动改写到 `%LOCALAPPDATA%\MarketMonitor\data`。
- 首次运行本地库为空，会在后台自动下载全部标的历史（约 1 分钟），期间界面可正常使用。
- 命令行参数：`--selftest`（自检后退出）、`--no-window`（只起服务）、`--port 9000`。

**自行打包 / 发布**（三个独立脚本，互不依赖）：

| 脚本 | 作用 | 产物 |
| --- | --- | --- |
| `tools\build.bat` | 打包 exe、便携包、安装包 | `dist\MarketMonitor\`、`dist\MarketMonitor-portable.zip`、`dist\MarketMonitor-Setup.exe` |
| `tools\release.bat` | 把 `dist\` 产物复制到 `release\`，重算 SHA256 并更新 `release/README.md` | `release\MarketMonitor-Setup.exe` |
| `tools\push.bat` | `git add -A` + 提交 + 推送，可带说明：`push.bat "feat: xxx"` | 远端仓库 |

首次打包需要先装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install requests numpy pillow pyinstaller
```

产物：`dist\MarketMonitor\`（目录）、`dist\MarketMonitor-portable.zip`、`dist\MarketMonitor-Setup.exe`。
安装器由 `installer\Installer.cs` 用系统自带的 `csc.exe` 编译（.NET Framework 4.x），payload 作为程序集资源内嵌。

### 自定义黄金储备数据（本地 CSV）

黄金视图同样支持本地覆盖：把各国央行或世界黄金协会的月度数据放进 `data/gold_reserves/`，程序优先使用，界面上标记「本」。

```bash
python run.py gold-template          # 生成 GDP 前 50 经济体的 CSV 模板
```

格式（至少两行才能算出同比买卖）：

```csv
date,tonnes,pct_reserves,source
2025-08-31,2302.1,7.4,世界黄金协会
2025-09-30,2306.3,7.5,世界黄金协会
```

- `tonnes` 为黄金储备吨数，必填；`pct_reserves` 可选；
- 程序按日期排序，**最后一行是当前值，上一行用于计算增持/减持**；
- 以 `#` 开头的行会被忽略；
- 本地文件优先于「世界银行 + 金价换算」的自动结果。

### 自定义主权债务数据（本地 CSV）

各国财政部导出的数据可以手动放进 `data/sovereign_debt/`，程序会**优先使用**，覆盖世界银行与内置接口，界面上标记「本」。

```bash
python run.py debt-template          # 生成 20 个国家的 CSV 模板
```

文件名为 ISO3 代码（`USA.csv`、`JPN.csv`…），格式：

```csv
date,debt_pct,debt_amount,source
2026-06-30,248.7,,日本财务省
```

- `date` 支持 `YYYY-MM-DD` 或 `YYYY`；
- `debt_pct` 与 `debt_amount` **至少填一个**，只填金额时按最新 GDP 自动换算百分比；
- 以 `#` 开头的行会被忽略；
- 程序按日期排序取最后一条有效记录，改完文件刷新页面即可生效（缓存 1 小时）。

### 官方行业分类（证监会 / 中证）

A 股行业支持三种口径，在板块页右上切换：

| 口径 | 数据源 | 说明 |
| --- | --- | --- |
| **新浪行业**（默认） | 新浪 `newSinaHy.php` | 49 个行业，金融再拆成银行/证券/保险/多元金融，共 52 个 |
| **证监会** | 证监会《上市公司行业分类结果》PDF → `data/industry_map.csv` | 5521 只股票，门类 → 次类 → 大类 |

> 行业 ETF 只匹配 A 股上市的行业/主题 ETF，会自动排除港股、恒生、纳指、标普等跨境品种。

导入证监会 PDF（`data/中上市行业分类.pdf`）：

```bash
python run.py pdf-import            # 解析 PDF，生成 data/industry_map.csv
```

- 用 `pdfplumber` 解析 160 页表格，自动处理跨行合并单元格；
- 输出列：`code,name,level1(门类),level2(次类),level3(大类)`；
- 行情（价格 / 涨跌幅 / 总市值）用腾讯接口按 60 只并发拉取（6 线程），约 5 秒覆盖 5000+ 只，缓存 10 分钟；
- 未取到的代码（北交所、停牌、退市）会自动换市场前缀重试。

也可以只用中证分类目录生成待填模板：

```bash
python run.py csi-template          # 读取 data/中证行业分类架构.xlsx，生成 98 行空模板
```

> 中证指数官网没有公开的成分股接口（巨潮需 token），所以中证口径需要自填股票归属；证监会口径的 PDF 则是完整名单，可直接使用。

### 应用内更新

工具栏「检查更新」会对比本地构建 SHA 与 GitHub 上 `main` 的最新提交：

| 模式 | 行为 |
| --- | --- |
| 安装版（exe） | 下载 `release/MarketMonitor-Setup.exe`，退出程序后静默覆盖安装并自动重新打开 |
| 源码版 | 执行 `git pull --ff-only`，重启生效 |

构建时 `tools/build.ps1` 会把当前 git SHA 和构建时间写入 `market_monitor/_buildinfo.py`，所以安装版也能准确报出版本。

## 命令

| 命令 | 说明 |
| --- | --- |
| `python run.py` | 启动面板（等同 `serve`） |
| `python desktop.py` | 启动桌面版（独立窗口） |
| `python desktop.py --selftest` | 桌面版自检 |
| `python run.py serve --port 9000 --no-browser` | 指定端口、不自动开浏览器 |
| `python run.py download` | 刷新全部标的历史（日/周/月）到 `data/`，补全派生指标并导出 CSV |
| `python run.py check` | 自检：各数据源实时覆盖率 |
| `python run.py check --history` | 自检：额外验证每个标的的历史接口 |
| `python run.py list` | 列出全部监测标的 |

## 数据源

| 数据 | 实时 | 历史 |
| --- | --- | --- |
| A 股指数 / ETF | 新浪 `hq.sinajs.cn`（主）、腾讯（备） | 腾讯 K 线（约 3 年）+ 新浪 K 线兜底；北证 50 同源 |
| 港股 | 新浪、腾讯 | 腾讯 K 线 |
| 美股指数 / ETF | 腾讯（主）、新浪 `gb_`（备） | 腾讯 K 线 / 新浪美股日线（个股可达 10 年以上） |
| 美股 VIX | CNBC | CNBC 图表历史 |
| 国内期货 | 新浪 `nf_` | 新浪期货日线（部分品种可回溯至 2005 年） |
| 国债期货 T / TF / TS | 新浪 `nf_T0` 等 | 新浪期货日线（2017 年起） |
| 国际期货 | 新浪 `hf_` | 新浪全球期货日线 |
| 美债收益率 | CNBC | 美国财政部官方日度收益率曲线（约 10 年，CNBC 兜底） |
| 中债收益率 2/5/10/30Y | CNBC（2Y、10Y 盘中）+ 东财（收盘估值） | 东方财富中美国债收益率数据集（2002 年起日线） |
| 国债 ETF（511260 / 511010 / 511090） | 新浪、腾讯 | 腾讯 K 线（约 3 年） |
| 汇率 | 新浪 `fx_` | 欧洲央行（frankfurter.app）；离岸人民币用 CNBC `CNH=` |
| 分时（逐分钟） | A股 / ETF / 港股 / 美股：腾讯 minute；国内期货 / 国际期货 / 汇率：新浪 | 取最近一个交易日 |
| 美元指数 | 新浪 `DINIW` | CNBC 图表历史 |

> 周期：日 / 周 / 月。腾讯源直接提供周月线；其余源只有日线，入库时按自然周、自然月本地聚合出周/月线。
> 所有时间均为北京时间。免费公开接口偶有限流，面板会记录失败次数并在界面上提示。

## 界面说明

- **顶部**：各市场交易状态灯（盘中 / 集合竞价 / 午休 / 盘后 / 休市，含美股夏令时换算）、搜索、刷新频率、下载按钮。
- **大卡**：上证、恒指、纳指、标普、美债 10Y、美元指数，附 40 日迷你走势。
- **主图**：点击任意标的切换；日线蜡烛 + MA5/20/60 + 成交量；`3Y` / `ALL` 自动切周线 / 月线；债券与汇率用面积折线；`分时` 展示当日逐分钟走势（昨收基准线 + 分钟量）。
- **右栏**：开高低收、成交量额、更新时间、数据源、区间收益（1 周 ~ 1 年，债券以 bp 计）。
- **分组**：按 7 大类平铺，点击卡片即选中，`/` 搜索、`R` 手动刷新。

![利率与国债](docs/rates.png)

## 关联分析

顶部切到「关联分析」，左侧勾选 2~8 个标的（可搜索），右侧即时计算。

| 指标 | 含义 |
| --- | --- |
| **综合关联指数** | 下面 6 个指标按权重加权平均得到的统一指标；某点缺失的指标会自动重新分配权重 |
| Pearson 线性相关 | 窗口内日收益率的皮尔逊相关系数，最常用但受异常值影响 |
| Spearman 秩相关 | 用秩次替代数值，抗极端行情 |
| Kendall τ 秩相关 | 一致对 / 不一致对之差，含并列修正，小样本更稳健 |
| 下行相关（同跌） | 只统计窗口内双方同时下跌的交易日，衡量“一起跌”的程度 |
| 波动相关（\|收益\|） | 绝对收益的相关性，衡量波动是否共振 |
| 滞后相关（±1 日） | `corr(A_t, B_{t-1})` 与 `corr(B_t, A_{t-1})` 的平均，判断领先 / 滞后 |

**综合指标权重**：默认 Pearson 30% / Spearman 20% / Kendall 15% / 下行 15% / 波动 10% / 滞后 10%。
点开图下方「权重设置」拖动滑杆即可调整，曲线实时重算；权重存在浏览器 `localStorage`，下次打开沿用。

- 滚动窗口可选 **20 / 60 / 120 日**，观察区间 **1Y / 2Y / 3Y / ALL**。
- 选 2 个标的：给出日收益散点图 + 线性回归线；选 3 个以上：按两两配对计算后取平均，下方列出 |ρ| 前 12 的配对明细。
- 结果全部由本地日线计算，不额外请求行情接口；同一组合 5 分钟内走缓存。
- 可直接分享链接：`/?view=analysis&ids=sh000001,us.INX,hf.GC`

## 目录结构

```
Market_monitor/
├─ run.py                     # 网页版入口：serve / download / check / list
├─ desktop.py                 # 桌面版入口（本地服务 + 独立窗口，exe 的启动脚本）
├─ requirements.txt
├─ assets/icon.ico            # 应用图标（tools/make_icon.py 生成）
├─ installer/
│  └─ Installer.cs            # 原生 C# 安装器（csc 编译，payload 内嵌）
├─ tools/
│  ├─ build.bat / build.ps1   # 打包 exe / 便携版 / 安装包
│  ├─ release.bat / release.ps1  # 同步 dist 到 release 并重算校验值
│  ├─ push.bat / push.ps1     # 提交并推送到远端
│  └─ make_icon.py
├─ data/                      # 源码模式的数据目录（桌面版在 %LOCALAPPDATA%）
│  ├─ market.db               # SQLite：K 线、快照、元数据
│  └─ exports/                # 下载时导出的 CSV
└─ market_monitor/
   ├─ config.py               # 标的目录（代码映射、历史源、精度等）
   ├─ net.py                  # HTTP 客户端：重试、限速、健康统计
   ├─ providers.py            # 新浪 / 腾讯 / CNBC / 财政部 / ECB 解析器
   ├─ analysis.py             # 滚动相关性：Pearson / Spearman / Kendall / 下行 / 波动 / 滞后
   ├─ store.py                # SQLite 存储与 CSV 导出
   ├─ service.py              # 抓取编排、交易时段、派生指标、下载任务
   ├─ server.py               # 标准库 HTTP 服务 + JSON API
   └─ web/                    # index.html / styles.css / app.js / vendor
```

## API

| 路径 | 说明 |
| --- | --- |
| `GET /api/overview` | 全部标的最新快照、分组、市场状态、数据源健康度 |
| `GET /api/history?id=sh000001&range=1Y` | 历史 K 线（`range` 取 `1M/3M/6M/1Y/3Y/ALL`） |
| `GET /api/intraday?id=sh000001` | 当日分时（分钟价 + 分钟量），30 秒缓存 |
| `GET /api/flow?id=sh000001&limit=120` | 每日资金流（流入 / 流出 / 净流入占比 %） |
| `GET /api/flow/markets` | 各市场累计净流入占比曲线 |
| `GET /api/sectors` | A 股行业列表（新浪行业分类） |
| `GET /api/sectors/board?node=new_dzxx&limit=50` | 行业前 N 只成分股（含总市值）与行业 ETF |
| `GET /api/stock?code=sh600176` | 个股详情：报价 + 日线 + 分时 + 资金流 |
| `GET /api/macro` | 前 20 大经济体宏观数据（`?refresh=1` 强制刷新） |
| `GET /api/sectors?market=us` | 美股 13 个 sector 及其涨跌幅、市值 |
| `GET /api/bonds` | 中美国债收益率曲线 + 美债月度发行量 |
| `GET /api/gold` | GDP 前 50 经济体的央行黄金储备与年度买卖 |
| `GET /api/gold/spot` | 伦敦金 / 纽约金 / 上海金现货报价与内外价差 |
| `GET /api/analysis?ids=a,b,c&window=60&range=2Y&weights=0.3,0.2,0.15,0.15,0.1,0.1` | 关联分析：6 种滚动相关性 + 加权综合指标 + 摘要 + 配对明细 |
| `GET /api/export?id=sh000001&interval=day` | 导出 CSV |
| `POST /api/download` · `GET /api/download/status` | 启动/查询批量下载任务 |
| `POST /api/refresh` | 立即刷新一次 |

## 已知限制

- 历史长度因源而异：中债收益率 2002 年起（6000+ 个交易日）、美债收益率约 10 年、A 股 / 港股 / 美股指数约 3 年、美股个股 10 年以上、期货 10~20 年；美元指数、VIX、离岸人民币来自 CNBC 图表接口，日线约 2 年、周线可回溯约 15 年。
- 中债 5Y / 30Y 没有免费盘中源，用东方财富当日收盘估值（约 17:30 更新）；2Y / 10Y 由 CNBC 提供盘中报价。
- 免费行情非交易所级实时：A 股 / 港股为 3 秒级快照，美股为 15 分钟延迟源，债券收益率随 CNBC 更新。
- 新浪美股日线为**源端调整后**的价格，与部分终端的前复权口径可能不同。
- **资金流是估算**：按 OHLCV 推导（收盘价在当日高低区间中的位置 = 流入占比），不是券商 / 交易所口径的「主力资金净流入」——后者需要逐笔成交数据，公开接口不稳定。
- **板块热力图目前只覆盖 A 股**：新浪行业分类共 49 个，行业 ETF 按名称匹配（部分行业没有对应 ETF）。港股 / 美股没有稳定的免费行业分类接口，暂未接入。
- **宏观数据可能不完整**：世界银行 API 对连续请求有限流，个别指标偶尔超时显示为「—」，点右上「刷新」可重试；中国、德国、法国等国家的「政府债务/GDP」在世界银行里本身缺报，也显示为「—」。
- **黄金储备是按年、且由估值反推**：世界银行只有年度储备数据，吨数由「黄金美元价值 ÷ 当年金价」换算（以美国 8,133.5 吨为校准锚点），因此是**年度净买卖**而非逐笔交易；世界黄金协会的月度央行购金数据没有公开 API。
- **中债发行量没有稳定免费源**：中国债券信息网的发行数据没有公开 API，债券视图里只有美债发行量。
- **港股行业热力图暂未接入**：东方财富的港股板块接口不稳定，目前只有 A 股与美股。
- **主权债务的官方源只接了美国、巴西**：各国统计口径与接口差异极大（英国 ONS 旧 API 已下线、澳大利亚 ABS 直连失败、IMF DataMapper 返回 403、日本财务省没有结构化接口），目前无法一次性覆盖 20 国。新增一个国家只需在 `market_monitor/sovereign_debt.py` 写一个抓取函数并注册到 `ADAPTERS`。
- 政策利率为代码内手动维护的参考值（`POLICY_RATES` / `POLICY_AS_OF`）。
- 本工具仅供研究参考，不构成任何投资建议。

## 本地数据

执行 `python run.py download`（或首次启动自动建档）后：

```
data/
├─ market.db        # 74 个标的 × 日/周/月，27 万+ 根 K 线
└─ exports/         # 每个标的每个周期一个 CSV：<id>_<interval>.csv
```

可在面板右栏点「导出 CSV」单独导出当前标的；`下载历史` 按钮会刷新全部并更新文件。
