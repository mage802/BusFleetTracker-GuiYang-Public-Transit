# BusFleetTracker — 贵阳公交车辆出勤与运营观察

本仓库包含两套独立的 Python 采集工具，分别读取**车来了**与**掌上公交**平台返回的线路、方向和车辆信息，保存每日已观测车辆及逐次运营记录。

当前已实现的是“采集 → 本地 SQLite → 出勤名单／运营明细导出”。两套工具分别运行、分别存储；尚未实现跨平台车辆身份映射、统一业务数据库、网页查询界面或实际班次自动重建。

> “出勤”在本项目中指某车辆标识在某日曾被数据源观测到。未被观测到不等于未出勤，记录条数也不等于发车班次数。数据可能延迟、缺失或由平台估计。

## 现有功能对照

| 能力 | 车来了 `chelaile-attendance` | 掌上公交 `guiyang-mygolbs-collector` |
|---|---|---|
| 城市与线路目录 | 城市目录、全市线路方向目录；目录缓存24小时 | 只读城市查询；采集开始读取并冻结线路目录 |
| 单线路查询 | 按城市ID和方向 `lineId` 查询 | 按目录原名 `LINENAME` 和 `DIRECTION` 查询 |
| 批量范围 | 所选城市全部目录方向 | 默认公司前缀筛选；支持全目录、指定线路、公司和数字编号范围 |
| 上下行站点 | 线路详情返回该方向 `stations` | `query --cmd 103` 查询指定方向站表 |
| 每日出勤 | 日期／城市／方向／busId存储，主列表按日期／城市／busId合并 | 日期／城市配置／线路／方向／busNumber存储，另导出按线路合并的名单 |
| 逐次运营记录 | 同车不同采集时间追加历史；相同观察重新导入不重复新增 | 按任务、轮次、方向、尝试与车辆条目保存；同一次尝试重放不重复写入 |
| 运营CSV | 六列主表＋包含信号时间和平台ID的审核表 | 六列主表；额外质量字段保存在SQLite中 |
| 限速／并发 | 1—16线程；同输出目录共享请求起始间隔及每日预算 | 1—12并发；每批完成后暂停，重试和高失败率时降低并发 |
| 重复运行 | 多轮／持续扫描；出勤与运营历史继续累计 | 多轮／持续采集；可恢复同一任务进度 |
| 失败处理 | 记录方向错误；401/403/429或预算耗尽时停止，无自动网络重试 | 临时网络错误受限补采；401/403/429停止后续提交 |
| 离线功能 | 解密响应、导入旧版全市快照、按日期重新导出 | 从SQLite导出累计日期及任务结果 |

上下行必须分别使用平台目录中的方向标识查询；两方向站点不一定互为倒序。当前代码可查询方向站表，但没有独立的“全市全部站点统一建库”命令。

## 仓库结构

```text
BusFleetTracker-GuiYang-Public-Transit/
├── README.md
├── chelaile-attendance/
│   ├── chelaile.py                  # CLI、请求、出勤累计和扫描
│   ├── operation_records.py         # 逐次运营历史与六列/审核CSV
│   ├── import_history.py            # 旧版全市存档离线导入
│   ├── h5_business.example.json      # H5方向/站点参数示例
│   ├── requirements.txt
│   ├── tests/test_core.py
│   └── README.md
└── guiyang-mygolbs-collector/
    ├── bus.py                       # CLI
    ├── config.example.json
    ├── mygolbs/
    │   ├── api.py                   # 表单API、业务状态与错误
    │   ├── collector.py             # 筛选、轮询、补采及续跑
    │   ├── operations.py            # 六列运营字段解析
    │   ├── storage.py               # SQLite、历史与导出
    │   └── __init__.py
    ├── requirements.txt
    ├── tests/test_core.py
    ├── LICENSE
    └── README.md
```

- [车来了：完整API、渠道、签名与解密说明](./chelaile-attendance/README.md)
- [掌上公交：完整API、命令、筛选及续跑说明](./guiyang-mygolbs-collector/README.md)

## 安装与首次运行

需要 Python **3.10+**。Windows可用 `py -3` 代替 `python`，Linux/macOS通常使用 `python3`。以下示例均在对应子目录内执行，不依赖其它本地工程。

```bash
git clone https://github.com/mage802/BusFleetTracker-GuiYang-Public-Transit.git
cd BusFleetTracker-GuiYang-Public-Transit
```

### 车来了

