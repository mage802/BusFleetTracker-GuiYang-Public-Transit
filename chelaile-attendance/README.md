# 车来了车辆出勤采集

通过车来了网页／小程序所使用的数据接口查询城市线路方向，解析原始数据，将当天采集到的车号去重整理全市车辆出勤清单。支持单线路查询、全市逐方向扫描、重复采集、SQLite持久化、JSON/CSV导出及离线解密。

这是独立整理的协议复现工具，非车来了官方SDK。下述版本、密钥、参数是已成功使用的配置快照，不代表最新官方版本，也不保证所有城市可用。目录存在不代表该城市一定提供实时车辆数据。请按数据提供方的规则及实际授权范围使用。

## 快速开始

Python 3.10及以上。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 贵阳3路：默认小程序配置，查询原始buses并导出当日车辆
.\.venv\Scripts\python.exe chelaile.py line --city 083 --line-id 851259873106

# 贵阳全市线路方向目录
.\.venv\Scripts\python.exe chelaile.py catalogue --city 083

# 逐方向采集一轮，所有车辆合并到同一份vehicle_list
.\.venv\Scripts\python.exe chelaile.py scan --city 083
```

Linux/macOS使用 `python3 -m venv .venv`，将后续解释器路径替换为 `.venv/bin/python`。以下示例用 `python` 代表已安装依赖的解释器。

```bash
# 查询城市目录
python chelaile.py cities

# 明确启用并发上限16；仍共享1秒一次的全局请求起始间隔
python chelaile.py scan --city 083 --workers 16 --interval 1 --daily-budget 1200

# 每轮起始至少相隔300秒，最多3轮；根据计划自行设置足够的请求预算
python chelaile.py scan --city 083 --rounds 3 --period 300 --daily-budget 2500

# 持续轮询直到中断或预算用完；不是后台服务，终端需保持运行
python chelaile.py scan --city 083 --rounds 0 --period 600 --daily-budget 5000

# 导出数据库中某一天已经观察到的车辆，不发网络请求
python chelaile.py export --day 2026-10-17
```

示例城市与线路可替换，但ID必须作为字符串保留前导零；不同方向使用不同 `lineId`，不能只按线路名称去重。

## API地址与作用

域名：`https://web.chelaile.net.cn`，请求方法均为GET，查询参数使用标准URL编码。

| 路径 | 用途 | 主要返回字段 |
|---|---|---|
| `/cdatasource/citylist` | 城市目录 | `data.allRealtimeCity` |
| `/api/bus/cityLineList` | 指定城市全部线路方向目录 | `data.allLines.all` |
| `/api/bus/line!encryptedLineDetail.action` | 单方向线路详情及车辆 | 解密后 `line`、`stations`、`buses`、`targetOrder` |

城市目录用 `cities` 查看。线路目录按 `lineId` 去重，保留线路名、起终点。目录缓存24小时；`catalogue --refresh` 或 `scan --refresh` 强制刷新。目录查询走H5公共配置，车辆扫描走小程序配置，两者不能混为同一请求。

HTTP 200只表示网络请求成功。还必须检查外层业务状态、密文是否可解、解密后的 `buses` 是否存在。`buses=[]` 是有效的空结果；字段缺失、业务错误、解密失败会记录为错误，不当作“无车”。

## 配置A：小程序协议，默认全市采集方案

对应渠道：`src=weixinapp_cx`。对应环境为微信小程序；以下Referer不是可独立打开的公交查询网页：

```text
https://servicewechat.com/wx71d589ea01ce3321/814/page-frame.html
```

请求参数顺序如下。签名依赖插入顺序，不要排序，也不要删除空字符串：

```json
{
  "s": "h5",
  "wxs": "wx_app",
  "sign": "1",
  "h5RealData": "1",
  "v": "3.11.28",
  "src": "weixinapp_cx",
  "ctm_mp": "mp_wx",
  "vc": "2",
  "favoriteGray": "1",
  "gpstype": "wgs",
  "geo_type": "wgs",
  "scene": "1256",
  "cityId": "083",
  "localCityId": "083",
  "lineId": "851259873106",
  "lat": "",
  "lng": "",
  "geo_lat": "",
  "geo_lng": ""
}
```

- `cityId` / `localCityId`：查询城市，本例贵阳为 `083`。
- `lineId`：线路方向标识，本例为3路长坡路口→贵州日报社。
- `v` / `vc`：客户端协议版本及版本代码，不是本项目版本号。
- `src` / `wxs` / `ctm_mp`：渠道与运行环境参数，应随完整配置使用。
- `h5RealData`：注意大小写，不能替换成 `h5realdata`。
- 该请求不传站点参数，在上述样本中返回终点视角。其它城市应检查返回的 `targetOrder` 与 `stations`，不能直接认定无站点参数就能覆盖全线。
- 成功配置没有使用账户Cookie、个人OpenId或UnionId。本项目不包含用户抓包中的身份信息。

