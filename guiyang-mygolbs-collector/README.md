# 贵阳掌上公交 API 与每日已观测车辆采集

通过掌上公交官方H5使用的接口查询贵阳线路、站序、实时车辆及班次，并按日期累计“当天曾查询到的车辆”。支持公司/线路/编号筛选、受控并发、失败补采、断点续跑、SQLite存储和CSV导出。

Python **3.10+**，仅使用标准库，无需安装第三方包。Windows、macOS、Linux均可运行。以下接口和参数以2026年10月的成功调用方式为基础，接口可能变化。

> 这是网页内部接口的调用参考，并非服务方承诺的开放API。访问公开网页不等于获得批量采集授权；请确认适用的使用许可和频率要求，遇到访问拒绝或限流应停止。

## 快速开始

下载或克隆本项目后，进入包含`bus.py`的目录：

```bash
# 查看命令
python bus.py --help

# 查询贵阳3路一个方向的实时原始JSON
python bus.py query --cmd 104 --param LINENAME=3路 --param DIRECTION=1

# 采集3路目录中存在的所有方向，一轮
python bus.py collect --line 3路

# 默认筛选贵阳公交、贵阳黔爽相关公司，采集一轮
python bus.py collect
```

Windows如未配置`python`命令，可用`py -3`代替；macOS/Linux可用`python3`。示例不依赖Windows专用路径。

## API接入规范

### 请求地址与公共参数

```text
POST https://h5.mygolbs.com/ApiData.do
Content-Type: application/x-www-form-urlencoded
Origin: https://h5.mygolbs.com
Referer: https://h5.mygolbs.com/
User-Agent: Mozilla/5.0
```

参数通过表单发送，**不是JSON请求体**。字段名按以下大写形式传递。

| 参数 | 贵阳推荐值 | 说明 |
|---|---|---|
| `CMD` | 按功能选取，如`104` | 命令编号，按字符串发送 |
| `CITYNAME` | `贵阳市` | 推荐始终填写；省略城市码时用于按名称寻址 |
| `CITYCODE` | `520100` | 推荐的城市代码；与车来了的城市ID不是同一套编号 |
| `CITYKEY` | 省略或空字符串 | 是另一套城市配置参数，不等于CITYCODE；贵阳成功调用不需要非空CITYKEY |

成功请求不需要携带Cookie、Authorization或用户token，也不需要照搬车来了的`src/s/v`字段。

**Origin是关键条件。** 缺失时可能HTTP 200却返回非JSON响应，不能把它记成“没有车辆”。Referer建议保留；已有成功调用表明保留Origin时可省略Referer，因此不将Referer描述为必需项。`Mozilla/5.0`已可用，不要求必须伪装移动端UA。

### 命令与响应结构

| CMD | 功能 | 除城市外的关键参数 | 主要响应字段 |
|---|---|---|---|
| `101` | 城市列表 | 无 | `data[]` |
| `119` | 线路目录/目录过滤 | `KEY`，空串表示不按关键字过滤 | `buslines[]` |
| `102` | 线路搜索 | `KEYWORD` | `buslines[]` |
| `114` | 线路搜索，含更多线路信息 | `KEYWORD` | `buslines[]`，可有`routeBean` |
| `103` | 一个方向的站表、轨迹、首末班等 | `LINENAME`、`DIRECTION` | `data[]`、`routeId`、`firstLast`、`nihelist` |
| `104` | 实时车辆；可查询指定站的ETA | `LINENAME`、`DIRECTION`；可加`STATIONORDER` | **车辆在`list[]`**；ETA在`routeOnStationRTimeInfoList[]` |
| `106` | 周边站点 | `LAT`、`LNG` | `data[]` |
| `110` | 站点搜索 | `KEYWORD` | `busstations[]` |
| `115` | 站点经停线路 | `STATIONNAME`、`MYLAT`、`MYLNG`、`ALL` | `data[]` |
| `116` | 简版发车班次 | **`ROUTEID`**、`DIRECTION` | `list[]` |
| `207` | 分组发车班次 | **`ROUTEID`**、`DIRECTION` | `list[]`、可有`nextTime` |