进入 `chelaile-attendance` 后安装 `cryptography` 依赖：

```bash
cd chelaile-attendance
python -m pip install -r requirements.txt

# 贵阳3路一个方向，解析车辆并写入出勤与运营记录
python chelaile.py line --city 083 --line-id 851259873106

# 贵阳全市线路方向目录
python chelaile.py catalogue --city 083

# 全市目录方向采集一轮
python chelaile.py scan --city 083

# 每轮起始至少相隔600秒，持续采集，受每日请求预算约束
python chelaile.py scan --city 083 --rounds 0 --period 600 --daily-budget 5000

# 离线导出已保存日期；替换成实际日期
python chelaile.py export --day 2026-10-25
```

`line` 默认样例为贵阳3路“长坡路口 → 贵州日报社”；其它线路、方向需使用目录返回的 `lineId`。不要将单线路命令理解为已经采集了同名线路的所有方向。

默认 `--workers 1 --interval 1 --daily-budget 1200`。线程上限与速率是独立控制；`--interval` 是同一输出目录共享的最小请求起始间隔，`--period` 是相邻轮次起始的最小间隔。一轮已超过该间隔时不会再额外等待。目录请求和失败请求同样占用预算。

重启扫描会重新遍历方向并累积观察，不会恢复为“从上次某一方向继续”的冻结任务。遇到预算耗尽或访问拒绝会退出；代码不会自动切换渠道继续采集。

### 掌上公交

从仓库根目录进入 `guiyang-mygolbs-collector`。该模块仅用Python标准库，无第三方依赖：

```bash
cd guiyang-mygolbs-collector

# 贵阳3路一个方向的原始实时响应；query本身不累计出勤
python bus.py query --cmd 104 --param LINENAME=3路 --param DIRECTION=1

# 采集3路在目录中存在的所有方向，写入数据库
python bus.py collect --line 3路

# 默认选择company以“贵阳公交”或“贵阳黔爽”开头的线路
python bus.py collect

# 全目录；不按默认公司前缀筛选
python bus.py collect --all

# 每轮完成后等待300秒，持续采集
python bus.py collect --rounds 0 --pause 300

# 恢复任务；替换成启动时打印的实际任务目录
python bus.py collect --resume data/jobs/实际任务目录名

# 从数据库重新导出已有记录，不发查询请求
python bus.py export --output data
```

`collect --line` 使用目录中的精确线路原名（例如 `3路`，而不是仅 `3`）；`--all`、`--line`、`--company-prefix` 互斥。数字范围仅匹配“数字+路”，具体示例见子项目文档。

默认 `workers=1`、`batch_pause=3`、`retries=3`、`rounds=1`。批次暂停发生在同批请求全部完成之后，**不是车来了那种全局请求起始间隔**。重试次数是初次请求之外的额外次数。`--pause` 是两轮完成后的等待秒数。

自定义城市和默认参数可复制 `config.example.json` 为本地配置，再将 `--config` 放在子命令前：

```bash
python bus.py --config config.local.json collect --line 3路
```

续跑读取任务保存的配置与目录，不能借续跑参数改变目标或并发。已成功／实时不可用方向在同一轮跳过；新的一轮仍会再次查询这些方向以发现新记录。已完成任务续跑不再采集，想重新查询应新建任务。持续任务不会自动发现新线路，需新建任务刷新目录。

## API与参数边界

两个平台的城市代码、方向编码、车辆字段及鉴权/解析协议不同，不能混用。

| 项目 | 车来了 | 掌上公交 |
|---|---|---|
| 基础地址 | `https://web.chelaile.net.cn` | `https://h5.mygolbs.com/ApiData.do` |
| 请求方式 | GET，URL查询参数 | POST，`application/x-www-form-urlencoded`表单 |
| 贵阳城市参数 | `cityId=083` | `CITYNAME=贵阳市`、`CITYCODE=520100` |
| 方向标识 | 不同方向对应不同 `lineId` | `LINENAME`＋目录 `upperOrDown` 对应的 `DIRECTION` |
| 车辆数组 | 解密后的 `buses[]` | `list[]` |
| 车辆标识 | `busId`；展示字段 `licence`不一定是车牌 | `busNumber` |
| 接入要点 | 参数插入顺序、cryptoSign、对应响应密钥与Referer | 大写表单字段、Origin、业务 `status=1` |
| 默认采集协议 | 小程序配置 `src=weixinapp_cx` | H5表单协议，无须套用车来了的src、版本或AES密钥 |

