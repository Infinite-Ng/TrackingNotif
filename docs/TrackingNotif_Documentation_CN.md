# ITU 空间业务部 — 无线电通知追踪系统

## 使用与维护文档（中文）

---

## 1. 系统简介

**无线电通知追踪系统**是 ITU 空间业务部的内部 Web 应用程序，用于查询、筛选和导出空间网络无线电通知记录。数据来源于两个内部数据库：

| 数据源 | 类型 | 连接信息 |
|--------|------|---------|
| `sntrdat.mdb` | MS Access（只读） | `M:\BR_DATA\SPACE\SNTRACK\sntrdat.mdb` |
| `SpaceNetworkSystem` | SQL Server | `sydney.itu.int` |

前端为单页 HTML/JavaScript 应用，由 Flask 后端通过 HTTPS 提供服务。

---

## 2. 系统架构

```
用户浏览器
    │
    │  HTTPS（端口 5001）
    ▼
Flask 后端（api.py）
    │  156.106.168.185:5001
    │
    ├── /api/data        → 返回通知记录（JSON）
    ├── /api/health      → 健康检查接口
    ├── /api/refresh     → 强制从数据库重新获取数据
    └── /                → 提供 index_api.html（前端页面）
         └── /data/cached_data.json  → 离线备份缓存
```

**前端访问方式（两种）：**

| 方式 | 地址 | 说明 |
|------|------|------|
| 通过 Flask 直接访问（推荐） | `https://156.106.168.185:5001/` | 由后端直接提供 |
| 通过 IIS 文件共享访问 | `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\index_api.html` | 静态 HTML 文件；JavaScript 自动向 `https://156.106.168.185:5001` 发起 API 请求 |

---

## 3. 首次使用设置（每台电脑/浏览器操作一次）

由于后端使用**自签名 SSL 证书**，浏览器在首次访问时会阻止 API 连接，需手动信任证书。

**操作步骤：**
1. 在浏览器中打开：`https://156.106.168.185:5001/api/health`
2. 点击 **高级 → 继续访问 156.106.168.185（不安全）**（不同浏览器措辞略有不同）。
3. 页面显示 `{"status": "ok"}` 则表示证书已被信任。
4. 返回追踪系统页面并刷新。

此操作每台电脑/浏览器只需执行一次，证书有效期为 **10 年**。

---

## 4. 用户操作指南

### 4.1 打开应用

- **推荐方式：** 在 Chrome 或 Edge 中访问 `https://156.106.168.185:5001/`
- **备选方式：** 在 Windows 资源管理器中打开 `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\index_api.html`

首次加载时，页面会从后端实时获取数据。若后端不可用，页面顶部会显示**黄色提示横幅**并自动加载离线备份缓存（`frontend/data/cached_data.json`），同时显示缓存生成时间（橙色横幅）。

### 4.2 数据筛选

**顶部主筛选栏：**

| 控件 | 说明 |
|------|------|
| **提交类型**（芯片选择器） | 多选筛选器。默认选中 *NotifSS* 和 *ResubSS*。点击选择框打开下拉列表；点击芯片上的 **×** 可单独移除；点击右侧 **×** 可清除全部选择。 |
| **状态（Status）** | 单选下拉框，按当前通知状态筛选。 |
| **BRReg 日期范围** | 对 BR 登记日期列进行区间筛选。 |

**表格内列筛选（表头第二行）：**
每列标题下方均有独立筛选控件。文本列支持模糊匹配；日期列提供日期选择器；TypeOfSubmission 和 Status 列提供多选复选框。

### 4.3 全局搜索

使用右上角 **Search** 输入框对所有可见列进行全文搜索。

### 4.4 刷新数据

点击 **Refresh（刷新）** 按钮，从后端数据库重新获取最新数据（同时查询 Access MDB 和 SQL Server）。数据加载期间显示加载遮罩。

### 4.5 重置筛选条件

点击 **Reset（重置）** 按钮，将所有筛选条件恢复为默认值：
- 提交类型 → NotifSS + ResubSS
- 其他筛选条件 → 清空 / 恢复为"全部"