101/106/110/115是公开脚本及接入参考中使用的只读命令，具体城市覆盖和参数语义应按实际响应核对。采集程序本身只依赖119和104，其余可通过`query`调用。

### 1. 先获取线路目录

```bash
python bus.py query --cmd 119 --param KEY= --out data/catalog.json
```

每个目录项常用字段：

```json
{
  "lineName": "3路",
  "upperOrDown": "1",
  "from": "起点名称",
  "to": "终点名称",
  "company": "贵阳公交中部分公司"
}
```

这是结构示意，目录应以实时返回为准。

- 用`(lineName, upperOrDown)`识别一个线路方向。
- `LINENAME`必须使用目录原名，保留B/K前缀、中文括号、旅游或定制名称。
- `DIRECTION`取该项`upperOrDown`，通常为`1`或`2`；不强行请求目录中不存在的方向。
- `KEY=3路`或`KEYWORD=3路`是包含匹配，还会匹配13路等名称，客户端需按原名精确筛选。
- `company`用于筛选运营公司；它是上游标签，不自动等同于工商登记全称。

### 2. 查询实时车辆

```bash
python bus.py query --cmd 104 --param LINENAME=3路 --param DIRECTION=1
```

等价的curl请求（以下换行写法适用于Bash；Windows可直接使用上面的Python命令）：

```bash
curl 'https://h5.mygolbs.com/ApiData.do' \
  -H 'Origin: https://h5.mygolbs.com' \
  -H 'Referer: https://h5.mygolbs.com/' \
  -A 'Mozilla/5.0' \
  --data-urlencode 'CMD=104' \
  --data-urlencode 'CITYNAME=贵阳市' \
  --data-urlencode 'CITYCODE=520100' \
  --data-urlencode 'LINENAME=3路' \
  --data-urlencode 'DIRECTION=1'
```

车辆标识读取`list[].busNumber`，**按文本保留前导零**。贵阳可能返回数字编号，不能直接假定所有城市的busNumber都是车牌或企业自编号。

`data[]`不是车辆列表。车辆可能附带`bus_lat/bus_lng`、站序及`_recTime`等字段；本项目名册只保存车辆标识和查询时间，不累积连续GPS轨迹。`--save-raw`会额外保存原始响应，其中可能包含位置字段。

### 3. 站序、到站预测与班次

```bash
# 先取得stationOrder与routeId
python bus.py query --cmd 103 --param LINENAME=3路 --param DIRECTION=1 --out data/route.json

# STATIONORDER取站表中的stationOrder，例如10
python bus.py query --cmd 104 --param LINENAME=3路 --param DIRECTION=1 --param STATIONORDER=10
```

103使用`LINENAME`；116/207使用103返回的`routeId`，两者不能混用。将下方示例值31替换成**当前103响应中的routeId**：

```bash
python bus.py query --cmd 207 --param ROUTEID=31 --param DIRECTION=1
python bus.py query --cmd 116 --param ROUTEID=31 --param DIRECTION=1
```

ETA不一定覆盖全部车辆。没有到站预测不等于没有车；班次表也不能证明某辆车已经实际出勤。

### 4. 城市名兼容方式与入口边界

如果要复现省略城市码的调用，将`config.example.json`复制为`config.local.json`，设置：

```json
{
  "city_name": "贵阳市",
  "city_code": ""
}
```

```bash
python bus.py --config config.local.json query --cmd 104 --param CITYKEY= --param LINENAME=3路 --param DIRECTION=1
```

城市码+城市名、仅城市名、仅城市名+空CITYKEY均有成功调用依据，但它们**共享H5后端**，仅作为参数兼容方式，不能抵御该后端整体故障。本项目不会在被限流或拒绝访问后自动切换参数或地址。

其他公开入口应区别对待：

