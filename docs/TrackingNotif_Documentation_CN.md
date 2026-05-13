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

数据库连接参数位于 `TrackingNotif/backend/api.py`：

```python
# MS Access
TRACKING_MDB_PATH = r'M:\BR_DATA\SPACE\SNTRACK\sntrdat.mdb'
MDW_FILE          = r'M:\BR_DATA\SPACE\SNTRACK\sntrapp.mdw'
MDB_USERNAME      = 'spruser01'
MDB_PASSWORD      = 'spruser01'

# SQL Server
SQL_SERVER   = 'sydney.itu.int'
SQL_DATABASE = 'SpaceNetworkSystem'
SQL_USERNAME = 'sns_a'
SQL_PASSWORD = 'sns_a_5678'
```

所有 MDB 连接均使用 `ReadOnly=1`，支持多用户同时访问数据库而不产生锁定。

### 7.7 安装依赖

```bat
cd TrackingNotif\backend
pip install -r requirements.txt
```

主要依赖：`flask`、`pyodbc`、`cryptography`

此外还需安装 **Microsoft Access 数据库引擎 2016 可再发行组件**（64位版本）。

### 7.8 常见问题排查

| 现象 | 可能原因 | 解决方法 |
|------|---------|---------|
| 页面显示"Connection failed"横幅 | 后端未运行 | 启动 `api.py`（参见 7.1）|
| 浏览器提示证书警告或阻止连接 | 证书未被信任 | 参见第 3 节操作步骤 |
| 表格显示旧数据并有橙色横幅 | 后端不可用，显示缓存数据 | 检查后端运行状态 |
| 端口 5001 已被占用 | 旧进程仍在后台运行 | 结束旧进程（参见 7.2）|
| 刷新后无数据 | 服务器上 `M:\` 盘未挂载 | 检查服务器上的网络驱动器映射 |
| 导出文件为空 | 当前筛选结果为零行 | 先重置筛选条件再导出 |

---

## 8. 代码仓库

- **Git 远程仓库：** `https://github.com/Infinite-Ng/TrackingNotif.git`
- **主分支：** `main`
- 本地两份代码：`TrackingNotif/`（主版本）和 `TrackingNotif_edited/`

---

*最后更新：2025年*