### 4.6 导出数据

点击 **Export（导出）** 按钮，将当前**筛选后的可见行**导出为 Excel（`.xlsx`）文件。

### 4.7 列显示设置

页面顶部的列芯片按钮用于显示/隐藏各列。调整后点击 **Apply（应用）** 生效。列设置会保存在浏览器 `localStorage` 中，下次访问自动还原。点击旁边的 **Reset（重置）** 可清除已保存的设置并恢复默认列。

### 4.8 行详情弹窗（`index_zhiruo.html`）

点击表格中任意一行，弹出该 notice 的详情窗口，字段按**数据来源**分三组显示（见第 5A 节）。SRS 部分由后端接口 `/api/details/<ntc_id>` 实时查询。空值照常显示字段名、值留空；notice 不在 SRS 中时显示 "Notice not found in SRS database."（新提交、尚未处理的 notice 属于这种情况）。

---

## 5A. 字段来源与 SRS 统计规则

### 三个数据库

| 来源 | 位置 | 关联字段 |
|------|------|---------|
| SNTrack | MS Access `sntrdat.mdb`，表 `tblSpaceStnNotif` | `ntc_id` |
| e-submission | SQL Server `SpaceNetworkSystem`：`res908.Res908`；Status 来自 `dbo.Submissions` + `dbo.SubmissionsStatus`（经 `SubmissionId`） | `SnsNtcId` = `ntc_id` |
| SRS | MS Access `\\blue\dfs\br\BR_DATA\SPACE\SRS_DB\srs_all.mdb`（只读连接，仅执行 SELECT） | `ntc_id` |

- **SNTrack 字段：** BRREG、2d_date、ADM、PHASE、tgt_ntc_id、11.32A、11.41、subtoc、SUP、d_val_in、d_check_in、d_spr_out、d_complete、d_wmeeting、CIRC/pub、CIRC2/pub2、CIRC3/pub3、remarks、tex_remarks
- **e-submission 字段：** DateOfReceive、SatName、long_nom、ntwk_org、act_code、TypeOfSubmission、Status、SNTrack ID（sntrack_id）、SubmissionId、InternalReference、DocumentumReference
  - 只存在于 SNTrack、没有 e-submission 记录的行，TypeOfSubmission 由后端默认填为 NotificationOfSpaceStation。
- **SRS 字段：** Res156/169/123（由 `e_srvcls`、`grp`、`notice` 计算）以及弹窗中的 SRS 部分。
- BRREG（SNTrack）与 DateOfReceive（e-submission）含义相同，但在提交被退回等情况下可能不同。

### SRS 表的关联方式

| 表 | 关联到 notice 的方式 |
|----|---------------------|
| `notice` | `ntc_id` |
| `provn` | `grp_id` → `grp.ntc_id`（provn 没有 ntc_id 列） |
| `tr_aff_ntw` | `ntc_id` |
| `coord_agree_ntw` | `ntc_id` |
| `all_aff_ntw` | `aff_rec_id` → `grp_aff_rec.grp_id` → `grp.ntc_id`（待确认） |

### 统计规则（依据 Wu 2026-02-24 邮件 "The coord_st statistics result"）

`provn` 和 `tr_aff_ntw` 规则相同。凡是"数主管部门"，都按 `adm` **去重**（同一 adm 出现多行只算 1 个），`adm` 为空的行不计入。

| 弹窗字段 | 含义 | 对应 Excel 列 |
|---------|------|--------------|
| agree_st | 每种符号下的不同 adm 数量及列表 | O 符号 = J `agree_st_o_count` / L `agree_st_o_adm_list` |
| coord_st | 每种符号下的不同 adm 数量及列表 | O 符号 = K `coord_st_o_count` / O `coord_st_o_adm_list` |
| coord_st details | 按 coord_st 符号，再按 agree_st 值分组的 adm 列表 | O 符号 = N `coord_st_o_details` |
| coord_prov | coord_prov 去重后的集合 | provn = M `coord_prov_values`；tr_aff_ntw = Q `tr_aff_ntw_coord_prov` |
| has data（仅 tr_aff_ntw） | 该 notice 在表中是否有记录 | P `info in tr_aff_ntw_data` |
| coord_agree_ntw | 行数（该表无主键，不去重） | — |

