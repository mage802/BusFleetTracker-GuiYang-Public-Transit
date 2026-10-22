# BusFleetTracker — Guiyang Public Transit

**贵阳市公交车辆实时出勤采集与历史数据研究项目**

A Python-based toolkit for collecting and analyzing public transit vehicle attendance data in Guiyang, China.

---

## 项目简介 | Overview

**BusFleetTracker** 是一个面向贵阳市公共交通系统的公交车辆出勤数据采集与分析项目。

本项目旨在通过自动化采集公开交通信息平台提供的实时公交数据，建立按日期、线路及车辆编号组织的历史出勤记录，为公交车辆运营情况分析、车辆调度规律研究及公交车队档案整理提供数据基础。

目前，本仓库主要包含针对 **车来了（Chelaile）** 和 **掌上公交（Mygolbs）** 两个公交信息平台开发的独立 Python 数据采集工具。

项目仍处于持续开发阶段，未来希望将多来源数据统一整理至公交历史数据库，并进一步实现基于 Web 的交互式车辆档案检索与运营记录分析。

## 主要功能 | Features

### 1. 实时公交信息采集

- 查询目标城市的公交线路及运营方向目录。
- 获取接口提供的实时车辆标识及对应线路信息。
- 支持指定线路查询与多线路批量采集。
- 支持按运营公司、线路等条件筛选采集对象（具体能力依采集模块而异）。
- 支持多轮采集，以提高当日车辆出勤记录的覆盖率。

### 2. 车辆出勤记录整理

- 根据车辆标识识别和去重重复记录。
- 按日期、线路及运营方向组织车辆数据。
- 保存车辆首次及最后一次被观测的时间。
- 累计每日已观测车辆，减少重复采集带来的冗余。
- 区分有效空结果、接口异常与采集失败。

### 3. 数据持久化与导出

- 使用 SQLite 保存本地采集记录及任务状态。
- 支持 CSV 数据导出，便于后续统计分析。
- 车来了模块支持 JSON 格式导出。
- 支持从数据库重新导出历史采集结果。
- 保留车辆编号的字符串格式，避免前导零丢失。

### 4. 自动化与采集管理

- 支持持续轮询及多轮定时采集。
- 支持控制请求速率及并发数量。
- 支持中断后的数据保留。
- 掌上公交模块支持采集任务断点续跑与失败补采。
- 为后续数据库集成及历史运营分析提供基础数据。

> **数据说明：** 项目中的“出勤”是指车辆在指定日期内曾被数据源观测到，并不代表该车辆的完整运营时长、真实发车班次数或完整运营轨迹。未被观测到的车辆也不能直接认定为当日未出勤。

## 数据来源 | Data Sources

| 数据来源         | 主要用途                  | 当前实现          |
| ------------ | --------------------- | ------------- |
| 车来了 Chelaile | 线路方向查询、实时车辆采集、每日车辆名册  | 独立 Python 采集器 |
| 掌上公交 Mygolbs | 线路目录、运营公司筛选、实时车辆与出勤统计 | 独立 Python 采集器 |

两个模块基于不同的上游数据接口，分别运行并维护各自的本地数据。

**目前仓库并未实现两个来源之间的统一车辆身份映射或跨平台自动合并。** 不同平台返回的车辆标识不一定具有相同含义。

## 项目结构 | Repository Structure

```text
BusFleetTracker-GuiYang-Public-Transit/
│
├── chelaile-attendance/
│   ├── chelaile.py
│   ├── import_history.py
│   ├── operation_records.py
│   ├── h5_business.example.json
│   ├── requirements.txt
│   ├── tests/
│   └── README.md
│
├── guiyang-mygolbs-collector/
│   ├── bus.py
│   ├── mygolbs/
│   │   ├── api.py
│   │   ├── collector.py
│   │   └── storage.py
│   ├── config.example.json
│   ├── requirements.txt
│   ├── tests/
│   ├── LICENSE
│   └── README.md
│
└── README.md
```

### 子项目介绍

**Chelaile Attendance**

基于车来了平台的实时公交数据采集工具，提供城市及线路查询、车辆数据解析、全市逐方向扫描、SQLite 持久化和数据导出功能。

[查看详细文档](./chelaile-attendance/README.md)

**Guiyang Mygolbs Collector**

基于掌上公交平台的实时车辆出勤采集工具，支持运营公司筛选、线路批量查询、受控并发、多轮采集、失败补采及断点续跑。