- `h5.mygolbs.com/line.html`、`linelist.html?key=…`仍使用同一H5后端，不是独立数据源。
- 页面小写`key`与API的`KEY`、`KEYWORD`、`CITYKEY`含义不同，不要混用。未提供或枚举贵阳专属key。
- `wx.mygolbs.com/WxBusServer/ApiData.do`是公开代码中出现的小程序旧地址，尚未验证可作贵阳备用实时接口。
- `quanguo.mygolbs.com:8081/...HttpHandlerServlet`只有历史地址线索，未验证实时查询协议，不作为备用接口配置。
- `www.mygolbs.com`和`m.mygolbs.com`是官网入口，不应直接套用H5的API路径。

## 批量采集

### 公司、线路及编号筛选

```bash
# 默认：company以“贵阳公交”或“贵阳黔爽”开头
python bus.py collect

# 自定义公司前缀，可重复指定
python bus.py collect --company-prefix 贵阳公交北部 --company-prefix 贵阳公交黔爽

# 指定线路原名；覆盖默认公司筛选
python bus.py collect --line 3路 --line B1路

# 全目录，不按company筛选
python bus.py collect --all

# 只查1至100路普通数字线路；--all表示同时取消默认公司筛选
python bus.py collect --all --min-number 1 --max-number 100

# 101路及以上普通数字线路
python bus.py collect --all --min-number 101
```

`--all`、`--line`、`--company-prefix`互斥；数字范围可以与其中一种组合。数字范围只匹配完整的“数字+路”，不包含B、K、夜间等前缀。若不加`--all`，数字范围仍受默认公司筛选约束。

### 并发、补采与多轮累计

```bash
# 默认单线程；可在确认适用频率后设置有限并发
python bus.py collect --workers 4 --batch-pause 3 --retries 3

# 多轮运行：0代表持续，Ctrl+C停止
python bus.py collect --rounds 0 --pause 300

# 采集两轮，并保存原始响应
python bus.py collect --rounds 2 --save-raw
```

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `--workers` | 1 | 同时请求数量，允许1—12；12是项目配置上界，不是平台承载能力承诺 |
| `--batch-pause` | 3 | 一批请求全部完成后的等待秒数，最小3秒；不是每个请求的全局发送间隔 |
| `--timeout` | 25 | 网络超时秒数，针对套接字操作，不保证整轮在固定时间结束 |
| `--retries` | 3 | 每个方向最多额外补试次数；初次+补试总数最多4 |
| `--rounds` | 1 | 轮数，0表示持续运行 |
| `--pause` | 300 | 两轮之间等待秒数 |
| `--output` | `data` | 输出根目录，相对当前工作目录解析 |
| `--save-raw` | 关闭 | 保存各次有效JSON响应 |

超时、连接中断及部分临时HTTP错误会在本轮其他对象处理后重新入队，重试逐步降速。最近多批失败比例偏高时也会降低并发，不自动超过用户配置。401/403/429立即停止后续提交；已经发出的同批请求可能仍需等待结束。

目录在创建任务时读取一次并冻结。持续运行中的新线路不会自动进入任务；需停止后新建任务刷新目录。不要同时运行两个采集进程或对同一任务并行续跑；需要更高覆盖率时优先合理缩小筛选范围并多轮累计。

### 断点续跑

每个任务输出独立目录，启动时会打印路径：

```bash
python bus.py collect --resume data/jobs/实际任务目录名
```

续跑使用该任务保存的城市、筛选、轮数、并发和重试设置；不会应用新的筛选或并发覆盖参数。SQLite保存每次尝试，已成功和实时不可用方向不会在同一轮重采，重试次数也不会因续跑被重置。

保留整个`data/`目录：只有任务文件夹、没有同级`attendance.sqlite3`时不能可靠续跑。已完成任务续跑不会再发新请求；想重新查一轮请创建新任务。达到补试次数上限或非网络解析失败的对象保留失败状态，不无限重试。

## 输出与数据口径