`tr_aff_ntw` 经 coordination wizard 处理后会被清空，大部分 notice 没有数据属于正常情况。

### 与参考 Excel 比对

`tools/compare_coord_stats.py` 用 Excel 中的 ntc_id 逐条比对 J–Q 列（只读）：

```
tracking_env_32\python.exe tools\compare_coord_stats.py coord_st_statistics_part1.xlsx result.csv
tracking_env_32\python.exe tools\compare_coord_stats.py coord_st_statistics_part1.xlsx result.csv --source-mdb
```

- 不加参数：查询 `srs_all.mdb`（弹窗实际使用的数据）。
- `--source-mdb`：改为读取 Excel `mdb_file` 列中各主管部门提交的 MDB，即 Excel 本身的数据来源，用于单独验证统计逻辑。
- 两者会有差异：BR 处理后写入 SRS 的数据与提交的 MDB 不同（例如提交时 coord_st = 'O' 的 adm，在 SRS 中变成 agree_st = 'O'；coord_prov 前缀 `X/` 变为 `11.41/`；tr_aff_ntw 被清空）。

---

## 5. 提交类型（TypeOfSubmission）对照表

| 简称 | 全称 |
|------|------|
| NotifSS | NotificationOfSpaceStation（空间电台通知）|
| ResubSS | Resubmission（重新提交）|
| API | APINotSubjectToCoordination（无需协调的 API）|
| CR | CoordinationRequest（协调请求）|
| API-Info | AdvancePublicationInformation（预先公布信息）|
| DDI | DueDiligenceInformation（尽职调查信息）|
| Res49 | NotificationUnderResolution49（决议49下的通知）|
| Sup | Suppression（删除）|
| Mod | Modification（修改）|

---

## 6. 离线备份缓存

每次后端成功获取数据后，会自动将结果保存至：

```
TrackingNotif/frontend/data/cached_data.json
```

文件格式：
```json
{
  "generated_at": "2025-01-15T09:30:00.000000",
  "data": [ ... ]
}
```

当后端不可用时，前端自动从此文件加载数据，并在顶部显示橙色横幅提示缓存时间。缓存数据支持完整的筛选、搜索和导出功能。

---

## 7. 运维维护指南

### 7.1 启动后端服务

在服务器（`156.106.168.185`）上执行：

```bat
cd C:\path\to\TrackingNotif\backend
python api.py
```

或使用批处理脚本：

```bat
TrackingNotif\backend\start_api.bat
```

服务启动后监听 `https://0.0.0.0:5001`，对所有网络接口开放。

### 7.2 停止后端服务

在运行 `api.py` 的终端中按 `Ctrl+C`。

若后台进程仍在运行导致端口 5001 被占用：

```powershell
# 查找占用 5001 端口的进程 PID
netstat -ano | findstr :5001
# 强制结束进程
taskkill /PID <PID> /F
```

### 7.3 验证后端是否运行正常

```powershell
Invoke-WebRequest -Uri "https://156.106.168.185:5001/api/health" -SkipCertificateCheck
```

正常响应：`{"status": "ok"}`

### 7.4 SSL 证书管理

证书文件路径：

```
TrackingNotif/backend/cert.pem
TrackingNotif/backend/key.pem
```

证书覆盖范围：
- IP：`156.106.168.185`
- `localhost`、`127.0.0.1`
- 有效期：**10 年**

如需重新生成证书（证书到期或服务器 IP 变更后）：

```bat
cd TrackingNotif\backend
python generate_cert.py
```

重新生成后，所有用户需在浏览器中重新信任证书（参见第 3 节）。

### 7.5 更换服务器 IP

若服务器 IP 发生变化，需同步修改以下内容：

1. 重新生成 SSL 证书（见 7.4）。
2. 在 `TrackingNotif/frontend/index_api.html` 和 `TrackingNotif_edited/frontend/index_api.html` 中更新：
   ```javascript
   const BACKEND_URL = 'https://<新IP>:5001';
   ```
