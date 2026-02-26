# 大文件断点续传设计文档

## 概述

解决当前系统传输大文件时的两个核心问题：
1. **内存溢出**：接收端将整个文件累积在内存中，大文件导致崩溃
2. **传输超时**：Socket 超时设置过短，大文件传输中途失败

同时引入断点续传和自动重连功能。

## 需求总结

| 需求 | 描述 |
|------|------|
| 内存安全 | 大文件边接收边写入磁盘，不累积在内存 |
| 断点续传 | 双方持久化传输状态，程序重启后可继续 |
| 自动重连 | 检测断开后自动重连并续传 |
| 信任设备 | 首次配对后记住设备，重连无需配对码 |
| IP 变化处理 | 设备标识不依赖 IP，支持局域网 IP 变化 |

---

## 1. 设备标识与信任机制

### 1.1 设备标识

由于 IP 可能变化，使用稳定的设备标识：

```
设备标识 = 设备名称 + 用户名 + 随机UUID（首次启动生成并持久化）
```

### 1.2 信任设备存储

文件路径：`{download_dir}/.lan_share/trusted_devices.json`

```json
{
  "devices": [
    {
      "device_id": "DESKTOP-ABC-username-550e8400-e29b-41d4-a716-446655440000",
      "hostname": "DESKTOP-ABC",
      "last_ip": "192.168.1.100",
      "trusted_at": "2026-02-23T10:00:00",
      "last_seen": "2026-02-23T12:00:00"
    }
  ]
}
```

### 1.3 重连验证流程

1. 连接时发送自己的 `device_id`
2. 对方检查是否在信任列表中
3. 若信任 → 跳过配对码验证，检查是否有未完成传输
4. 若不信任 → 要求配对码验证，验证通过后加入信任列表

---

## 2. 传输状态持久化

### 2.1 发送端状态文件

路径：`{download_dir}/.lan_share/sending/{file_hash}.json`

```json
{
  "file_path": "/path/to/large_file.zip",
  "file_name": "large_file.zip",
  "file_size": 1073741824,
  "file_hash": "abc123...",
  "chunk_size": 65536,
  "total_chunks": 16384,
  "sent_chunks": [0, 1, 2, 5, 6, 7],
  "receiver_device_id": "DEVICE-ID-...",
  "created_at": "2026-02-23T10:00:00",
  "updated_at": "2026-02-23T10:30:00"
}
```

### 2.2 接收端状态文件

路径：`{download_dir}/.lan_share/receiving/{file_hash}.json`

```json
{
  "file_name": "large_file.zip",
  "file_size": 1073741824,
  "file_hash": "abc123...",
  "chunk_size": 65536,
  "total_chunks": 16384,
  "received_chunks": [0, 1, 2, 5, 6, 7],
  "temp_file": "receiving/large_file.zip.abc123.part",
  "sender_device_id": "DEVICE-ID-...",
  "created_at": "2026-02-23T10:00:00",
  "updated_at": "2026-02-23T10:30:00"
}
```

### 2.3 临时文件管理

- 使用 `.part` 后缀存储未完成的文件
- 按块索引写入对应位置（随机写入）
- 完成后重命名为正式文件名

### 2.4 状态同步时机

- 每传输 50 个块更新状态文件
- 或每 5 秒（取先到者）
- 重连时双方交换 `received_chunks` 列表

---

## 3. 块传输协议

### 3.1 消息类型

| 类型 | 说明 |
|------|------|
| FILE_INFO | 文件信息（包含哈希、大小、块数） |
| FILE_DATA | 单块数据（块索引 + 数据） |
| FILE_ACK | 单块确认 |
| FILE_ACK_BATCH | 批量确认（每 50 块发送一次） |
| FILE_RESUME | 续传请求（发送已接收块列表） |
| FILE_RESUME_OK | 续传确认（发送需要重传的块列表） |
| FILE_COMPLETE | 传输完成确认 |
| HEARTBEAT | 心跳包 |

### 3.2 发送流程

```
1. 发送 FILE_INFO
2. 等待 FILE_ACK（接收端确认准备好）
3. 循环发送 FILE_DATA（每块）
   - 发送后等待 FILE_ACK
   - 超时则重试（最多 3 次）
   - 累积 ACK 每 50 块持久化一次状态
4. 发送 FILE_COMPLETE
5. 等待 FILE_COMPLETE 确认
6. 清理状态文件
```

### 3.3 接收流程

```
1. 收到 FILE_INFO → 创建临时文件，加载/创建状态
2. 若有历史状态 → 发送 FILE_RESUME（已接收块列表）
3. 收到 FILE_DATA → 写入临时文件，发送 FILE_ACK
4. 累积每 50 块持久化一次状态
5. 收齐所有块 → 重命名为正式文件，发送 FILE_COMPLETE
```

---

## 4. 内存安全接收机制

### 4.1 分块写入