```text
data/
  attendance.sqlite3       # 跨任务、跨日期累计的SQLite数据库
  daily_by_direction.csv   # 按日期/城市/线路/方向/车辆去重
  daily_by_line.csv        # 合并同线路两个方向后的每日名册
  jobs/
    任务ID/
      job.json             # 冻结配置与目标方向，供续跑使用
      catalog.json         # 创建任务时的目录快照
      summary.json         # 当前进度、最终状态与去重车辆数
      attempts.csv         # 每一次查询结果，包括失败原因
      vehicles.csv         # 本任务每轮逐车明细
      line_summary.csv     # 本任务按线路合并的车辆列表
      raw/                 # 仅启用--save-raw时生成
```

```bash
# 从已有SQLite重新导出所有已存日期
python bus.py export --output data
```

- `success`：收到有效车辆列表，可能为空。
- `unavailable`：没有车辆列表且`hasReal=0`，表示实时数据不可用，不能据此断言线路停运。
- `failed`：请求失败、业务失败或响应结构不符合要求。
- `pending`：尚未写入查询结果，常见于中断或访问拒绝后。

每日去重键为北京时间日期、城市配置、线路、方向、车辆标识。城市键包含城市名和城市码；切换为无城市码配置后会分开归档，避免不同城市配置误合并。`daily_by_line.csv`合并同一城市同线路的两个方向；`vehicles.csv`仍保留轮次和方向。

首次/末次观测时间是查询时间，`observations`是查询命中次数，不是实际发车时刻或班次数。成功空列表不代表当天没出勤；不同方向采集时间不同，不能称为同一时刻全城快照。最终口径始终是**“当日已观测车辆”**。

CSV为UTF-8 BOM。用Excel导入时将`vehicle`列设为文本，以保留前导零。默认数据目录、SQLite和本地配置已加入`.gitignore`；若自行指定其他输出路径，上传GitHub前也应排除该路径。

退出码：`0`正常结束（允许实时不可用）；`1`配置或未处理错误；`2`中断/访问拒绝停止；`3`任务结束但有对象最终失败。可进一步查看`summary.json`与`attempts.csv`。

## 常见问题

- **HTTP 200但解析失败**：检查Origin、表单格式和地址；不要将非JSON降级为空数组。
- **城市不可用**：核对CITYNAME/CITYCODE；城市名兼容方式只能在有证据的场景使用，不保证所有城市适用。
- **103/104为500**：核对LINENAME与目录完全一致、方向真实存在；500也可能是上游临时故障。不要自动解释为零车。
- **线路名搜索不精确**：目录/搜索接口是包含匹配，采集器按目录原名精确选择。
- **无ETA但有车**：车辆来自list；ETA需要有效STATIONORDER，且预测条目可能少于车辆数。
- **持续运行却没发现新线路**：当前任务使用目录快照，创建新任务更新目录。

## 开发与项目结构

```text
bus.py                  # 统一命令行入口
config.example.json     # 可复制为config.local.json
mygolbs/api.py          # API表单、请求头、错误及实时结果解析
mygolbs/collector.py    # 筛选、并发、补采、轮次与续跑
mygolbs/storage.py      # SQLite、幂等写入及CSV导出
tests/test_core.py      # 离线功能测试，不访问真实服务
LICENSE                 # 项目代码使用MIT许可证
```

```bash
python -m unittest discover -s tests -v
```

代码许可证不授予第三方API使用权或数据再分发权。发布包不包含历史车辆名单、真实响应、登录凭据、第三方前端源码或压力探测脚本。

## 参考来源

- [掌上公交官方H5](https://h5.mygolbs.com/)
- [官方H5前端脚本](https://h5.mygolbs.com/js/mybus.js?version=1.2.2)：CMD、表单字段及页面参数的主要依据。
- [yulin-bus-activity](https://github.com/misakano7545/yulin-bus-activity)，尤其是[掌上公交客户端](https://github.com/misakano7545/yulin-bus-activity/blob/main/internal/mygolbs/mygolbs.go)：接入思路参考。本项目代码独立整理，贵阳字段含义以贵阳实际响应为准。

维护时先复现“119目录→103站表→104车辆”这条最短链路，再考虑搜索、ETA和班次功能。不要依赖未经验证的备用域名或猜测城市密钥。