3. 更新 HTML 中证书信任横幅的链接：
   ```html
   <a href="https://<新IP>:5001/api/health" ...>
   ```

### 7.6 数据源配置

数据库连接参数位于 `TrackingNotif/backend/api.py`。**现在支持通过环境变量设置凭据**（推荐，更安全），未设置时使用代码中的默认值：

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `MDB_USER` | MS Access 用户名 | `spruser01` |
| `MDB_PASSWORD` | MS Access 密码 | `spruser01` |
| `SQL_SERVER_USER` | SQL Server 用户名 | `sns_a` |
| `SQL_SERVER_PASSWORD` | SQL Server 密码 | `sns_a_5678` |
| `SQL_SERVER_HOST` | SQL Server 主机名 | `sydney.itu.int` |
| `SQL_SERVER_DB` | SQL Server 数据库名 | `SpaceNetworkSystem` |

所有 MDB 连接均使用 `ReadOnly=1`，支持多用户同时访问数据库而不产生锁定。

### 7.7 CORS（跨域资源共享）配置

后端限制 CORS 为已知来源列表以提高安全性。当前端从不同来源访问时（如通过 IIS `https://intweb.itu.int`），浏览器会发送跨域请求，必须被后端允许。

**内置允许的来源：**
- `https://156.106.168.185:5001` / `http://156.106.168.185:5001`
- `https://localhost:5001` / `http://localhost:5001`
- `https://127.0.0.1:5001` / `http://127.0.0.1:5001`
- `https://intweb.itu.int` / `http://intweb.itu.int`
- `null`（用于 `file://` 协议访问）

**添加额外来源：** 设置 `FRONTEND_ORIGIN` 环境变量（逗号分隔多个 URL）：
```bat
set FRONTEND_ORIGIN=https://example.com,https://another.example.com
```

如果其他电脑访问时显示 "Offline – using backup data" 但后端实际正常运行，通常是 CORS 问题——请检查访问来源是否在允许列表中。

### 7.8 安全配置说明

**请求频率限制：** `/api/data` 接口限制为每个客户端 IP 每 60 秒最多 **30 次请求**，防止滥用。

**SSL 私钥：** `backend/key.pem` 文件未加密存储。Windows 上请将 NTFS 权限限制为仅管理员可读，Linux 上使用 `chmod 600 key.pem`。

**数据库凭据：** 参见 7.6 节——推荐使用环境变量，避免密码明文出现在源码中。

### 7.9 安装依赖

```bat
cd TrackingNotif\backend
pip install -r requirements.txt
```

主要依赖：`flask`、`pyodbc`、`cryptography`

此外还需安装 **Microsoft Access 数据库引擎 2016 可再发行组件**（64位版本）。

### 7.10 常见问题排查

| 现象 | 可能原因 | 解决方法 |
|------|---------|------------|
| 页面显示"Connection failed"横幅 | 后端未运行 | 启动 `api.py`（参见 7.1）|
| 浏览器提示证书警告或阻止连接 | 证书未被信任 | 参见第 3 节操作步骤 |
| 其他电脑显示"Offline"橙色横幅 | CORS 来源未允许 | 确认访问来源在允许列表中（参见 7.7）或设置 `FRONTEND_ORIGIN` 环境变量 |
| 表格显示旧数据并有橙色横幅 | 后端不可用，显示缓存数据 | 检查后端运行状态 |
| 端口 5001 已被占用 | 旧进程仍在后台运行 | 结束旧进程（参见 7.2）|
| 刷新后无数据 | 服务器上 `M:\` 盘未挂载 | 检查服务器上的网络驱动器映射 |
| 导出文件为空 | 当前筛选结果为零行 | 先重置筛选条件再导出 |
| 出现 429 Too Many Requests 错误 | 超出请求频率限制 | 稍后重试（限制: 30次/分钟）|

---

## 8. 代码仓库

- **Git 远程仓库：** `https://github.com/Infinite-Ng/TrackingNotif.git`
- **主分支：** `main`

---

*最后更新：2026年6月*