车来了主要路径：

- `GET /cdatasource/citylist`：城市目录。
- `GET /api/bus/cityLineList`：城市线路方向目录。
- `GET /api/bus/line!encryptedLineDetail.action`：方向详情、站点与车辆。默认小程序配置按全部参数计算带引号的MD5签名，并使用对应ASCII密钥做AES-256-ECB/PKCS7响应解析。
- H5单线路备选为 `webapp_alipay_fuwu`、`webapp_meizu_map`，需使用 `--profile h5 --business h5_business.example.json`，并匹配站点业务参数、H5签名与密钥，不能只更换src。全市scan仅使用小程序配置。

掌上公交主要只读命令：

- `CMD=119, KEY=`：线路方向目录。
- `CMD=103, LINENAME=…, DIRECTION=…`：该方向站表与线路信息。
- `CMD=104, LINENAME=…, DIRECTION=…`：实时车辆；可附有效 `STATIONORDER` 查询站点到达预测。
- 采集器自动流程只依赖119与104；站表、搜索与班次等其它允许的命令通过 `query` 手动调用，不等于已自动整理进统一档案。

HTTP 200不等于取得有效车辆数据；解析失败、业务错误、有效空数组和实时数据不可用应分别判断。没有ETA也不等于没有车辆。公开网页入口或协议复现不代表服务方承诺开放API。

## 两类结果：出勤名单与运营历史

### 出勤名单

用于回答“某天见过哪些车辆标识、出现在哪些线路/方向、首次和最后何时见到”。

- **车来了**：SQLite按日期、城市、方向ID与busId保存；`vehicle_list`再按当日城市＋busId合并，保留关联方向ID。
- **掌上公交**：`daily_by_direction.csv`按日期、城市配置、线路、方向、车辆去重；`daily_by_line.csv`合并同一线路两方向。它不是跨所有线路去重的全市单车主表。
- 两者都会继续查询已经见过的车所属方向，出勤去重并不意味着以后不再采集该车。

### 逐次运营记录

两套代码都已实现六列CSV：

| 列名 | 含义 |
|---|---|
| 车号 | 平台返回的车辆标识或展示编号，不保证是真实车牌/企业自编号 |
| 运营线路 | 平台线路名称；掌上公交对简单数字/字母数字线路去掉“路”后缀 |
| 运营方向 | 该方向的终到站名称，不仅是0/1/2方向代码 |
| 下一站名称 | 行驶状态显示前方站；到站状态按本项目约定显示当前所在站 |
| 距离下站距离（米） | 行驶时取平台相应距离字段；到站时填0，异常/未知时留空 |
| 采集时间 | 本次响应的本地观察时间，统一UTC+8；不是实际发车或到站时刻 |

到站显示“本站、0米”是输出约定，**不表示距离后续站点0米**。两平台状态值不同：

- 车来了：`state=0`结合 `order`、`stations` 和 `distanceToSc`定位前方站及距离；`state=1`显示本站、0米；排除 `rType=1`历史行。
- 掌上公交：`statusType=0`显示接口 `stationName`、0米；`statusType=2`读取 `busToStationNiheDistance`；其它未确认状态不猜测站名/距离。
- 下一站距离不是到所选候车站的距离，不应以 `distanceToWaitStn` 或ETA字段直接替代。
- 车来了审核CSV保留车辆信号时间；掌上公交将 `_recTime`转换后的信号时间及质量标记保存在SQLite中，当前六列CSV没有额外审核列。

不同采集时间继续追加运营历史，不因当天已经出现过该车辆而覆盖旧记录。导出会重写CSV文件，但内容来自已累计的数据库记录。保留SQLite数据库才能持续追加，不能只保留最后导出的CSV。

车来了支持从规定布局的旧快照补录，并在重复导入相同观察时更新派生站名/距离而不重复新增。掌上公交依任务/轮次/方向/尝试去重，同一次任务尝试重放不会重复增加历史。此前没有存下的中间位置无法从首次/最后出现时间恢复。

## 输出文件

两个子项目的默认 `data/` 均相对各自运行目录解析，互不共享。可通过车来了 `--out` 或掌上公交 `--output` 指定独立输出位置。

### 车来了