请求头的Referer、微信环境User-Agent、Content-Type及Sec-Fetch字段已放入 `chelaile.py` 的 `MINI_HEADERS`。这些字段与参数、签名、密钥共同构成可复现配置；没有逐字段证明哪一项是唯一必要条件。

### 小程序cryptoSign

1. 取添加 `cryptoSign` 前的**全部参数**，按插入顺序处理，包括空字符串。
2. 每项拼为 `"键"="值"`，带双引号，用 `&` 连接。
3. 尾部追加固定字符串 `qwihrnbtmj`。
4. 对UTF-8字节做MD5，取小写十六进制字符串，添加为 `cryptoSign`。
5. 最后再做URL查询参数编码，发送请求。不要先URL编码再签名。

代码实现为 `signature(params, "mini")`。

## 配置B：H5单线路备选

对应网页入口及实际Referer：

```text
https://web.chelaile.net.cn/ch5/index.html
```

已成功用于贵阳3路的两个渠道：`webapp_alipay_fuwu`、`webapp_meizu_map`。它们共用同一数据域名，不能视为独立数据源。入口页面是否能直接加载，与接口完整配置能否返回数据是不同问题。

```bash
python chelaile.py line --city 083 --line-id 851259873106 --profile h5 --src webapp_alipay_fuwu --business h5_business.example.json
python chelaile.py line --city 083 --line-id 851259873106 --profile h5 --src webapp_meizu_map --business h5_business.example.json
```

随包业务参数示例是贵阳3路头桥②站视角，不是终点视角：

```json
{
  "lineId": "851259873106",
  "lineName": "3",
  "direction": 0,
  "stationName": "头桥②",
  "nextStationName": "延安西路①",
  "lineNo": "r83283",
  "targetOrder": 10
}
```

更换方向或站点必须同时核对上述字段。`lineNo`可能是内部编号，与显示名称 `lineName`不同；不要一律替换成“3”。站序以接口返回为准，不自行猜测。

H5签名只处理上述7个业务参数：按文件顺序拼为 `键=值`，**不带引号**，用 `&` 连接，追加 `qwihrnbtmj` 后做MD5。随后添加 `cryptoSign`，再合并公共参数：

```text
cityId=083, s=h5, v=3.3.21, vc=1, sign=1,
src=所选H5渠道, userId=本地持久匿名访客, h5Id=相同访客
```

本工具只对 `line` 开放这套H5站点配置，全市 `scan` 使用小程序配置，避免将某条线路的站点参数错误套到全市。H5访客在本地SQLite首次生成并重复使用，不使用仓库作者或手机抓包中的个人标识。

## 密文解析

典型外层响应经过 `**YGKJ ... YGKJ##` 包装，内部为JSON：

```json
{"jsonr":{"status":"00","data":{"encryptResult":"Base64密文"}}}
```

按以下顺序解析：去除已识别的包装标记 → 解析外层JSON → 检查业务状态 → 取 `jsonr.data.encryptResult` → Base64解码 → AES解密 → 严格移除PKCS7填充 → UTF-8解码 → JSON解析。

| 配置 | ASCII密钥 | 算法 |
|---|---|---|
| 小程序 `mini` | `FF32AE65FBFD19414EAAFF6291A54B42` | AES-256-ECB / PKCS7 |
| H5 `h5` | `422556651C7F7B2B5C266EED06068230` | AES-256-ECB / PKCS7 |

两者均将字符串按ASCII/UTF-8编码成**32字节**，不是按十六进制解析成16字节；ECB不传IV。响应密钥与请求签名是不同步骤，不能混用。代码不会盲目尝试不同密钥或去掉未知密文头部。

离线解析已保存的完整响应文本：

```bash
python chelaile.py decode --profile mini --input response.txt --out decoded_output
python chelaile.py decode --profile h5 --input response.txt --out decoded_output
```

结果为 `decoded_output/decoded.json`。若密文字节长度不是16的倍数，当前格式不适用；不要将解密错误写成空车辆。早期App接口的另一种封装不在本工具支持范围内。

## 车辆出勤与输出

车辆记录来自原始 `buses`，不按到站时间筛选，也不按“最近几辆”截断。程序排除 `rType=1` 的历史行；缺失 `busId` 的行计入诊断，不用数组序号或模糊车牌补造车辆身份。`licence`缺失时保持空字符串，`busId`不自动等同于车牌。

默认输出目录：

```text
data/
  state.sqlite3              # 访客、共享限速/预算、观察记录、方向覆盖记录
  catalogue_083.json         # 线路方向目录，24小时缓存
  line_decoded.json          # 最近一次单线路解密结果
  YYYY-MM-DD/
    vehicle_list.json        # 当日全部车辆，同一city_id+bus_id合并
    vehicle_list.csv
    coverage.json            # 各方向成功/失败、原始数量、缺失ID诊断
```