```python
class ChunkedFileReceiver:
    def __init__(self, file_hash, file_size, chunk_size):
        self.temp_path = f"receiving/{file_hash}.part"
        # 预分配文件大小（稀疏文件，不占实际磁盘空间）
        with open(self.temp_path, 'wb') as f:
            f.truncate(file_size)

    def write_chunk(self, chunk_index: int, data: bytes):
        # 随机写入，不累积在内存
        with open(self.temp_path, 'r+b') as f:
            f.seek(chunk_index * self.chunk_size)
            f.write(data)
```

---

## 5. 自动重连机制

### 5.1 断线检测

- 发送端：`send()` 失败或 `ACK` 超时超过 3 次
- 接收端：消息循环超过 30 秒无数据

### 5.2 重连流程

```
[断线检测]
    ↓
[保存当前传输状态到磁盘]
    ↓
[启动重连线程]
    ↓
[尝试连接对方 IP] ←─── 失败 ───┐
    ↓ 成功                      │
[发送自己的 device_id]          │
    ↓                          │
[对方验证是否信任设备]          │
    ↓ 信任                     │
[交换未完成传输列表]            │
    ↓                          │
[发送 RESUME 请求]              │
    ↓                          │
[恢复传输]                      │
    ↓                          ↑
   完成                     失败重试
                             (最多5次，间隔5秒)
```

### 5.3 UDP 设备发现

当 IP 变化时，通过 UDP 广播发现设备：

```
1. 发送端广播: {"type": "discover", "target_device_id": "xxx"}
2. 局域网内所有设备收到后检查是否匹配
3. 匹配的设备响应: {"type": "discover_response", "device_id": "xxx", "ip": "192.168.1.105"}
```

---

## 6. Socket 超时优化

### 6.1 配置

```python
SOCKET_CONFIG = {
    'connect_timeout': 30,     # 连接超时 30 秒
    'recv_timeout': 60,        # 接收超时 60 秒（等待 ACK）
    'send_timeout': None,      # 发送不设超时，由 TCP 自己控制
}
```

### 6.2 发送超时处理

```python
def send_chunk_with_retry(sock, chunk_data, max_retry=3):
    for attempt in range(max_retry):
        try:
            sock.settimeout(None)  # 发送不限时
            sock.send(chunk_data)

            sock.settimeout(60)    # 等待 ACK 60秒
            ack = recv_ack(sock)
            return True
        except socket.timeout:
            if attempt < max_retry - 1:
                continue
            raise TransferError("ACK timeout after retries")
```

### 6.3 心跳机制

- 每 10 秒发送一次心跳
- 超过 30 秒无响应视为断开

---

## 7. 目录结构

### 7.1 代码结构

```
sq_lan_file_sharing/
├── config.py                 # 添加新配置项
├── file_handler.py           # 修改：添加分块写入
├── network/
│   ├── protocol.py           # 修改：添加新消息类型
│   ├── server.py             # 修改：支持信任设备、断点续传
│   ├── client.py             # 修改：支持信任设备、断点续传
│   ├── discovery.py          # 新增：UDP 设备发现
│   └── reconnect.py          # 新增：自动重连管理
├── transfer/
│   ├── __init__.py
│   ├── state_manager.py      # 新增：传输状态持久化
│   ├── chunk_sender.py       # 新增：分块发送器
│   └── chunk_receiver.py     # 新增：分块接收器（内存安全）
├── trust/
│   ├── __init__.py
│   ├── device_manager.py     # 新增：设备标识与信任管理
│   └── trusted_devices.json  # 新增：信任设备存储
└── ui/
    └── main_window.py        # 修改：适配新传输机制
```

### 7.2 数据目录结构

```
{download_dir}/
├── .lan_share/
│   ├── trusted_devices.json  # 信任设备
│   ├── device_id.json        # 本机设备标识
│   ├── sending/              # 发送中的状态
│   │   └── {file_hash}.json
│   └── receiving/            # 接收中的状态和临时文件
│       ├── {file_hash}.json
│       └── {file_hash}.part
└── (实际下载的文件)
```

---

## 8. 配置项

```python
# config.py 新增

# 传输配置
CHUNK_SIZE = 64 * 1024        # 64KB 块大小
ACK_TIMEOUT = 60              # ACK 超时秒数
MAX_RETRY = 3                 # 最大重试次数

# 重连配置
RECONNECT_INTERVAL = 5        # 重连间隔秒数
MAX_RECONNECT_ATTEMPTS = 5    # 最大重连次数

# 心跳配置
HEARTBEAT_INTERVAL = 10       # 心跳间隔秒数
HEARTBEAT_TIMEOUT = 30        # 心跳超时秒数

# 状态同步配置
STATE_SYNC_INTERVAL = 5       # 状态同步间隔秒数
CHUNKS_PER_SYNC = 50          # 每多少块同步一次状态

# UDP 发现配置
DISCOVERY_PORT = 9528         # UDP 发现端口
DISCOVERY_TIMEOUT = 5         # 发现超时秒数
```

---

## 9. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 状态文件损坏 | 使用原子写入（先写临时文件再重命名） |
| 磁盘空间不足 | 传输前检查可用空间 |
| 块写入乱序 | 使用块索引精确定位写入位置 |
| 恶意设备欺骗 | 首次连接必须配对码验证 |