```text
chelaile-attendance/data/
├── state.sqlite3
├── catalogue_083.json
├── line_decoded.json                 # 最近一次单线路结果
├── YYYY-MM-DD/
│   ├── vehicle_list.json
│   ├── vehicle_list.csv
│   ├── coverage.json
│   ├── operation_records.csv
│   └── operation_records_audit.csv
└── snapshots/                        # scan --save-responses启用
```

`coverage.json`记录各方向的成功/失败、原始车辆数和缺失ID诊断。单线路命令保存 `line_decoded.json`；扫描的可选snapshot仅保留各方向最近一次成功结果，逐次历史仍在数据库中。

### 掌上公交

```text
guiyang-mygolbs-collector/data/
├── attendance.sqlite3
├── daily_by_direction.csv
├── daily_by_line.csv
├── operation_records.csv             # 所有已存日期的六列明细
├── YYYY-MM-DD/
│   └── operation_records.csv
└── jobs/任务ID/
    ├── job.json                      # 冻结配置、目标方向、进度
    ├── catalog.json
    ├── summary.json
    ├── attempts.csv
    ├── vehicles.csv
    ├── line_summary.csv
    ├── operation_records.csv         # 当前任务的全部运营历史
    └── raw/                          # --save-raw启用
```

`vehicles.csv`和任务汇总按每轮方向的最新尝试整理；全部尝试在 `attempts.csv`/数据库中，逐次运营历史在 `operation_records`表和相应CSV中。

CSV采用UTF-8 BOM。Excel导入时将车号、busId、车辆列设为**文本**，避免前导零丢失。

## 车辆身份与数据限制

App显示“未知车号”并不一定代表接口没有标识：

1. **展示层不显示**：接口可能返回可识别编号，但页面不展示，例如此前韶关场景的调查线索。
2. **接口仅返回不透明ID**：例如此前北京345快返回过的长数字标识，不能直接称为真实车号。

接口响应AES解密与真实车辆身份映射是两件事。仅凭返回数字不能确定上游是在运营企业侧加密、平台侧映射、哈希还是随机分配；恢复真实编号需要可核实的字段定义、合法对应资料或授权密钥。本仓库没有实现这部分身份还原。

两平台相同数字也不能自动视为同一台车。未验证标识稳定性前，统计的是“见过的平台标识”，不保证等于实体车辆总数。采集时间与车辆信号时间可能相差较大；连续采样也不等于经过验证的真实GPS轨迹。

## 实际运行边界

- 持续采集需要电脑/服务器及进程保持运行；仓库没有定时唤醒、系统服务、云部署或GitHub Actions自动运行配置。
- 车来了默认每日预算1200次可能只够有限轮次；持续全天运行应结合方向数、间隔与服务许可规划。预算按本地UTC+8日期计算。
- 两套工具没有跨平台/跨输出目录的统一限速器。不要通过多个输出目录或多个进程叠加请求。
- 两者都在401/403/429后停止后续请求，不自动换渠道、代理或身份继续访问。已经发出的并发请求可能仍需完成。
- 部分方向失败时查看覆盖/尝试记录；不能把缺失或错误记录当作全天零出勤。
- 新线路和临时调整依上游目录及缓存/任务快照，不能保证即时发现。
- 当前是本地采集器，不含面向公众的Web界面、统一车辆/企业档案、中心化数据库服务、跨来源自动合并及班次推断。这些可作为后续开发方向。

## 本地测试

测试使用本地数据和模拟请求，不会查询真实公交服务。在各子目录分别执行：

```bash
python -m unittest discover -s tests -v
```

车来了覆盖响应解析、签名、解密、历史追加、到站0米及出勤去重；掌上公交覆盖表单请求、筛选、错误分类、重试上限、幂等与中断续跑。通过本地测试不代表已重新验证上游接口当前可用。

## 发布与许可

上传源码前排除运行产生的 `data/`、`*.sqlite3*`、`.venv/`、`__pycache__/`及本地配置。当前仓库未包含可依赖的 `.gitignore`文件，不要假定这些内容会被Git自动忽略；应在本地另行设置。不要公开Cookie、个人标识、登录凭据或未经确认可再分发的原始数据。

`guiyang-mygolbs-collector/LICENSE`声明该子项目使用MIT许可证；仓库整体与车来了目录没有明确的独立许可证，不能将子项目MIT授权自动扩大到整个仓库。代码许可证不授予第三方API访问或数据再分发权。

本项目与车来了、掌上公交及公交运营企业无官方合作或授权关系。使用者需确认适用的服务条款、访问许可和合理频率，数据不应用作高可靠性运营调度依据。
