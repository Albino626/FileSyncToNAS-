#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Windows实时文件同步脚本 - 支持多种协议
支持SMB、FTP、NFS、rsync、WebDAV协议
支持实时监听文件变化并自动同步到网络驱动器

同步方向：
- 双向同步（two-way）：
  * 本地文件的创建和修改 → 同步到NAS
  * NAS文件的创建和修改 → 同步到本地
  * 删除操作根据 sync_deletes 配置决定是否同步
  
- 本地到NAS（local-to-nas）：
  * 本地文件的创建和修改 → 同步到NAS
  * 删除操作根据 sync_deletes 配置决定是否同步
  
- NAS到本地（nas-to-local）：
  * NAS文件的创建和修改 → 同步到本地
  * 删除操作根据 sync_deletes 配置决定是否同步
"""

import os
import sys
import json
import time
import logging
import shutil
import subprocess
from pathlib import Path
from abc import ABC, abstractmethod
from threading import Timer
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 配置日志（必须在导入之前）
log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'sync.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 协议相关导入
try:
    from smbprotocol.exceptions import SMBException, SMBOSError
    import smbclient
    SMB_AVAILABLE = True
except ImportError:
    SMB_AVAILABLE = False
    logger.warning("SMB库未安装，SMB协议不可用")

try:
    from ftplib import FTP, error_perm
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False

try:
    import requests
    from requests.auth import HTTPBasicAuth, HTTPDigestAuth
    WEBDAV_AVAILABLE = True
except ImportError:
    WEBDAV_AVAILABLE = False

try:
    import nfs
    NFS_AVAILABLE = True
except ImportError:
    NFS_AVAILABLE = False


class ProtocolAdapter(ABC):
    """协议适配器抽象基类"""
    
    @abstractmethod
    def connect(self):
        """建立连接"""
        pass
    
    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass
    
    @abstractmethod
    def ensure_directory(self, remote_path):
        """确保远程目录存在"""
        pass
    
    @abstractmethod
    def upload_file(self, local_path, remote_path):
        """上传文件"""
        pass
    
    @abstractmethod
    def file_exists(self, remote_path):
        """检查文件是否存在"""
        pass
    
    @abstractmethod
    def get_file_stat(self, remote_path):
        """获取文件统计信息（修改时间等）"""
        pass
    
    @abstractmethod
    def normalize_path(self, path):
        """标准化路径格式"""
        pass
    
    @abstractmethod
    def download_file(self, remote_path, local_path):
        """下载文件到本地（用于双向同步）"""
        pass
    
    @abstractmethod
    def delete_file(self, remote_path):
        """删除远程文件（用于双向同步）"""
        pass
    
    @abstractmethod
    def list_files(self, remote_path=''):
        """列出远程目录下的文件（用于双向同步）"""
        pass


class SMBProtocol(ProtocolAdapter):
    """SMB协议适配器"""
    
    def __init__(self, config, target_dir=''):
        self.server = config.get('server', '')
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.port = config.get('port', 445)
        self.share = config.get('share', '')
        self.base_path = config.get('base_path', '')
        self.target_dir = target_dir  # 目标目录路径（完整SMB路径）
        self._connected = False
    
    def connect(self):
        """建立SMB连接"""
        if not SMB_AVAILABLE:
            raise ImportError("SMB库未安装，请运行: pip install smbprotocol")
        try:
            smbclient.register_session(
                self.server,
                username=self.username,
                password=self.password,
                port=self.port
            )
            self._connected = True
            logger.info(f"SMB连接已建立: {self.server}")
        except Exception as e:
            logger.error(f"SMB连接初始化失败: {e}")
            raise
    
    def disconnect(self):
        """断开SMB连接"""
        # SMB连接通常不需要显式断开
        self._connected = False
    
    def normalize_path(self, path):
        """标准化SMB路径"""
        # 如果提供了 target_dir，使用它作为基础路径
        if self.target_dir:
            # path 是相对路径，需要拼接到 target_dir
            rel_path = str(path).replace('/', '\\')
            if self.target_dir.endswith('\\'):
                return f"{self.target_dir}{rel_path}"
            else:
                return f"{self.target_dir}\\{rel_path}"
        
        # 兼容旧配置方式（使用 share 和 base_path）
        if self.share:
            # 构建完整路径: \\server\share\base_path\relative_path
            rel_path = str(path).replace('/', '\\')
            if self.base_path:
                base = self.base_path.rstrip('\\')
                return f"\\\\{self.server}\\{self.share}\\{base}\\{rel_path}".replace('/', '\\')
            else:
                return f"\\\\{self.server}\\{self.share}\\{rel_path}".replace('/', '\\')
        
        # 如果 path 已经是完整路径，直接返回
        path_str = str(path).replace('/', '\\')
        if path_str.startswith('\\\\'):
            return path_str
        
        # 默认返回原路径
        return path_str
    
    def ensure_directory(self, remote_path):
        """确保SMB目录存在"""
        try:
            # remote_path 是相对路径，需要先转换为完整路径
            full_path = self.normalize_path(remote_path)
            # 提取目录路径（去掉文件名）
            dir_path = '\\'.join(full_path.replace('/', '\\').split('\\')[:-1]) if '\\' in full_path else full_path
            if dir_path:
                # 逐级创建目录
                parts = [p for p in dir_path.split('\\') if p]
                if len(parts) >= 2:  # 至少要有 \\server\share
                    current_path = f"\\\\{parts[0]}\\{parts[1]}"
                    for part in parts[2:]:
                        if part:
                            current_path = f"{current_path}\\{part}"
                            try:
                                smbclient.makedirs(current_path, exist_ok=True)
                            except Exception as e:
                                logger.debug(f"创建目录 {current_path}: {e}")
                    logger.debug(f"目录已确保存在: {dir_path}")
        except Exception as e:
            logger.warning(f"创建目录失败 {remote_path}: {e}")
    
    def upload_file(self, local_path, remote_path):
        """上传文件到SMB"""
        full_path = self.normalize_path(remote_path)
        self.ensure_directory(remote_path)
        
        with open(local_path, 'rb') as src_file:
            with smbclient.open_file(full_path, mode='wb') as dst_file:
                shutil.copyfileobj(src_file, dst_file)
    
    def file_exists(self, remote_path):
        """检查SMB文件是否存在"""
        try:
            full_path = self.normalize_path(remote_path)
            smbclient.stat(full_path)
            return True
        except (FileNotFoundError, SMBOSError):
            return False
    
    def get_file_stat(self, remote_path):
        """获取SMB文件统计信息"""
        try:
            full_path = self.normalize_path(remote_path)
            return smbclient.stat(full_path)
        except (FileNotFoundError, SMBOSError):
            return None
    
    def download_file(self, remote_path, local_path):
        """从SMB下载文件到本地"""
        full_path = self.normalize_path(remote_path)
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)
        
        with smbclient.open_file(full_path, mode='rb') as src_file:
            with open(local_path, 'wb') as dst_file:
                shutil.copyfileobj(src_file, dst_file)
    
    def delete_file(self, remote_path):
        """删除SMB文件或目录"""
        try:
            full_path = self.normalize_path(remote_path)
            
            # 尝试判断是文件还是目录
            try:
                # 尝试列出目录内容来判断是否为目录
                try:
                    list(smbclient.listdir(full_path))
                    is_dir = True
                except:
                    is_dir = False
            except:
                # 如果无法获取信息，尝试作为文件删除
                is_dir = False
            
            if is_dir:
                # 删除目录及其内容（递归删除）
                try:
                    items = list(smbclient.listdir(full_path))
                    for item in items:
                        item_path = f"{full_path}\\{item}" if not full_path.endswith('\\') else f"{full_path}{item}"
                        # 递归删除子项
                        relative_item_path = item_path.replace(self.target_dir.replace('\\', '/'), '').replace('\\', '/').lstrip('/')
                        if not relative_item_path:
                            relative_item_path = item.replace('\\', '/')
                        self.delete_file(relative_item_path)
                    # 删除空目录
                    smbclient.rmdir(full_path)
                except Exception as e:
                    logger.debug(f"删除SMB目录失败 {full_path}: {e}")
                    return False
            else:
                # 删除文件
                try:
                    smbclient.remove(full_path)
                except Exception as e:
                    logger.debug(f"删除SMB文件失败 {full_path}: {e}")
                    return False
            
            return True
        except (FileNotFoundError, SMBOSError) as e:
            logger.debug(f"SMB文件/目录不存在或已删除: {remote_path}, {e}")
            return False
        except Exception as e:
            logger.warning(f"删除SMB文件/目录失败 {remote_path}: {e}")
            return False
    
    def list_files(self, remote_path=''):
        """列出SMB目录下的文件"""
        try:
            full_path = self.normalize_path(remote_path) if remote_path else self.target_dir
            items = []
            for item in smbclient.listdir(full_path):
                item_path = f"{full_path}\\{item}" if not full_path.endswith('\\') else f"{full_path}{item}"
                try:
                    stat = smbclient.stat(item_path)
                    if stat:
                        # 判断是文件还是目录（通过尝试访问父目录的方式）
                        try:
                            # 尝试作为目录访问
                            list(smbclient.listdir(item_path))
                            is_dir = True
                        except:
                            is_dir = False
                        
                        items.append({
                            'name': item,
                            'path': item_path,
                            'is_dir': is_dir,
                            'size': stat.st_size if not is_dir else 0,
                            'mtime': stat.st_mtime
                        })
                except Exception as e:
                    logger.debug(f"获取文件信息失败 {item_path}: {e}")
                    continue
            return items
        except Exception as e:
            logger.debug(f"列出SMB目录失败 {remote_path}: {e}")
            return []


class FTPProtocol(ProtocolAdapter):
    """FTP协议适配器"""
    
    def __init__(self, config):
        self.host = config.get('host', '')
        self.port = config.get('port', 21)
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.base_path = config.get('base_path', '/')
        self.ftp = None
        self._connected = False
    
    def connect(self):
        """建立FTP连接"""
        if not FTP_AVAILABLE:
            raise ImportError("FTP库不可用")
        try:
            self.ftp = FTP()
            self.ftp.connect(self.host, self.port)
            if self.username or self.password:
                self.ftp.login(self.username, self.password)
            else:
                self.ftp.login()
            
            # 切换到基础路径
            if self.base_path and self.base_path != '/':
                try:
                    self.ftp.cwd(self.base_path)
                except error_perm:
                    logger.warning(f"无法切换到FTP基础路径: {self.base_path}")
            
            self._connected = True
            logger.info(f"FTP连接已建立: {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"FTP连接失败: {e}")
            raise
    
    def disconnect(self):
        """断开FTP连接"""
        if self.ftp:
            try:
                self.ftp.quit()
            except:
                self.ftp.close()
            self.ftp = None
        self._connected = False
    
    def normalize_path(self, path):
        """标准化FTP路径"""
        # FTP使用正斜杠
        path = path.replace('\\', '/')
        if not path.startswith('/'):
            path = '/' + path
        return path
    
    def ensure_directory(self, remote_path):
        """确保FTP目录存在"""
        try:
            dir_path = os.path.dirname(self.normalize_path(remote_path))
            if dir_path == '/':
                return
            
            # 逐级创建目录
            parts = [p for p in dir_path.split('/') if p]
            current_path = self.base_path
            for part in parts:
                current_path = current_path.rstrip('/') + '/' + part
                try:
                    self.ftp.cwd(current_path)
                except error_perm:
                    try:
                        self.ftp.mkd(current_path)
                        self.ftp.cwd(current_path)
                    except error_perm as e:
                        logger.warning(f"创建FTP目录失败 {current_path}: {e}")
        except Exception as e:
            logger.warning(f"确保FTP目录存在时出错 {remote_path}: {e}")
    
    def upload_file(self, local_path, remote_path):
        """上传文件到FTP"""
        self.ensure_directory(remote_path)
        remote_file = self.normalize_path(remote_path)
        
        with open(local_path, 'rb') as f:
            self.ftp.storbinary(f'STOR {remote_file}', f)
    
    def file_exists(self, remote_path):
        """检查FTP文件是否存在"""
        try:
            remote_file = self.normalize_path(remote_path)
            dir_path = os.path.dirname(remote_file)
            filename = os.path.basename(remote_file)
            
            if dir_path:
                self.ftp.cwd(dir_path)
            
            files = self.ftp.nlst()
            return filename in files
        except:
            return False
    
    def get_file_stat(self, remote_path):
        """获取FTP文件统计信息"""
        try:
            remote_file = self.normalize_path(remote_path)
            size = self.ftp.size(remote_file)
            mtime = self.ftp.voidcmd(f'MDTM {remote_file}')[4:].strip()
            
            class Stat:
                def __init__(self, size, mtime):
                    self.st_size = size
                    self.st_mtime = time.mktime(time.strptime(mtime, '%Y%m%d%H%M%S'))
            
            return Stat(size, mtime)
        except:
            return None
    
    def download_file(self, remote_path, local_path):
        """从FTP下载文件到本地"""
        remote_file = self.normalize_path(remote_path)
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(local_path, 'wb') as f:
            self.ftp.retrbinary(f'RETR {remote_file}', f.write)
    
    def delete_file(self, remote_path):
        """删除FTP文件"""
        try:
            remote_file = self.normalize_path(remote_path)
            self.ftp.delete(remote_file)
            return True
        except:
            return False
    
    def list_files(self, remote_path=''):
        """列出FTP目录下的文件"""
        items = []
        try:
            if remote_path:
                self.ftp.cwd(self.normalize_path(remote_path))
            files = self.ftp.nlst()
            for item in files:
                try:
                    size = self.ftp.size(item)
                    items.append({
                        'name': item,
                        'path': item,
                        'is_dir': False,
                        'size': size,
                        'mtime': 0
                    })
                except:
                    items.append({
                        'name': item,
                        'path': item,
                        'is_dir': True,
                        'size': 0,
                        'mtime': 0
                    })
        except:
            pass
        return items


class NFSProtocol(ProtocolAdapter):
    """NFS协议适配器"""
    
    def __init__(self, config):
        self.host = config.get('host', '')
        self.export_path = config.get('export_path', '')
        self.base_path = config.get('base_path', '')
        self.mount_point = config.get('mount_point', '')
        self._connected = False
    
    def connect(self):
        """建立NFS连接（通过挂载）"""
        if not NFS_AVAILABLE:
            logger.warning("NFS库未安装，使用系统挂载方式")
            # 在Windows上，NFS通常需要手动挂载
            if self.mount_point and Path(self.mount_point).exists():
                self._connected = True
                logger.info(f"NFS挂载点已存在: {self.mount_point}")
            else:
                logger.warning("NFS挂载点不存在，请手动挂载NFS共享")
                raise ConnectionError("NFS挂载点不存在")
        else:
            # 使用nfs库连接
            try:
                self.nfs_client = nfs.connect(self.host, self.export_path)
                self._connected = True
                logger.info(f"NFS连接已建立: {self.host}:{self.export_path}")
            except Exception as e:
                logger.error(f"NFS连接失败: {e}")
                raise
    
    def disconnect(self):
        """断开NFS连接"""
        if NFS_AVAILABLE and hasattr(self, 'nfs_client'):
            self.nfs_client.close()
        self._connected = False
    
    def normalize_path(self, path):
        """标准化NFS路径"""
        if self.mount_point:
            # 使用挂载点路径
            return str(Path(self.mount_point) / self.base_path / path).replace('\\', '/')
        else:
            return path.replace('\\', '/')
    
    def ensure_directory(self, remote_path):
        """确保NFS目录存在"""
        try:
            full_path = self.normalize_path(remote_path)
            dir_path = os.path.dirname(full_path)
            Path(dir_path).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"创建NFS目录失败 {remote_path}: {e}")
    
    def upload_file(self, local_path, remote_path):
        """上传文件到NFS"""
        full_path = self.normalize_path(remote_path)
        self.ensure_directory(remote_path)
        shutil.copy2(local_path, full_path)
    
    def file_exists(self, remote_path):
        """检查NFS文件是否存在"""
        try:
            full_path = self.normalize_path(remote_path)
            return Path(full_path).exists()
        except:
            return False
    
    def get_file_stat(self, remote_path):
        """获取NFS文件统计信息"""
        try:
            full_path = self.normalize_path(remote_path)
            return Path(full_path).stat()
        except:
            return None
    
    def download_file(self, remote_path, local_path):
        """从NFS下载文件到本地"""
        full_path = self.normalize_path(remote_path)
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full_path, local_path)
    
    def delete_file(self, remote_path):
        """删除NFS文件"""
        try:
            full_path = self.normalize_path(remote_path)
            Path(full_path).unlink()
            return True
        except:
            return False
    
    def list_files(self, remote_path=''):
        """列出NFS目录下的文件"""
        items = []
        try:
            full_path = self.normalize_path(remote_path) if remote_path else self.mount_point
            path_obj = Path(full_path)
            if path_obj.exists() and path_obj.is_dir():
                for item in path_obj.iterdir():
                    try:
                        stat = item.stat()
                        items.append({
                            'name': item.name,
                            'path': str(item),
                            'is_dir': item.is_dir(),
                            'size': stat.st_size if item.is_file() else 0,
                            'mtime': stat.st_mtime
                        })
                    except:
                        continue
        except:
            pass
        return items


class RSyncProtocol(ProtocolAdapter):
    """rsync协议适配器"""
    
    def __init__(self, config):
        self.host = config.get('host', '')
        self.port = config.get('port', 22)
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.base_path = config.get('base_path', '/')
        self.use_ssh = config.get('use_ssh', True)
        self._connected = False
    
    def connect(self):
        """建立rsync连接（通过测试连接）"""
        # rsync通过命令行调用，连接测试通过执行命令
        try:
            if self.use_ssh:
                # 测试SSH连接
                cmd = ['ssh', '-o', 'ConnectTimeout=5', f'{self.username}@{self.host}', 'echo test']
                result = subprocess.run(cmd, capture_output=True, timeout=5)
                if result.returncode == 0:
                    self._connected = True
                    logger.info(f"rsync/SSH连接测试成功: {self.host}")
                else:
                    raise ConnectionError("rsync/SSH连接测试失败")
            else:
                self._connected = True
                logger.info("rsync连接已就绪")
        except Exception as e:
            logger.error(f"rsync连接测试失败: {e}")
            raise
    
    def disconnect(self):
        """断开rsync连接"""
        self._connected = False
    
    def normalize_path(self, path):
        """标准化rsync路径"""
        if self.use_ssh:
            if self.username:
                return f"{self.username}@{self.host}:{self.base_path.rstrip('/')}/{path}".replace('\\', '/')
            else:
                return f"{self.host}:{self.base_path.rstrip('/')}/{path}".replace('\\', '/')
        else:
            return f"{self.host}::{self.base_path.rstrip('/')}/{path}".replace('\\', '/')
    
    def ensure_directory(self, remote_path):
        """确保rsync目录存在（通过创建父目录）"""
        # rsync会自动创建目录，这里不需要额外操作
        pass
    
    def upload_file(self, local_path, remote_path):
        """使用rsync上传文件"""
        remote = self.normalize_path(remote_path)
        
        if self.use_ssh:
            # 使用SSH的rsync
            cmd = ['rsync', '-avz', '--progress', str(local_path), remote]
            if self.password:
                # 使用sshpass（如果可用）
                cmd = ['sshpass', '-p', self.password] + cmd
        else:
            # 使用rsync daemon模式
            if self.password:
                # 设置RSYNC_PASSWORD环境变量
                env = os.environ.copy()
                env['RSYNC_PASSWORD'] = self.password
            else:
                env = os.environ.copy()
            
            cmd = ['rsync', '-avz', '--progress', str(local_path), remote]
            subprocess.run(cmd, env=env, check=True)
            return
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.debug(f"rsync输出: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"rsync上传失败: {e.stderr}")
            raise
    
    def file_exists(self, remote_path):
        """检查rsync文件是否存在（通过列出文件）"""
        try:
            remote = self.normalize_path(remote_path)
            dir_path = os.path.dirname(remote)
            filename = os.path.basename(remote_path)
            
            if self.use_ssh:
                cmd = ['ssh', f'{self.username}@{self.host}', f'ls "{dir_path}/{filename}"']
            else:
                cmd = ['rsync', '--list-only', f'{dir_path}/']
            
            result = subprocess.run(cmd, capture_output=True, timeout=5)
            return result.returncode == 0 and filename in result.stdout.decode()
        except:
            return False
    
    def get_file_stat(self, remote_path):
        """获取rsync文件统计信息"""
        try:
            remote = self.normalize_path(remote_path)
            if self.use_ssh:
                cmd = ['ssh', f'{self.username}@{self.host}', f'stat "{remote}"']
            else:
                # rsync daemon模式较复杂，简化处理
                return None
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # 解析stat输出（简化处理）
                class Stat:
                    def __init__(self):
                        self.st_size = 0
                        self.st_mtime = 0
                return Stat()
        except:
            return None
    
    def download_file(self, remote_path, local_path):
        """从rsync下载文件到本地"""
        remote = self.normalize_path(remote_path)
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)
        
        if self.use_ssh:
            cmd = ['rsync', '-avz', remote, str(local_path)]
        else:
            cmd = ['rsync', '-avz', remote, str(local_path)]
        
        subprocess.run(cmd, check=True)
    
    def delete_file(self, remote_path):
        """删除rsync文件"""
        try:
            remote = self.normalize_path(remote_path)
            if self.use_ssh:
                cmd = ['ssh', f'{self.username}@{self.host}', f'rm "{remote}"']
            else:
                return False
            result = subprocess.run(cmd, timeout=5)
            return result.returncode == 0
        except:
            return False
    
    def list_files(self, remote_path=''):
        """列出rsync目录下的文件"""
        items = []
        try:
            remote = self.normalize_path(remote_path) if remote_path else f"{self.username}@{self.host}:{self.base_path}"
            if self.use_ssh:
                cmd = ['ssh', f'{self.username}@{self.host}', f'ls -la "{remote}"']
            else:
                return items
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # 解析ls输出（简化处理）
                for line in result.stdout.split('\n'):
                    if line.strip():
                        items.append({
                            'name': line.split()[-1] if len(line.split()) > 0 else '',
                            'path': line,
                            'is_dir': line.startswith('d'),
                            'size': 0,
                            'mtime': 0
                        })
        except:
            pass
        return items


class WebDAVProtocol(ProtocolAdapter):
    """WebDAV协议适配器"""
    
    def __init__(self, config):
        self.url = config.get('url', '')
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.base_path = config.get('base_path', '')
        self.auth_type = config.get('auth_type', 'basic')  # basic 或 digest
        self._connected = False
        self.session = None
    
    def connect(self):
        """建立WebDAV连接"""
        if not WEBDAV_AVAILABLE:
            raise ImportError("requests库未安装，请运行: pip install requests")
        try:
            self.session = requests.Session()
            if self.username and self.password:
                if self.auth_type == 'digest':
                    self.session.auth = HTTPDigestAuth(self.username, self.password)
                else:
                    self.session.auth = HTTPBasicAuth(self.username, self.password)
            
            # 测试连接
            test_url = self.url.rstrip('/') + '/' + self.base_path.lstrip('/')
            response = self.session.request('PROPFIND', test_url, timeout=5)
            if response.status_code in [200, 207, 404]:
                self._connected = True
                logger.info(f"WebDAV连接已建立: {self.url}")
            else:
                raise ConnectionError(f"WebDAV连接测试失败: {response.status_code}")
        except Exception as e:
            logger.error(f"WebDAV连接失败: {e}")
            raise
    
    def disconnect(self):
        """断开WebDAV连接"""
        if self.session:
            self.session.close()
            self.session = None
        self._connected = False
    
    def normalize_path(self, path):
        """标准化WebDAV路径"""
        base_url = self.url.rstrip('/')
        base_path = self.base_path.strip('/')
        remote_path = path.replace('\\', '/').lstrip('/')
        
        if base_path:
            return f"{base_url}/{base_path}/{remote_path}".replace('//', '/')
        else:
            return f"{base_url}/{remote_path}".replace('//', '/')
    
    def ensure_directory(self, remote_path):
        """确保WebDAV目录存在"""
        try:
            dir_path = os.path.dirname(remote_path)
            if not dir_path:
                return
            
            full_url = self.normalize_path(dir_path)
            # 使用MKCOL方法创建目录
            response = self.session.request('MKCOL', full_url, timeout=10)
            if response.status_code not in [201, 405]:  # 405表示已存在
                logger.debug(f"创建WebDAV目录: {response.status_code} - {full_url}")
        except Exception as e:
            logger.warning(f"确保WebDAV目录存在时出错 {remote_path}: {e}")
    
    def upload_file(self, local_path, remote_path):
        """上传文件到WebDAV"""
        full_url = self.normalize_path(remote_path)
        self.ensure_directory(remote_path)
        
        with open(local_path, 'rb') as f:
            response = self.session.put(full_url, data=f, timeout=30)
            if response.status_code not in [200, 201, 204]:
                raise Exception(f"WebDAV上传失败: {response.status_code} - {response.text}")
    
    def file_exists(self, remote_path):
        """检查WebDAV文件是否存在"""
        try:
            full_url = self.normalize_path(remote_path)
            response = self.session.head(full_url, timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def get_file_stat(self, remote_path):
        """获取WebDAV文件统计信息"""
        try:
            full_url = self.normalize_path(remote_path)
            response = self.session.head(full_url, timeout=5)
            if response.status_code == 200:
                class Stat:
                    def __init__(self, size, mtime):
                        self.st_size = size
                        self.st_mtime = mtime
                
                size = int(response.headers.get('Content-Length', 0))
                mtime_str = response.headers.get('Last-Modified', '')
                if mtime_str:
                    from email.utils import parsedate_to_datetime
                    mtime = parsedate_to_datetime(mtime_str).timestamp()
                else:
                    mtime = 0
                
                return Stat(size, mtime)
        except:
            return None
    
    def download_file(self, remote_path, local_path):
        """从WebDAV下载文件到本地"""
        full_url = self.normalize_path(remote_path)
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)
        
        response = self.session.get(full_url, timeout=30)
        if response.status_code == 200:
            with open(local_path, 'wb') as f:
                f.write(response.content)
        else:
            raise Exception(f"WebDAV下载失败: {response.status_code}")
    
    def delete_file(self, remote_path):
        """删除WebDAV文件"""
        try:
            full_url = self.normalize_path(remote_path)
            response = self.session.delete(full_url, timeout=10)
            return response.status_code in [200, 204, 404]
        except:
            return False
    
    def list_files(self, remote_path=''):
        """列出WebDAV目录下的文件"""
        items = []
        try:
            full_url = self.normalize_path(remote_path) if remote_path else self.normalize_path('')
            response = self.session.request('PROPFIND', full_url, timeout=10)
            if response.status_code in [200, 207]:
                # 解析PROPFIND响应（简化处理，需要实际实现XML解析）
                # 这里返回空列表，实际使用时需要实现XML解析
                pass
        except:
            pass
        return items


def create_protocol(protocol_type, config, target_dir=''):
    """协议工厂函数"""
    protocol_map = {
        'smb': SMBProtocol,
        'ftp': FTPProtocol,
        'nfs': NFSProtocol,
        'rsync': RSyncProtocol,
        'webdav': WebDAVProtocol
    }
    
    protocol_type = protocol_type.lower()
    if protocol_type not in protocol_map:
        raise ValueError(f"不支持的协议类型: {protocol_type}。支持: {', '.join(protocol_map.keys())}")
    
    protocol_class = protocol_map[protocol_type]
    # SMB 协议需要传递 target_dir
    if protocol_type == 'smb':
        return protocol_class(config, target_dir=target_dir)
    else:
        return protocol_class(config)


class SyncHandler(FileSystemEventHandler):
    """文件系统事件处理器"""
    
    def __init__(self, source_dir, target_dir, protocol, sync_direction='local-to-nas', sync_deletes=False, debounce_time=1.0):
        super().__init__()
        self.source_dir = Path(source_dir)
        self.target_dir = target_dir
        self.protocol = protocol
        self.sync_direction = sync_direction.lower()  # 'two-way', 'local-to-nas', 'nas-to-local'
        self.sync_deletes = sync_deletes  # 是否同步删除操作
        self.pending_operations = []
        self.debounce_time = debounce_time  # 防抖延迟时间（秒）
        self.pending_timers = {}  # 存储待同步文件的定时器
        self._syncing_from_remote = False  # 标志：是否正在从远程同步（避免循环）
        self._recently_deleted = {}  # 记录最近删除的文件，格式：{relative_path: timestamp}
        self._delete_cooldown = 5.0  # 删除冷却时间（秒），在此期间不会从NAS恢复文件
        
        # 初始化协议连接
        self.protocol.connect()
    
    def _sync_file(self, src_path, from_remote=False):
        """同步单个文件
        
        Args:
            src_path: 源文件路径
            from_remote: 是否从远程同步到本地（True：远程→本地，False：本地→远程）
        """
        try:
            if from_remote:
                # 从远程同步到本地
                relative_path = src_path  # src_path 在这里是相对路径（字符串）
                remote_path = relative_path
                local_path = self.source_dir / relative_path.replace('/', '\\')
                
                # 检查该文件是否在最近删除列表中（防止删除后立即恢复）
                current_time = time.time()
                if relative_path in self._recently_deleted:
                    delete_time = self._recently_deleted[relative_path]
                    if current_time - delete_time < self._delete_cooldown:
                        logger.debug(f"文件 {relative_path} 在删除冷却期内，跳过从NAS恢复")
                        return
                    else:
                        # 冷却期已过，从删除列表中移除
                        del self._recently_deleted[relative_path]
                
                # 检查是否需要同步（比较修改时间）
                try:
                    remote_stat = self.protocol.get_file_stat(remote_path)
                    if not remote_stat:
                        return  # 远程文件不存在
                    
                    if local_path.exists():
                        local_stat = local_path.stat()
                        # 如果远程文件不比本地新，跳过
                        if remote_stat.st_mtime <= local_stat.st_mtime:
                            return
                    
                    # 下载文件
                    self._syncing_from_remote = True
                    self.protocol.download_file(remote_path, str(local_path))
                    logger.info(f"已从NAS同步到本地: {relative_path}")
                except Exception as e:
                    logger.warning(f"从NAS同步文件失败 {relative_path}: {e}")
                finally:
                    self._syncing_from_remote = False
            else:
                # 从本地同步到远程
                relative_path = src_path.relative_to(self.source_dir)
                remote_path = str(relative_path).replace('\\', '/')
                
                # 如果源文件存在，执行复制
                if src_path.exists() and src_path.is_file():
                    try:
                        self.protocol.upload_file(str(src_path), remote_path)
                        logger.info(f"文件已同步到NAS: {relative_path}")
                    except Exception as write_error:
                        logger.error(f"写入文件失败 {remote_path}: {write_error}")
                        logger.error(f"请检查目标路径是否存在且有写入权限")
                    
        except Exception as e:
            logger.error(f"同步文件失败 {src_path}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    def _sync_directory(self, src_path):
        """同步目录结构"""
        try:
            relative_path = src_path.relative_to(self.source_dir)
            remote_path = str(relative_path).replace('\\', '/')
            
            if src_path.exists() and src_path.is_dir():
                self.protocol.ensure_directory(remote_path)
                logger.debug(f"目录已创建: {relative_path}")
            # 注意：本地目录删除不删除NAS上的目录（单向同步）
        except Exception as e:
            logger.error(f"同步目录失败 {src_path}: {e}")
    
    def _schedule_sync(self, file_path, is_directory=False):
        """安排文件同步（带防抖机制）"""
        file_path_str = str(file_path)
        
        # 如果是目录，立即同步
        if is_directory:
            self._sync_directory(file_path)
            return
        
        # 取消之前的定时器（如果存在）
        if file_path_str in self.pending_timers:
            timer = self.pending_timers[file_path_str]
            timer.cancel()
        
        # 创建新的定时器
        def sync_file():
            try:
                if Path(file_path_str).exists():
                    self._sync_file(Path(file_path_str))
            finally:
                # 同步完成后移除定时器
                self.pending_timers.pop(file_path_str, None)
        
        timer = Timer(self.debounce_time, sync_file)
        timer.start()
        self.pending_timers[file_path_str] = timer
    
    def on_created(self, event):
        """文件/目录创建事件"""
        # 只处理本地到NAS或双向同步的本地变化
        if self.sync_direction in ['local-to-nas', 'two-way']:
            file_path = Path(event.src_path)
            if event.is_directory:
                self._sync_directory(file_path)
            else:
                # 使用防抖机制，避免频繁同步
                self._schedule_sync(file_path, is_directory=False)
    
    def on_modified(self, event):
        """文件修改事件"""
        # 只处理本地到NAS或双向同步的本地变化
        if self.sync_direction in ['local-to-nas', 'two-way']:
            if not event.is_directory:
                # 使用防抖机制，避免频繁同步
                file_path = Path(event.src_path)
                self._schedule_sync(file_path, is_directory=False)
    
    def on_deleted(self, event):
        """文件/目录删除事件"""
        if self._syncing_from_remote:
            # 如果正在从远程同步，忽略本地删除事件（避免循环）
            logger.debug(f"忽略删除事件（正在从远程同步）: {event.src_path}")
            return
        
        # 只处理本地到NAS或双向同步的本地删除
        if self.sync_direction not in ['local-to-nas', 'two-way']:
            logger.debug(f"忽略删除事件（同步方向不支持）: {event.src_path}, sync_direction={self.sync_direction}")
            return
        
        # 根据 sync_deletes 配置决定是否同步删除
        if not self.sync_deletes:
            # 不同步删除操作（数据保护）
            if event.is_directory:
                logger.debug(f"本地目录已删除（不删除NAS目录，sync_deletes=False）: {event.src_path}")
            else:
                logger.debug(f"本地文件已删除（不删除NAS文件，sync_deletes=False）: {event.src_path}")
            return
        
        # 同步删除到NAS
        try:
            file_path = Path(event.src_path)
            
            # 检查文件是否真的被删除（而不是移动操作）
            if file_path.exists():
                logger.debug(f"文件仍然存在，可能是移动操作，跳过删除: {event.src_path}")
                return
            
            # 计算相对路径
            try:
                relative_path = file_path.relative_to(self.source_dir)
            except ValueError:
                # 文件不在源目录内，跳过
                logger.debug(f"文件不在源目录内，跳过: {event.src_path}")
                return
            
            # 转换为远程路径格式
            remote_path = str(relative_path).replace('\\', '/')
            
            logger.info(f"检测到本地删除: {relative_path}，准备同步删除到NAS...")
            
            # 检查远程文件是否存在
            try:
                if self.protocol.file_exists(remote_path):
                    # 删除远程文件或目录
                    if event.is_directory:
                        # 对于目录，需要递归删除（如果协议支持）
                        # 这里先尝试删除目录下的所有文件，然后删除目录本身
                        logger.info(f"正在删除NAS目录: {relative_path}")
                        # 使用delete_file删除目录（某些协议可能支持）
                        result = self.protocol.delete_file(remote_path)
                        if result:
                            logger.info(f"已同步删除NAS目录: {relative_path}")
                            # 记录到删除列表，防止双向同步立即恢复目录
                            self._recently_deleted[relative_path] = time.time()
                        else:
                            logger.warning(f"删除NAS目录失败: {relative_path}")
                    else:
                        # 删除文件
                        result = self.protocol.delete_file(remote_path)
                        if result:
                            logger.info(f"已同步删除NAS文件: {relative_path}")
                            # 记录到删除列表，防止双向同步立即恢复文件
                            self._recently_deleted[relative_path] = time.time()
                        else:
                            logger.warning(f"删除NAS文件失败（协议返回False）: {relative_path}")
                else:
                    logger.debug(f"NAS上文件不存在，无需删除: {relative_path}")
            except Exception as e:
                logger.error(f"同步删除NAS文件/目录失败 {relative_path}: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        except Exception as e:
            logger.error(f"处理删除事件失败 {event.src_path}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    def on_moved(self, event):
        """文件/目录移动事件"""
        # 只处理本地到NAS或双向同步的本地变化
        if self.sync_direction not in ['local-to-nas', 'two-way']:
            return
        
        logger.debug(f"文件/目录移动: {event.src_path} -> {event.dest_path}")
        dest_path = Path(event.dest_path)
        if dest_path.exists():
            if dest_path.is_dir():
                self._sync_directory(dest_path)
            else:
                # 使用防抖机制
                self._schedule_sync(dest_path, is_directory=False)
        
        # 如果启用删除同步，删除旧位置的文件
        if self.sync_deletes:
            try:
                old_path = Path(event.src_path)
                if old_path.exists():
                    return  # 旧文件仍然存在
                
                relative_path = old_path.relative_to(self.source_dir) if self.source_dir in old_path.parents else None
                if relative_path:
                    remote_path = str(relative_path).replace('\\', '/')
                    try:
                        if self.protocol.file_exists(remote_path):
                            self.protocol.delete_file(remote_path)
                            logger.info(f"已删除NAS上的旧文件: {relative_path}")
                    except Exception as e:
                        logger.debug(f"删除NAS旧文件失败 {relative_path}: {e}")
            except:
                pass
    
    def _check_remote_changes(self):
        """检查远程文件变化（用于双向同步或NAS到本地同步）"""
        if self.sync_direction not in ['two-way', 'nas-to-local'] or self._syncing_from_remote:
            return
        
        try:
            # 递归检查远程目录
            self._scan_remote_directory('')
        except Exception as e:
            logger.debug(f"检查远程变化失败: {e}")
    
    def _scan_remote_directory(self, remote_dir):
        """递归扫描远程目录"""
        try:
            items = self.protocol.list_files(remote_dir)
            remote_items_map = {}  # 用于跟踪远程存在的文件和目录
            
            for item in items:
                item_name = item['name']
                
                # 构建相对路径
                if remote_dir:
                    relative_path = f"{remote_dir}/{item_name}" if not remote_dir.endswith('/') else f"{remote_dir}{item_name}"
                else:
                    relative_path = item_name
                
                # 标准化路径分隔符
                relative_path = relative_path.replace('\\', '/')
                remote_items_map[item_name] = item
                
                if item['is_dir']:
                    # 递归扫描子目录
                    self._scan_remote_directory(relative_path)
                else:
                    # 检查文件是否需要同步
                    local_path = self.source_dir / relative_path.replace('/', '\\')
                    if not local_path.exists():
                        # 文件在远程存在但本地不存在，下载
                        self._sync_file(relative_path, from_remote=True)
                    else:
                        # 比较修改时间
                        try:
                            remote_stat = self.protocol.get_file_stat(relative_path)
                            if remote_stat:
                                local_stat = local_path.stat()
                                if remote_stat.st_mtime > local_stat.st_mtime:
                                    # 远程文件更新，下载
                                    self._sync_file(relative_path, from_remote=True)
                        except:
                            pass
            
            # 检查本地是否有远程不存在的文件和目录（用于删除同步）
            if self.sync_deletes:
                self._check_local_files_to_delete(remote_dir, remote_items_map)
        except Exception as e:
            logger.debug(f"扫描远程目录失败 {remote_dir}: {e}")
    
    def _check_local_files_to_delete(self, remote_dir, remote_items_map=None):
        """检查本地需要删除的文件和目录（当远程文件已删除且启用删除同步时）"""
        try:
            if remote_items_map is None:
                # 如果没有提供远程项目映射，重新获取
                remote_items = self.protocol.list_files(remote_dir)
                remote_items_map = {item['name']: item for item in remote_items}
            
            # 获取本地文件和目录列表
            local_dir = self.source_dir / remote_dir.replace('/', '\\') if remote_dir else self.source_dir
            if not local_dir.exists() or not local_dir.is_dir():
                return
            
            local_items = {item.name: item for item in local_dir.iterdir()}
            remote_names = set(remote_items_map.keys())
            local_names = set(local_items.keys())
            
            # 找出本地存在但远程不存在的项目
            items_to_delete = local_names - remote_names
            
            # 删除这些文件和目录
            for item_name in items_to_delete:
                item_path = local_items[item_name]
                try:
                    if item_path.is_file():
                        # 删除文件
                        item_path.unlink()
                        relative_path = item_path.relative_to(self.source_dir)
                        logger.info(f"已删除本地文件（远程已删除）: {relative_path}")
                    elif item_path.is_dir():
                        # 递归删除目录
                        import shutil
                        shutil.rmtree(item_path)
                        relative_path = item_path.relative_to(self.source_dir)
                        logger.info(f"已删除本地目录（远程已删除）: {relative_path}")
                except Exception as e:
                    logger.warning(f"删除本地项目失败 {item_path}: {e}")
        except Exception as e:
            logger.debug(f"检查本地文件删除失败 {remote_dir}: {e}")


def load_config(config_path='config.json'):
    """加载配置文件"""
    config_file = Path(config_path)
    if not config_file.exists():
        logger.error(f"配置文件不存在: {config_path}")
        # 创建默认配置
        default_config = {
            "source_dir": "C:\\Users\\Albino\\Documents\\Sync",
            "target_dir": "",
            "protocol": "smb",
            "sync_direction": "local-to-nas",
            "sync_deletes": False,
            "smb": {
                "server": "192.168.1.100",
                "share": "share",
                "base_path": "Sync",
                "username": "",
                "password": "",
                "port": 445
            },
            "ftp": {
                "host": "192.168.1.100",
                "port": 21,
                "username": "",
                "password": "",
                "base_path": "/Sync"
            },
            "nfs": {
                "host": "192.168.1.100",
                "export_path": "/export",
                "base_path": "Sync",
                "mount_point": "Z:"
            },
            "rsync": {
                "host": "192.168.1.100",
                "port": 22,
                "username": "",
                "password": "",
                "base_path": "/Sync",
                "use_ssh": True
            },
            "webdav": {
                "url": "http://192.168.1.100:8080",
                "username": "",
                "password": "",
                "base_path": "Sync",
                "auth_type": "basic"
            }
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        logger.info(f"已创建默认配置文件: {config_path}，请编辑后重新运行")
        return None
    else:
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 尝试解析 JSON
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                # 提供详细的错误信息
                logger.error(f"配置文件 JSON 格式错误:")
                logger.error(f"  错误类型: {e.msg}")
                logger.error(f"  位置: 第 {e.lineno} 行, 第 {e.colno} 列 (字符 {e.pos})")
                
                # 显示错误位置周围的代码
                lines = content.split('\n')
                if e.lineno <= len(lines):
                    start = max(0, e.lineno - 3)
                    end = min(len(lines), e.lineno + 2)
                    logger.error("错误位置周围的代码:")
                    for i in range(start, end):
                        marker = " >>> " if i == e.lineno - 1 else "     "
                        logger.error(f"{marker}{i+1:3d}: {lines[i]}")
                
                # 尝试修复常见的 JSON 错误
                logger.info("尝试自动修复常见的 JSON 格式错误...")
                import re
                fixed_content = content
                
                # 修复1: 移除 JSON 注释（支持 // 和 # 两种注释格式）
                # 使用逐字符解析，正确处理字符串内的注释标记
                lines = fixed_content.split('\n')
                fixed_lines = []
                
                for line in lines:
                    # 检查是否为纯注释行（整行都是注释）
                    stripped = line.strip()
                    if stripped.startswith('//') or stripped.startswith('#'):
                        # 纯注释行，跳过
                        continue
                    
                    # 处理行内注释
                    fixed_line = []
                    in_string = False
                    escape_next = False
                    i = 0
                    
                    while i < len(line):
                        char = line[i]
                        
                        # 处理转义字符
                        if escape_next:
                            fixed_line.append(char)
                            escape_next = False
                            i += 1
                            continue
                        
                        if char == '\\':
                            escape_next = True
                            fixed_line.append(char)
                        elif char == '"':
                            in_string = not in_string
                            fixed_line.append(char)
                        elif not in_string:
                            # 不在字符串中，检查注释
                            if char == '/' and i + 1 < len(line) and line[i + 1] == '/':
                                # 遇到 // 注释，停止处理本行剩余部分
                                break
                            elif char == '#':
                                # 遇到 # 注释，停止处理本行剩余部分
                                break
                            else:
                                fixed_line.append(char)
                        else:
                            # 在字符串中，保留所有字符（包括可能的 // 或 #）
                            fixed_line.append(char)
                        
                        i += 1
                    
                    fixed_line_str = ''.join(fixed_line).rstrip()
                    # 保留非空行
                    if fixed_line_str.strip():
                        fixed_lines.append(fixed_line_str)
                
                fixed_content = '\n'.join(fixed_lines)
                
                # 修复2: 移除对象/数组最后一个元素后的尾随逗号
                fixed_content = re.sub(r',(\s*[}\]])', r'\1', fixed_content)
                
                # 修复3: 清理多余的空行
                fixed_content = re.sub(r'\n\s*\n+', '\n', fixed_content)
                
                # 再次尝试解析
                try:
                    config = json.loads(fixed_content)
                    logger.info("自动修复成功！")
                    # 保存修复后的文件
                    backup_path = str(config_file) + '.bak'
                    if Path(backup_path).exists():
                        Path(backup_path).unlink()
                    config_file.rename(backup_path)
                    logger.info(f"已备份原配置文件到: {backup_path}")
                    
                    with open(config_file, 'w', encoding='utf-8') as f:
                        json.dump(config, f, indent=4, ensure_ascii=False)
                    logger.info(f"已保存修复后的配置文件: {config_file}")
                    return config
                except json.JSONDecodeError as e2:
                    logger.error(f"自动修复失败: {e2.msg}")
                    logger.error(f"修复后仍存在错误，位置: 第 {e2.lineno} 行, 第 {e2.colno} 列 (字符 {e2.pos})")
                    
                    # 保存修复后的内容到临时文件，便于调试
                    debug_path = str(config_file) + '.fixed'
                    try:
                        with open(debug_path, 'w', encoding='utf-8') as f:
                            f.write(fixed_content)
                        logger.info(f"已保存修复后的内容到: {debug_path}，请检查")
                    except:
                        pass
                    
                    # 显示修复后内容的错误位置
                    fixed_lines = fixed_content.split('\n')
                    if e2.lineno <= len(fixed_lines):
                        start = max(0, e2.lineno - 3)
                        end = min(len(fixed_lines), e2.lineno + 2)
                        logger.error("修复后内容的错误位置:")
                        for i in range(start, end):
                            marker = " >>> " if i == e2.lineno - 1 else "     "
                            logger.error(f"{marker}{i+1:3d}: {fixed_lines[i]}")
                    
                    logger.error("请手动检查并修复配置文件中的 JSON 语法错误。")
                    logger.error("常见错误:")
                    logger.error("  1. 属性名必须用双引号括起来")
                    logger.error("  2. 最后一个属性后不能有逗号")
                    logger.error("  3. 字符串值必须用双引号，不能用单引号")
                    logger.error("  4. 检查是否有未匹配的大括号或方括号")
                    raise
        except Exception as e:
            logger.error(f"加载配置文件时发生错误: {e}")
            raise


def sync_existing_files(source_dir, protocol):
    """同步已存在的文件（首次运行或重新连接时）"""
    logger.info("开始同步现有文件...")
    source_path = Path(source_dir)
    
    if not source_path.exists():
        logger.warning(f"源目录不存在: {source_dir}")
        return
    
    file_count = 0
    
    for root, dirs, files in os.walk(source_dir):
        for file in files:
            file_path = Path(root) / file
            relative_path = file_path.relative_to(source_dir)
            remote_path = str(relative_path).replace('\\', '/')
            
            try:
                # 确保目标目录存在
                protocol.ensure_directory(remote_path)
                
                # 检查是否需要同步
                remote_stat = protocol.get_file_stat(remote_path)
                if remote_stat:
                    local_stat = file_path.stat()
                    # 比较修改时间
                    if remote_stat.st_mtime >= local_stat.st_mtime:
                        logger.debug(f"文件已是最新，跳过: {relative_path}")
                        continue
                
                # 同步文件
                logger.info(f"正在同步: {relative_path}")
                protocol.upload_file(str(file_path), remote_path)
                
                # 验证文件大小（如果协议支持）
                try:
                    local_size = file_path.stat().st_size
                    remote_stat = protocol.get_file_stat(remote_path)
                    if remote_stat and hasattr(remote_stat, 'st_size'):
                        remote_size = remote_stat.st_size
                        if local_size == remote_size:
                            logger.info(f"✓ 文件同步成功: {relative_path} ({local_size} 字节)")
                        else:
                            logger.warning(f"文件大小不匹配: {relative_path} - 本地: {local_size}, 远程: {remote_size}")
                    else:
                        logger.info(f"✓ 文件同步成功: {relative_path} ({local_size} 字节)")
                except Exception as verify_error:
                    logger.warning(f"无法验证文件大小: {relative_path} - {verify_error}")
                
                file_count += 1
                if file_count % 10 == 0:
                    logger.info(f"已同步 {file_count} 个文件...")
                    
            except Exception as e:
                logger.error(f"同步文件失败 {relative_path}: {e}")
                import traceback
                logger.error(traceback.format_exc())
    
    logger.info(f"现有文件同步完成，共同步 {file_count} 个文件")


def main():
    """主函数"""
    logger.info("=== Windows文件同步服务启动 ===")
    
    # 加载配置
    config = load_config()
    if not config:
        return
    
    source_dir = config['source_dir']
    target_dir = config.get('target_dir', '')
    protocol_type = config.get('protocol', 'smb').lower()
    protocol_config = config.get(protocol_type, {})
    
    # 验证源目录
    if not Path(source_dir).exists():
        logger.error(f"源目录不存在: {source_dir}")
        return
    
    logger.info(f"源目录: {source_dir}")
    logger.info(f"目标目录: {target_dir}")
    logger.info(f"协议类型: {protocol_type.upper()}")
    
    # 创建协议适配器
    try:
        protocol = create_protocol(protocol_type, protocol_config, target_dir=target_dir)
    except Exception as e:
        logger.error(f"创建协议适配器失败: {e}")
        return
    
    # 初始化协议连接
    try:
        protocol.connect()
    except Exception as e:
        logger.error(f"{protocol_type.upper()}连接失败: {e}")
        logger.error("请检查服务器地址、用户名和密码")
        return
    
    # 首次同步现有文件
    try:
        sync_existing_files(source_dir, protocol)
    except Exception as e:
        logger.error(f"初始同步失败: {e}")
    
    # 获取同步方向和删除同步配置
    sync_direction = config.get('sync_direction', 'local-to-nas').lower()
    if sync_direction not in ['two-way', 'local-to-nas', 'nas-to-local']:
        logger.warning(f"未知的同步方向: {sync_direction}，使用默认值 local-to-nas")
        sync_direction = 'local-to-nas'
    
    sync_deletes = config.get('sync_deletes', False)
    
    # 显示同步配置信息
    direction_names = {
        'two-way': '双向同步',
        'local-to-nas': '本地到NAS',
        'nas-to-local': 'NAS到本地'
    }
    logger.info(f"同步方向: {direction_names.get(sync_direction, sync_direction)}")
    logger.info(f"同步删除操作: {'是' if sync_deletes else '否（数据保护）'}")
    
    # 创建事件处理器和观察者
    try:
        event_handler = SyncHandler(source_dir, target_dir, protocol, 
                                   sync_direction=sync_direction, 
                                   sync_deletes=sync_deletes)
        observer = Observer()
        observer.schedule(event_handler, source_dir, recursive=True)
        
        # 启动观察者
        observer.start()
        logger.info("文件监控已启动，开始实时同步...")
        
        # 双向同步或NAS到本地模式下，定期检查远程变化
        if sync_direction in ['two-way', 'nas-to-local']:
            last_check = time.time()
            check_interval = 30  # 每30秒检查一次远程变化
            logger.info(f"已启用远程监控，每 {check_interval} 秒检查一次NAS变化")
        else:
            check_interval = None
        
        try:
            # 保持运行
            while True:
                time.sleep(1)
                
                # 定期检查远程变化
                if check_interval and sync_direction in ['two-way', 'nas-to-local']:
                    current_time = time.time()
                    if current_time - last_check >= check_interval:
                        event_handler._check_remote_changes()
                        last_check = current_time
                        
        except KeyboardInterrupt:
            logger.info("收到停止信号，正在关闭...")
            observer.stop()
        
        observer.join()
        protocol.disconnect()
    except Exception as e:
        logger.error(f"运行错误: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        try:
            protocol.disconnect()
        except:
            pass
    
    logger.info("=== 文件同步服务已停止 ===")


if __name__ == '__main__':
    main()
