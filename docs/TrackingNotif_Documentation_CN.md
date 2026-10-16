# ITU 空间业务部 — Notice 追踪系统

使用与维护文档（中文）· 英文版：[TrackingNotif_Documentation_EN.md](TrackingNotif_Documentation_EN.md)

*最后更新：2026 年 10 月 16 日*

---

## 目录

1. [系统简介](#1-系统简介)
2. [系统架构](#2-系统架构)
3. [数据来源与合并规则](#3-数据来源与合并规则)
4. [数据刷新与缓存](#4-数据刷新与缓存)
5. [首次使用设置（每个浏览器一次）](#5-首次使用设置每个浏览器一次)
6. [用户操作指南](#6-用户操作指南)
7. [Notice 详情弹窗与 SRS 统计](#7-notice-详情弹窗与-srs-统计)
8. [API 接口说明](#8-api-接口说明)
9. [代码目录结构](#9-代码目录结构)
10. [运维指南](#10-运维指南)
11. [常见问题排查](#11-常见问题排查)
12. [已知问题与后续工作](#12-已知问题与后续工作)
13. [更新记录](#13-更新记录)

---

## 1. 系统简介

**Notice 追踪系统**是 ITU 空间业务部的内部 Web 应用，把空间电台 notice 汇总到一张可搜索、可筛选、可导出的表格中。数据来自三个内部数据库：

| 来源 | 数据库 | 提供的内容 |
|------|--------|-----------|
| **SNTrack** | MS Access `sntrdat.mdb` | BR 追踪数据（BRReg 日期、ADM、CIRC/pub、remarks 等） |
| **e-submission** | SQL Server `SpaceNetworkSystem`（`sydney.itu.int`） | 提交数据（SatName、DateOfReceive、TypeOfSubmission、Status 等） |
| **SRS** | MS Access `srs_all.mdb` | ESIM 决议标记（Res156/169/123）以及行详情弹窗中的协调信息 |

所有数据库均以**只读**方式打开，系统不会写入任何数据库。

| 项目 | 值 |
|------|----|
| 用户访问地址 | `https://intweb.itu.int/ITU-R/space/css/notification/index_api.html` |
| 后端服务器 | `156.106.168.185`，HTTPS 端口 `5001` |
| 前端文件（IIS 共享目录） | `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\` |
| Git 仓库 | `https://github.com/Infinite-Ng/TrackingNotif.git`（分支 `main`） |

---

## 2. 系统架构

```
 浏览器
   │  1. 页面 + 缓存数据              https://intweb.itu.int/.../notification/
   │◄───────────────────────────────  IIS（静态文件）
   │                                    ├── index_api.html
   │                                    └── data/cached_data.json  ◄──┐ 每次刷新
   │                                                                  │ 后写入
   │  2. /api/refresh、/api/data、    https://156.106.168.185:5001    │
   │     /api/details/<ntc_id>        Flask 后端（backend/api.py）  ──┘
   └─────────────────────────────────►  ├── 内存缓存 + 后台刷新线程
                                        ├── SNTrack（sntrdat.mdb，本地副本）
                                        ├── e-submission（SQL Server）
                                        └── SRS（srs_all.mdb）
```

**用户打开页面时的流程：**

1. 页面先从 IIS 读取 `data/cached_data.json`，立即显示表格。
2. 随后自动发起一次刷新，效果与点击 **Refresh** 相同（见[第 4 节](#4-数据刷新与缓存)）。
3. 后端重新查询完数据库后，表格原地更新。

**组成部分：**

| 组件 | 文件 | 作用 |
|------|------|------|
| 前端 | `frontend/index_api.html` | 单页应用（jQuery、DataTables、Bootstrap、flatpickr、SheetJS）。复制到 IIS 共享目录即完成部署。 |
| 后端 | `backend/api.py` | Flask API：查询并合并数据库、维护内存缓存、写入 `cached_data.json`。 |
| IIS 配置 | `web.config`（仅在共享目录中） | 含反向代理规则 `api/*` → `http://156.106.168.185:5000`。**目前不可用**，见[第 12 节](#12-已知问题与后续工作)。 |

后端也可以直接提供前端页面：`https://156.106.168.185:5001/`（来自 `frontend/` 目录）。此时页面使用自身地址调用 API；从其他地址打开时，使用写死的 `BACKEND_URL`（`https://156.106.168.185:5001`）。

---

## 3. 数据来源与合并规则

### 3.1 数据源与关联字段

| 来源 | 位置 | 使用的表 | 关联字段 |
|------|------|---------|---------|
| SNTrack | `\\blue\dfs\br\BR_DATA\SPACE\SNTRACK\sntrdat.mdb`（+ 工作组文件 `sntrapp.mdw`） | `tblSpaceStnNotif` | `ntc_id` |
| e-submission | SQL Server `sydney.itu.int` / `SpaceNetworkSystem` | `res908.Res908`；Status 来自 `dbo.Submissions` + `dbo.SubmissionsStatus`（经 `SubmissionId`） | `SnsNtcId` = `ntc_id` |
| SRS | `\\blue\dfs\br\BR_DATA\SPACE\SRS_DB\srs_all.mdb` | `notice`、`grp`、`e_srvcls`、`provn`、`tr_aff_ntw`、`coord_agree_ntw`、`all_aff_ntw`、`grp_aff_rec` | `ntc_id` |

每次刷新开始时，SNTrack 的 MDB 会先**复制到本地目录**（`api.py` 中的 `LOCAL_DATA_DIR`）再查询，避免网络文件被锁时阻塞后端。复制失败时使用上一次的本地副本。

### 3.2 各字段的来源

- **SNTrack：** BRREG、2d_date、ADM、PHASE、tgt_ntc_id、11.32A、11.41、subtoc、SUP、d_val_in、d_check_in、d_spr_out、d_complete、d_wmeeting、CIRC/pub、CIRC2/pub2、CIRC3/pub3、remarks、tex_remarks
- **e-submission：** DateOfReceive、SatName、long_nom、ntwk_org、act_code、TypeOfSubmission、Status、SNTrack ID（`sntrack_id`）、SubmissionId、InternalReference、DocumentumReference
- **SRS：** Res156 / Res169 / Res123（规则见 [ESIM_RES_Classification.md](ESIM_RES_Classification.md)），以及行详情弹窗中的 SRS 部分（[第 7 节](#7-notice-详情弹窗与-srs-统计)）。

BRREG（SNTrack）与 DateOfReceive（e-submission）含义相同，但在提交被退回等情况下可能不同。

### 3.3 合并规则（`api.py` 中的 `fetch_data()`）

按 `ntc_id` 做全外连接：

| 情况 | 规则 |
|------|------|
| notice 在 SNTrack 中（无论是否有 e-submission 记录） | **始终保留。** 若没有 e-submission 的 TypeOfSubmission，则标记为 `NotificationOfSpaceStation`。 |
| 只在 e-submission 中，且 TypeOfSubmission = `NotificationOfEarthStation` | **丢弃**（本系统只追踪空间电台）。 |
| 只在 e-submission 中，其他类型 | **仅当**有 `sntrack_id` **或** Status 非空时保留。 |
| 多条 e-submission 记录使用相同 `SnsNtcId`（如占位值 `1`） | 全部保留。 |

---

## 4. 数据刷新与缓存

### 4.1 何时刷新

| 触发条件 | 行为 |
|---------|------|
| **用户打开页面** | 先显示缓存数据，然后自动请求刷新。 |
| **用户点击 Refresh** | 请求刷新。 |
| **无用户操作** | 后端**每天**自动刷新一次（`REFRESH_INTERVAL_SECONDS`，默认 `86400`）。 |
| **后端启动** | 若缓存文件超过 5 分钟或不存在，立即刷新。 |

为保护数据库：页面上的数据不足 1 分钟时，页面**跳过**刷新；后端在刷新进行中收到的请求会被合并，最多再排队一次刷新。

### 4.2 刷新过程

1. 页面发送 `POST /api/refresh`。后端立即返回（`202`），并唤醒后台刷新线程。
2. 刷新线程同步 SNTrack MDB、查询三个数据库、合并数据并存入内存。一次完整刷新约 **20 秒**。
3. 同时，页面每 3 秒轮询一次 `GET /api/data`（最多 20 次）。一旦返回的 `timestamp` 在 1 分钟以内，表格即更新，并显示 **"Synced: dd.mm.yyyy HH:MM:SS"**。
4. 若 `POST /api/refresh` 失败（后端未运行或证书未被信任），页面**立即停止**，保留缓存数据，并在状态指示中显示 *"Server not reachable – showing cached data"*（Dev Mode 下可见）。

`/api/data` 从不直接查询数据库，总是返回内存缓存，因此即使数据库很慢也能立即响应。

### 4.3 缓存文件

每次刷新成功后，后端把同一份 JSON 写到两个位置：

| 文件 | 使用者 |
|------|--------|
| `TrackingNotif/frontend/data/cached_data.json` | 后端启动时载入内存；Flask 提供的页面 |
| `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\data\cached_data.json` | IIS 上的页面（打开时首先读取） |

```json
{ "generated_at": "2026-10-16T17:17:08.792043+00:00", "data": [ ... ] }
```

文件采用**原子写入**：先写入 `cached_data.json.tmp`，再用 `os.replace()` 替换。因此浏览器在刷新期间读取文件时，总能拿到完整文件。（2026 年 10 月之前是直接覆盖原文件，页面可能读到写了一半的文件而显示无数据。）

刷新失败时，内存和文件中保留上一次的数据，`/api/data` 返回 `"stale": true`。

---

## 5. 首次使用设置（每个浏览器一次）

后端使用**自签名证书**。在浏览器信任该证书之前，页面仍会显示缓存数据，但 **Refresh** 和弹窗中的 SRS 部分无法连接后端。

1. 打开 `https://156.106.168.185:5001/api/health`。
2. 点击 **高级 → 继续访问 156.106.168.185（不安全）**（不同浏览器措辞略有不同）。
3. 页面应显示 `{"status": "healthy", "timestamp": "..."}`。
4. 回到追踪系统页面并刷新。

每个浏览器只需操作一次；证书重新生成后需要再操作一次（最近一次重新生成：**2026 年 10 月 16 日**）。开启 **Dev Mode** 时，若证书未被信任，页面顶部会显示带此链接的黄色提示条。

---

## 6. 用户操作指南

### 6.1 打开应用

在 Chrome 或 Edge 中打开 `https://intweb.itu.int/ITU-R/space/css/notification/index_api.html`。

**不要**在 Windows 资源管理器中直接双击 HTML 文件（`file://`）：浏览器会阻止读取 `cached_data.json`，表格将为空。

"Data Preview" 旁的文字表示数据的新旧：

| 文字 | 含义 |
|------|------|
| `Generated: <日期 时间> (N records)` | 来自缓存文件，生成于该时间 |
| `Synced: <日期 时间> (N records)` | 刚从后端获取的最新数据 |

### 6.2 筛选

**顶部筛选栏：**

| 控件 | 说明 |
|------|------|
| **Type of Submission**（芯片） | 多选。默认 *NotifSS* 和 *ResubSS*。点击框打开列表；点芯片上的 **×** 移除；右侧 **×** 清除全部。 |
| **Status**（芯片） | 多选，操作同上。默认全部状态。 |
| **BRReg 日期范围** | 起止日期选择器（dd.mm.yyyy），两端均包含。 |

**列筛选**（表头第二行）：文本列支持模糊匹配；日期列有起止日期选择器；ADM、TypeOfSubmission、Status、SUP 为多选列表；标记类列（11.32A、subtoc、Res156 等）为下拉框。顶部与表内的 TypeOfSubmission/Status 筛选保持同步。

### 6.3 搜索

**Search** 框在所有可见列中搜索。日期两种写法都可以，例如 `23.02.2025` 或 `2025-02-23`。

### 6.4 刷新

点击 **Refresh** 获取最新数据（约 20 秒），新数据到达前按钮显示 *Refreshing…*。若数据不足 1 分钟则不执行。

### 6.5 重置

**Reset** 恢复全部筛选：TypeOfSubmission → NotifSS + ResubSS；其他 → 清空 / "All"。

### 6.6 导出

**Export** 将当前**筛选后的行和可见列**下载为 `notice_report_YYYYMMDD.xlsx`。日期列是真正的 Excel 日期，格式为 `dd.mm.yyyy`，在 Excel 中仍可按日期排序和筛选。

### 6.7 列显示

用顶部的列芯片显示/隐藏列，然后点击 **Apply**。设置保存在浏览器（`localStorage`）中。旁边的 **Reset** 恢复默认列。

### 6.8 日期格式

所有日期（表格、弹窗、日期选择器、导出）均显示为 **dd.mm.yyyy**（如 `07.10.2026`）。内部排序和筛选使用 ISO 日期（`yyyy-mm-dd`）。

### 6.9 行详情

点击任意一行，打开 notice 详情弹窗（[第 7 节](#7-notice-详情弹窗与-srs-统计)）。

### 6.10 Dev Mode

右上角 **Dev Mode** 开关显示 API 状态指示，并在需要时显示证书提示条。设置按浏览器保存。

### 6.11 TypeOfSubmission 简称对照

| 简称 | 全称 |
|------|------|
| NotifSS | NotificationOfSpaceStation（空间电台通知） |
| ResubSS | Resubmission（重新提交） |
| API | APINotSubjectToCoordination（无需协调的 API） |
| CR | CoordinationRequest（协调请求） |
| API-Info | AdvancePublicationInformation（预先公布信息） |
| DDI | DueDiligenceInformation（尽职调查信息） |
| Res49 | NotificationUnderResolution49（第 49 号决议下的通知） |
| Sup | Suppression（删除） |
| Mod | Modification（修改） |

---

## 7. Notice 详情弹窗与 SRS 统计

点击一行后弹出详情窗口，按数据来源分为三组：

| 分组 | 内容 | 来源 |
|------|------|------|
| SNTrack | 该行的全部 SNTrack 字段 | 表格数据中已有 |
| e-submission | 该行的全部 e-submission 字段 | 表格数据中已有 |
| SRS | Res156/169/123、`prov`、`ntf_occurs`、`prov_desc`、`provn` 与 `tr_aff_ntw` 的协调统计、`coord_agree_ntw` 与 `all_aff_ntw` 的行数 | 实时查询：`GET /api/details/<ntc_id>` |

空值照常显示字段名、值留空。notice 尚不在 SRS 中（通常是新提交）时显示 *"Notice not found in SRS database."*

### 7.1 SRS 表与 notice 的关联方式

| 表 | 关联方式 |
|----|---------|
| `notice` | `ntc_id` |
| `provn` | `grp_id` → `grp.ntc_id`（provn 没有 `ntc_id` 列） |
| `tr_aff_ntw` | `ntc_id` |
| `coord_agree_ntw` | `ntc_id` |
| `all_aff_ntw` | `aff_rec_id` → `grp_aff_rec.grp_id` → `grp.ntc_id`（**规则待确认**） |

### 7.2 统计规则

依据 Wu 2026-02-24 邮件 "The coord_st statistics result"。`provn` 和 `tr_aff_ntw` 规则相同。主管部门按 `adm` **去重**（同一 adm 出现多行只算 1 个），`adm` 为空的行不计入。

| 弹窗字段 | 含义 | 对应参考 Excel 列 |
|---------|------|------------------|
| agree_st | 每种符号下不同 adm 的数量及列表 | O 符号 = J `agree_st_o_count` / L `agree_st_o_adm_list` |
| coord_st | 每种符号下不同 adm 的数量及列表 | O 符号 = K `coord_st_o_count` / O `coord_st_o_adm_list` |
| coord_st details | 先按 coord_st 符号、再按 agree_st 值分组的 adm 列表 | O 符号 = N `coord_st_o_details` |
| coord_prov | coord_prov 去重后的集合 | provn = M `coord_prov_values`；tr_aff_ntw = Q `tr_aff_ntw_coord_prov` |
| has data（仅 tr_aff_ntw） | 该 notice 在表中是否有记录 | P `info in tr_aff_ntw_data` |
| coord_agree_ntw | 行数（该表无主键，不去重） | — |
| all_aff_ntw | 不同 `aff_rec_id` 的个数 | — |

`tr_aff_ntw` 在 coordination wizard 处理后会被清空，大部分 notice 在该表中没有数据，属于正常情况。

### 7.3 与参考 Excel 比对

`tools/compare_coord_stats.py` 按 notice 逐条比对参考 Excel 的 J–Q 列（只读）：

```bat
tracking_env_32\python.exe tools\compare_coord_stats.py coord_st_statistics_part1.xlsx result.csv
tracking_env_32\python.exe tools\compare_coord_stats.py coord_st_statistics_part1.xlsx result.csv --source-mdb
```

- 不加参数：查询 `srs_all.mdb`（弹窗实际使用的数据）。
- `--source-mdb`：改为读取 Excel `mdb_file` 列中各主管部门提交的 MDB（即 Excel 本身的数据来源），用于单独验证统计逻辑。
- 两者存在差异属于正常：BR 处理会改变数据（例如提交时 coord_st = 'O' 的 adm，在 SRS 中变为 agree_st = 'O'；coord_prov 前缀 `X/` 变为 `11.41/`；tr_aff_ntw 被清空）。

---

## 8. API 接口说明

基础地址：`https://156.106.168.185:5001`

| 方法 | 路径 | 限流 | 说明 |
|------|------|:---:|------|
| GET | `/api/data` | ✓ | 返回内存缓存：`{success, count, timestamp, stale, data}`。首次数据载入前返回 `503`。 |
| POST / GET | `/api/refresh` | ✓ | 安排一次后台刷新，立即返回 `202` 及 `current_timestamp`。 |
| GET | `/api/details/<ntc_id>` | ✓ | 单个 notice 的 SRS 字段（弹窗用）。`ntc_id` 非数字返回 `400`；`srs_all.mdb` 不可达返回 `503`；查询出错返回 `500`。 |
| GET | `/api/health` | — | `{"status": "healthy", "timestamp": ...}` |
| GET | `/`、`/index_api.html`、`/<文件>` | — | 提供 `frontend/` 目录中的文件。 |

**限流：** 每个客户端 IP 每 60 秒最多 30 次请求（内存计数），所有限流接口共用。超出返回 `429`。打开页面触发的一次刷新约使用 1 + 7 次请求。

**CORS：** 允许的来源为后端自身（`http(s)://156.106.168.185:5001`、`localhost:5001`、`127.0.0.1:5001`）、`http(s)://intweb.itu.int` 以及 `null`（`file://`）。可通过 `FRONTEND_ORIGIN`（逗号分隔）添加。

---

## 9. 代码目录结构

```
TrackingNotif/
├── backend/
│   ├── api.py                 Flask 后端（全部服务端逻辑）
│   ├── start_api.bat          启动脚本（管理员权限、防火墙规则、M: 盘、python api.py）
│   ├── generate_cert.py       生成 cert.pem / key.pem（自签名，10 年）
│   ├── requirements.txt       Python 依赖
│   ├── .flaskenv.example      .flaskenv 模板（配置与凭据）
│   └── .flaskenv、*.pem、*.log   仅本地，已被 git 忽略
├── frontend/
│   ├── index_api.html         主页面（部署到 IIS 时同名）
│   ├── index_zhiruo.html      主页面的开发副本（内容相同）
│   ├── index.html、index_tracking.html   旧版页面，读取静态 data/data.json
│   └── data/                  cached_data.json（git 忽略）、data.json（旧版）
├── tools/compare_coord_stats.py   SRS 统计与参考 Excel 比对
├── dataTransfer/、auto_update_data.bat   旧版静态 JSON 导出（index_api.html 不使用）
└── docs/                      本文档、ESIM_RES_Classification.md
```

IIS 共享目录中的 `index_api_.html` 是 2026 年 10 月改动**之前**的页面备份（美式日期格式、无弹窗）。

---

## 10. 运维指南

### 10.1 配置（`backend/.flaskenv`）

将 `backend/.flaskenv.example` 复制为 `backend/.flaskenv` 并填写。`.flaskenv` 已被 git 忽略，切勿提交。**密码没有默认值**——缺失时后端仍能启动，但会记录警告，相关数据库连接会失败。

| 变量 | 必填 | 默认值 | 说明 |
|------|:---:|--------|------|
| `SQL_SERVER_PASSWORD` | ✓ | — | SQL Server 密码 |
| `MDB_PASSWORD` | ✓ | — | SNTrack MDB 密码 |
| `SQL_SERVER_HOST` | | `sydney.itu.int` | SQL Server 主机 |
| `SQL_SERVER_DB` | | `SpaceNetworkSystem` | SQL Server 数据库 |
| `SQL_SERVER_USER` | | `sns_a` | SQL Server 用户 |
| `MDB_USER` | | `spruser01` | SNTrack MDB 用户 |
| `SRS_MDB_PATH` | | `\\blue\dfs\br\BR_DATA\SPACE\SRS_DB\srs_all.mdb` | SRS 数据库 |
| `REFRESH_INTERVAL_SECONDS` | | `86400`（1 天） | 自动刷新间隔 |
| `FRONTEND_ORIGIN` | | — | 额外的 CORS 来源，逗号分隔 |

`api.py` 中仍写死的路径：`LOCAL_DATA_DIR`（MDB 本地副本）、网络 MDB/MDW 路径、`_write_cache_files()` 中的 IIS 缓存路径。

### 10.2 Python 环境与依赖

- 使用已安装 `backend/requirements.txt`（`flask`、`flask-cors`、`pyodbc`、`python-dotenv`、`waitress`）的 Python 环境。当前服务器上为 conda 环境 `C:\Users\xianwu\Anaconda3\envs\codingSpace`。
- `generate_cert.py` 还需要 `cryptography`，它**不在** `requirements.txt` 中，上述环境也没有；Anaconda 基础环境（`C:\Users\xianwu\Anaconda3\python.exe`）中有。
- 需安装 **Microsoft Access Database Engine**（ODBC 驱动），位数（32/64 位）须与 Python 一致。

### 10.3 启动后端

后端**必须以 HTTPS 方式运行在 5001 端口**，因为页面调用的是 `https://156.106.168.185:5001`。

```bat
cd C:\Users\xianwu\Desktop\codingSpace\TrackingNotif\backend
C:\Users\xianwu\Anaconda3\envs\codingSpace\python.exe api.py
```

`cert.pem` 和 `key.pem` 都存在且有效时，`python api.py` 自动启用 HTTPS。`start_api.bat` 效果相同，另外还会提升为管理员、添加 5001 端口防火墙规则、结束占用 5001 端口的进程并映射 `M:` 盘；注意它调用的是 `python`，需确认指向正确的环境。

> ⚠️ **不要**用 `waitress --listen=0.0.0.0:5001` 启动。waitress 只提供 HTTP，页面的 HTTPS 请求会一直挂起、到不了后端。waitress 只适合放在 HTTPS 反向代理之后使用（见[第 12 节](#12-已知问题与后续工作)）。

后台运行并写日志（自 2026-10-16 起服务器上即为此方式）：

```powershell
$b = 'C:\Users\xianwu\Desktop\codingSpace\TrackingNotif\backend'
Start-Process -FilePath 'C:\Users\xianwu\Anaconda3\envs\codingSpace\python.exe' -ArgumentList 'api.py' `
  -WorkingDirectory $b -WindowStyle Hidden `
  -RedirectStandardOutput "$b\api_server.out.log" -RedirectStandardError "$b\api_server.log"
```

后台进程在机器重启或用户注销后会停止，重启后需重新启动。

### 10.4 检查与停止后端

```powershell
# 是否运行、是否为 HTTPS（-k = 接受自签名证书）
curl.exe -k https://156.106.168.185:5001/api/health

# 哪个进程占用 5001 端口、以什么命令启动
netstat -ano | findstr :5001
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" | Select-Object CommandLine

# 停止
taskkill /PID <PID> /F
```

日志（`backend\api_server.log`）记录每个请求和每次刷新，例如 `POST /api/refresh … 202`、`Background refresh complete: 21023 records`。

### 10.5 SSL 证书

文件：`backend/cert.pem` 和 `backend/key.pem`（git 忽略）。证书覆盖 `156.106.168.185`、`localhost`、`127.0.0.1`，有效期 10 年。`key.pem` 未加密，请限制文件权限。

```bat
cd C:\Users\xianwu\Desktop\codingSpace\TrackingNotif\backend
C:\Users\xianwu\Anaconda3\python.exe generate_cert.py
```

然后重启后端。**重新生成后，所有用户都需重新信任证书**（[第 5 节](#5-首次使用设置每个浏览器一次)）。

### 10.6 部署前端改动

1. 在仓库中修改并测试 `frontend/index_api.html`。
2. 先备份共享目录中的线上文件，再用新文件覆盖：
   `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\index_api.html`
3. 请用户刷新页面（如浏览器缓存了旧版，按 Ctrl+F5）。
4. 将同一文件提交到 Git，使仓库与线上一致。

后端改动（`api.py`）需重启后端才生效。

### 10.7 更换服务器 IP

1. 为新 IP 重新生成证书（先修改 `generate_cert.py` 中的 IP）。
2. 在 `frontend/index_api.html` 中修改 `const BACKEND_URL = 'https://<新IP>:5001';` 以及证书提示条中的链接。
3. 修改 `api.py` 中 `_ALLOWED_ORIGINS` 的来源；若使用代理，同时修改 `web.config` 中的目标地址。
4. 重启后端、重新部署页面，并请用户信任新证书。

---

## 11. 常见问题排查

| 现象 | 可能原因 | 解决方法 |
|------|---------|---------|
| 表格为空 | 从资源管理器直接打开了页面（`file://`） | 使用 `https://intweb.itu.int/...` 地址 |
| 表格为空（状态为 "No cached data available"） | `cached_data.json` 缺失或无法读取 | 检查共享目录中的文件，执行一次刷新。（采用原子写入后不会再出现写了一半的文件。） |
| 点击 Refresh 无反应，后端日志没有请求 | 后端未运行，或以**纯 HTTP** 方式运行 | `curl.exe -k https://156.106.168.185:5001/api/health`；失败则检查启动方式（[10.3](#103-启动后端)） |
| 显示 "Server not reachable – showing cached data" | 后端未运行，或该浏览器未信任证书 | 检查后端；按[第 5 节](#5-首次使用设置每个浏览器一次)操作 |
| 后端启动即报错 `ssl.SSLError: [SSL] PEM lib` | `cert.pem` / `key.pem` 为空或无效 | 重新生成证书（[10.5](#105-ssl-证书)） |
| 弹窗显示 "Could not load SRS data" | 后端不可达，或 `srs_all.mdb` 不可达 | 检查后端和 `\\blue` 共享 |
| 刷新显示 "Refresh may be incomplete" / "Refresh timed out" | 刷新时间超过轮询窗口（约 70 秒） | 查看后端日志中数据库连接是否缓慢或失败 |
| `429 Too Many Requests` | 触发限流（每 IP 每分钟 30 次） | 等待一分钟 |
| 刷新后数据没有变化 | 某个数据库连接失败，保留了旧数据（`stale: true`） | 检查 `.flaskenv` 中的凭据和日志 |
| 5001 端口被占用 | 旧进程仍在运行 | 结束该进程（[10.4](#104-检查与停止后端)） |
| 其他电脑只能看到缓存数据，后端正常 | CORS 来源未被允许 | 通过 `FRONTEND_ORIGIN` 添加来源 |

---

## 12. 已知问题与后续工作

- **自签名证书带来的不便。** 每个用户都要在每个浏览器中信任一次证书。更好的做法是让 API 经过 IIS 转发，页面只访问自身站点。`web.config` 已有规则 `api/*` → `http://156.106.168.185:5000`，但目前返回 **IIS 错误 500.50**（URL Rewrite，访问被拒绝），最可能的原因是规则设置了 `HTTP_X_FORWARDED_HOST`，而该服务器变量不在服务器允许列表中。修复需要 IIS 管理员（或删除该 `<serverVariables>` 段），然后用 waitress 以 HTTP 方式在 5000 端口运行后端，并把 `BACKEND_URL` 改为相对路径 `api/`。
- **后端不是 Windows 服务。** 重启或注销后会停止。可考虑开机计划任务或服务包装。
- **待确认规则：** 弹窗中 `all_aff_ntw` 的关联方式（[7.1](#71-srs-表与-notice-的关联方式)）。
- **无用代码：** `index_api.html` 中的 `loadDataFromAPI()` 从未被调用（其中还引用了未定义的 `filteredData`）。
- **重复页面：** `index_api.html` 与 `index_zhiruo.html` 内容相同，需保持同步或删除副本。
- **写死的路径**（本地数据目录、IIS 缓存路径）使服务器迁移更困难。
- `requirements.txt` 缺少 `cryptography`（`generate_cert.py` 需要）。

---

## 13. 更新记录

### 2026 年 10 月

- **Notice 详情弹窗**（xuzhiruo，PR #1）：点击行查看按来源分组的全部字段，并通过新接口 `/api/details/<ntc_id>` 实时获取 SRS 数据。新增工具 `tools/compare_coord_stats.py`。
- **日期统一为 dd.mm.yyyy**（表格、弹窗、日期选择器、时间戳）；Excel 导出写入真正的日期。
- **刷新策略：** 用户打开页面或点击 Refresh 时刷新；否则每天一次（原为每 10 分钟）。
- **原子写入缓存：** `cached_data.json` 先写临时文件再替换，修复了页面在刷新期间加载时表格为空的问题。
- **快速失败：** 后端不可达时页面立即停止并保留缓存数据，不再轮询约 4 分钟。
- **恢复 HTTPS：** `cert.pem` / `key.pem` 自 2026 年 6 月起为空，且后端以纯 HTTP 方式启动，导致刷新请求从未到达后端。已于 2026-10-16 重新生成证书，并用 `python api.py` 重启后端。

### 2026 年早些时候

- 显示仅存在于 SNTrack 的空间电台 notice；排除地球站提交。
- 保留共用占位 `SnsNtcId` 的所有 e-submission 记录。
- 内存缓存 + 后台刷新线程；启动时载入磁盘缓存。
- 基于 SRS 的 ESIM 决议分类（Res156/169/123）。
- 凭据改为环境变量；CORS 限制；接口限流。