[查看详细文档](./guiyang-mygolbs-collector/README.md)

## 快速开始 | Quick Start

### 运行环境

- Python 3.10 或更高版本
- Windows / Linux / macOS
- 能够访问对应公交信息服务的网络环境

### 1. 克隆仓库

```bash
git clone https://github.com/mage802/BusFleetTracker-GuiYang-Public-Transit.git

cd BusFleetTracker-GuiYang-Public-Transit
```

### 2. 使用车来了采集模块

```bash
cd chelaile-attendance

python -m pip install -r requirements.txt

# 查询贵阳市线路方向目录
python chelaile.py catalogue --city 083

# 查询指定线路方向
python chelaile.py line --city 083 --line-id 851259873106

# 执行一轮全市车辆扫描
python chelaile.py scan --city 083
```

采集结果默认保存至当前模块的 `data/` 目录。

### 3. 使用掌上公交采集模块

从仓库根目录进入：

```bash
cd guiyang-mygolbs-collector

# 查询贵阳3路实时车辆
python bus.py query --cmd 104 --param LINENAME=3路 --param DIRECTION=1

# 采集指定线路
python bus.py collect --line 3路

# 按默认运营公司条件采集
python bus.py collect
```

此模块基于 Python 标准库，无须额外安装第三方 Python 依赖。

详细参数、接口配置、运行限制及异常处理方式请参考各模块独立 README。

## 数据记录与应用 | Data Applications

项目主要关注公交车辆的**历史出勤情况（Historical Vehicle Attendance）**。

典型研究需求包括：

| 查询维度 | 研究目标              |
| ---- | ----------------- |
| 车辆编号 | 查询指定车辆在不同日期出现过的线路 |
| 公交线路 | 统计指定日期某条线路已观测到的车辆 |
| 历史日期 | 汇总特定日期内采集到的公交车辆   |
| 运营方向 | 分析车辆在不同线路方向的出现情况  |
| 观测时间 | 研究车辆被采集系统观测到的时间范围 |

当前各采集模块已实现部分数据记录及导出功能。面向跨日期、多数据源的统一查询，以及完整班次与站点停靠历史重建，仍需要额外的数据库和分析逻辑支持。

## 后续开发方向 | Future Development

本仓库将作为公交历史数据平台的数据采集基础，后续探索以下功能：

- [x] 建立统一的车辆、线路及运营企业档案结构。
- [x] 整合不同数据源的车辆标识与线路信息。
- [x] 构建面向长期历史数据的集中式数据库。
- [ ] 开发基于 Web 的车辆出勤查询与可视化界面。
- [x] 提供按车辆编号、线路、日期等条件进行组合检索的能力。
- [ ] 在数据完整性允许的情况下，开展车辆班次、运行轨迹及站点停靠时间推断。

以上属于后续研究及集成方向，不代表相关功能已经在本仓库中完整实现。

## 使用说明与免责声明 | Disclaimer

本项目仅用于公共交通数据处理、技术学习与非商业性研究。

1. 本项目不是车来了、掌上公交或贵阳市公共交通运营企业的官方项目，与相关服务提供方不存在官方合作或授权关系。
2. 所涉及的数据接口可能随上游平台调整而发生变化，不能保证长期可用。
3. 公开网页或接口可访问，不等于获得批量采集或再分发数据的授权。使用者应自行遵守适用的法律法规、服务条款、访问频率限制和数据授权要求。
4. 采集过程应采用合理请求频率，避免对上游服务造成不必要的负担。遇到访问拒绝或明确限流时应停止请求。
5. 车辆标识、实时位置及运营数据可能存在缺失、延迟或误差，不应直接用于实际运营调度或其他需要高可靠性的决策。
6. 不应将包含凭据、个人身份信息、原始敏感数据或本地运行环境配置的文件提交至公共仓库。

## 许可证 | License

`guiyang-mygolbs-collector` 子项目包含 MIT License。

其他目录及仓库整体的许可范围应以各自明确发布的许可证为准。第三方数据接口的访问权及数据再分发权不属于源代码许可证的授权范围。

## 作者 | Author

**GitHub:** [@mage802](https://github.com/mage802)

**Repository:** [BusFleetTracker-GuiYang-Public-Transit](https://github.com/mage802/BusFleetTracker-GuiYang-Public-Transit)

---

*BusFleetTracker — Recording vehicle attendance and exploring the operational history of Guiyang's public transit system.*
