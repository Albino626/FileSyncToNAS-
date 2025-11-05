# 静默运行文件同步工具 v1.0.0

Windows 实时文件同步工具，支持将本地文件自动同步到 NAS（网络附加存储）设备。

## 功能特点

- ✅ **实时同步**：监控本地文件变化，自动同步到 NAS
- ✅ **多种协议**：支持 SMB、FTP、NFS、rsync、WebDAV 协议
- ✅ **同步方向**：支持三种同步方向，灵活配置
  - **双向同步**：NAS 和本地互相同步
  - **本地到NAS**：仅将电脑的数据变更同步至 NAS
  - **NAS到本地**：仅将 NAS 的数据变更同步至电脑
- ✅ **删除同步控制**：可选择是否同步删除操作，保护数据安全
- ✅ **静默运行**：支持后台静默运行，不显示窗口
- ✅ **自动重连**：网络中断后自动重连
- ✅ **智能防抖**：避免频繁同步，优化性能

## 系统要求

- Windows 7 或更高版本
- Python 3.7 或更高版本
- 网络连接到 NAS 设备

## 安装步骤

### 1. 安装 Python

如果尚未安装 Python，请访问 [Python 官网](https://www.python.org/downloads/) 下载并安装 Python 3.7 或更高版本。

安装时请勾选 **"Add Python to PATH"** 选项。

### 2. 安装依赖

运行安装脚本：
```
双击运行 install.bat
```

或手动安装：
```
pip install -r requirements.txt
```

### 3. 配置连接信息

1. 复制配置文件模板：
   ```
   复制 config.json.example 为 config.json
   ```

2. 编辑 `config.json`，填写您的配置信息：
   ```json
   {
               "source_dir": "C:\\Users\\YourName\\Documents\\Sync",
        "target_dir": "\\\\NAS_SERVER\\share\\Sync",
        "protocol": "smb",
        "sync_direction": "local-to-nas",
        "sync_deletes": false,
       "smb": {
           "server": "192.168.1.100",
           "username": "your_username",
           "password": "your_password",
           "port": 445
       }
   }
   ```

### 4. 启动同步服务

**方式一（推荐）**：双击 `start_sync.vbs` - 完全静默运行，不显示窗口

**方式二**：双击 `start_sync.bat` - 静默运行（可能短暂闪现窗口）

## 使用方法

### 启动同步服务

- **自动启动**：双击 `start_sync.vbs` 或 `start_sync.bat`
- **手动同步**：双击 `manual_sync.bat` 进行一次性同步

### 检查运行状态

1. **查看任务管理器**：
   - 打开任务管理器（Ctrl+Shift+Esc）
   - 查找 `pythonw.exe` 或 `python.exe` 进程
   - 查看命令行参数中是否包含 `sync_to_nas.py`

2. **查看日志文件**：
   - 日志位置：`logs\sync.log`
   - 使用文本编辑器打开查看详细日志

### 停止同步服务

在任务管理器中结束 `pythonw.exe` 或 `python.exe` 进程。

## 配置文件说明

### 基本配置

- `source_dir`：本地源目录路径（要同步的文件夹）
- `target_dir`：NAS 目标目录路径（SMB 格式：`\\服务器\共享名\路径`）
- `protocol`：使用的协议类型（`smb`、`ftp`、`nfs`、`rsync`、`webdav`）
- `sync_direction`：同步方向（`two-way`、`local-to-nas`、`nas-to-local`）
- `sync_deletes`：是否同步删除操作（`true` 或 `false`，默认 `false`）

### 同步方向说明

#### 1. 双向同步（two-way）
- ✅ 本地文件创建/修改 → 同步到 NAS
- ✅ NAS 文件创建/修改 → 同步到本地
- 🔄 删除操作根据 `sync_deletes` 配置决定是否同步
- ✅ 适合场景：多设备协作，需要保持 NAS 和本地完全一致

#### 2. 本地到NAS（local-to-nas，推荐用于备份）
- ✅ 本地文件创建/修改 → 同步到 NAS
- 🔄 本地文件删除：根据 `sync_deletes` 配置决定是否同步删除 NAS 文件
- ✅ 适合场景：备份重要数据，保护数据安全

#### 3. NAS到本地（nas-to-local）
- ✅ NAS 文件创建/修改 → 同步到本地
- 🔄 NAS 文件删除：根据 `sync_deletes` 配置决定是否同步删除本地文件
- ✅ 适合场景：从 NAS 恢复或下载文件到本地

### 删除同步控制（sync_deletes）

- `false`（默认，推荐）：删除操作**不会**同步，提供数据保护
  - 即使本地/NAS 删除了文件，另一端的文件仍然保留
  - 适合备份场景，防止误删导致数据丢失

- `true`：删除操作**会**同步
  - 本地删除文件 → NAS 文件也会被删除
  - NAS 删除文件 → 本地文件也会被删除
  - ⚠️ **注意**：启用删除同步后，删除操作会双向影响，请谨慎使用

### SMB 协议配置

```json
"smb": {
    "server": "192.168.1.100",      // NAS 服务器地址（IP 或主机名）
    "username": "your_username",     // 用户名（留空表示匿名登录）
    "password": "your_password",     // 密码（留空表示匿名登录）
    "port": 445                      // SMB 端口（默认 445）
}
```

### 其他协议

其他协议的配置示例请参考 `config.json.example` 文件。

## 注意事项

1. **同步方向选择**：
   - 备份场景：使用 `local-to-nas` 方向，`sync_deletes: false`（推荐）
   - 多设备协作：使用 `two-way` 方向，根据需要设置 `sync_deletes`
   - 恢复/下载：使用 `nas-to-local` 方向
   
2. **删除同步建议**：
   - 建议将 `sync_deletes` 设置为 `false`，提供数据保护
   - 即使误删文件，另一端的数据仍然保留
   - 只有在确定需要完全同步删除操作时才设置为 `true`
   
3. **双向同步说明**：
   - 双向同步或 NAS 到本地模式下，程序每 30 秒自动检查一次 NAS 变化
   - 如果 NAS 和本地同时修改了同一个文件，后修改的会覆盖先修改的
   
3. **首次运行**：首次运行会自动同步现有文件，可能需要一些时间

4. **网络中断**：网络中断后程序会自动重连，无需手动重启

5. **日志文件**：定期检查日志文件，确保同步正常

## 常见问题

### Q: 同步失败怎么办？

A: 
1. 检查日志文件 `logs\sync.log` 查看错误信息
2. 确认网络连接正常
3. 确认 NAS 服务器地址、用户名和密码正确
4. 确认目标路径存在且有写入权限

### Q: 如何验证同步是否正常工作？

A:
1. 在源目录中创建或修改一个文件
2. 等待几秒钟
3. 检查 NAS 目标目录，确认文件已同步

### Q: 程序是否支持开机自启动？

A: 可以将 `start_sync.vbs` 添加到 Windows 启动文件夹：
1. 按 `Win+R` 打开运行对话框
2. 输入 `shell:startup` 并按回车
3. 将 `start_sync.vbs` 的快捷方式复制到启动文件夹

## 技术支持

如有问题，请查看日志文件或联系技术支持。

## 版本历史

- **v1.0.0** (2025-11-05)
  - 初始发布版本
  - 支持 SMB、FTP、NFS、rsync、WebDAV 协议
  - 实时文件监控和同步
  - 静默后台运行

## 许可证

本软件仅供个人使用。