同一车辆跨方向出现时，主列表只保留一项，在 `line_ids` 中保留关联方向。日期统一采用UTC+8，每个日期独立累计。数据库主键保留日期、城市、方向与车辆ID；重启后继续累积，重复采集不增加重复条目。默认不会跳过上轮成功方向，因为下一轮需要发现新车辆。

```json
{
  "day": "YYYY-MM-DD",
  "city_id": "083",
  "bus_id": "保留前导零的字符串",
  "licence": "",
  "line_ids": ["线路方向ID"],
  "first_seen": "首次观察时间",
  "last_seen": "最后观察时间"
}
```

JSON/SQLite中的ID保持字符串。Excel可能将CSV中的数字样式ID转换为数值，导入CSV时应将ID列设置为文本。

### “未知车号”与平台标识的区别

App同样显示“未知车号”，底层情况可能不同，不能根据页面显示一概判断：

| 情形 | 接口与页面的关系 | 本工具可以得到什么 |
|---|---|---|
| 仅展示层不显示车号 | 接口可能仍返回可识别的车号，而App隐藏或不展示；韶关公交属于这一调查线索 | 可保存接口实际返回的标识；是否是真实车号仍应结合车辆或运营企业资料核对 |
| 接口只返回不透明标识 | 真实车号可能在运营企业或数据供应链中已被转换，平台仅得到转换后的ID；北京345快的返回值表现为加密后的车号 | 可按平台busId累计观察记录，真实车号还需进一步解析 |

北京样本中返回过的长数字ID不能直接视为车号，如需最终统计真实车号，还需在具有合法访问权限的前提下，核实当地运营企业／数据供应方的字段定义、标识生成机制或公开对应表；若属于带密钥加密，需要相应授权解密资料；若为不可逆哈希或随机映射，通常不能直接通过“解密”得到车号。当前项目不包含这部分映射或破解能力，也不把不同日期／线路出现的不同标识强行合并为同一实体车辆。

`--save-responses` 可在扫描时保留每个方向最近一次成功的解密结果；默认不保存这些详细位置数据。程序不需要抓包手机长期在线。观察记录只能证明“采集时见过”，无法保证完整覆盖短时出勤、无信号、平台隐藏或轮询间隔内上下线的车辆。

## 频率、失败与排查

- `--interval` 是同一输出目录内所有线程／进程共享的最小请求起始间隔；默认1秒。它不是每个线程分别1秒。
- `--workers` 默认1，上限16；并发上限不等于请求速率。无需将16作为必须达到的实际并发。
- `--daily-budget` 为本地UTC+8当日总请求预算，默认1200，目录请求和失败请求也计入。连续采集必须结合线路方向数、轮数设定预算。
- `--period` 是相邻扫描轮次起始的最小间隔；若一轮耗时已经超过它，下一轮完成后即可开始。它不会中断上一轮。
- HTTP 401/403/429立即停止，不自动切渠道或换身份重试。预算用完也停止。已提交的车辆记录保留，`export`可重新导出。
- 一般线路错误记录到覆盖表后继续其它方向；不会自动无限重试。目录错误直接停止。失败方向可稍后用 `line` 复核或下一轮重试。
- 不同 `--out` 目录拥有独立状态文件；**它们不共享限速**。使用同一上游时应统一调度，不能用多目录或多进程绕过总速率。
- 空数组首先检查城市/方向ID、服务时段、站点范围和完整协议配置。解密成功不代表一定有车辆，也不能靠更换密钥让空数组产生车辆。
- 示例配置确认的是兼容性；不保证某个渠道永久有效或覆盖所有城市。不要把版本号随意升级成App显示版本。

## 文件与验证

```text
chelaile.py                  # 独立客户端、解密、采集、持久化、导出
h5_business.example.json     # 贵阳3路H5业务参数示例
requirements.txt
tests/test_core.py           # 本地协议、签名、解密、去重测试
README.md
.gitignore
```

执行本地测试，不访问上游：

```bash
python -m unittest discover -s tests -v
```

本项目没有打包本地数据库、真实响应、个人身份字段或其它仓库的运行环境。上传GitHub时不要提交运行产生的 `data/`、抓包文件、Cookie或个人标识。尚未选择开源许可证；公开复用前请仓库维护者自行确定许可。

## 协议参考

- [Justintunsday/chelaile-api-server](https://github.com/Justintunsday/chelaile-api-server)
- [SuperL-2233/chelaile-API](https://github.com/SuperL-2233/chelaile-API)
- [cors1900/chelaile-vercel](https://github.com/cors1900/chelaile-vercel)
- [2790Cake/Chelaile_Notify](https://github.com/2790Cake/Chelaile_Notify)
- [官方CH5入口](https://web.chelaile.net.cn/ch5/index.html)

本文件说明复现所需的协议与程序使用方式，不将上述仓库数量等同于独立渠道数量。公开代码或可访问入口本身不代表数据提供方授予批量采集权限。
