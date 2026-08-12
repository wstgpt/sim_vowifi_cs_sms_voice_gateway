# MDD Sim Gateway 整合版 - 部署说明

**源码路径**: `/home/sim_vowifi_cs_sms_voice_gateway`  
**版本**: 基于 MDD Sim Gateway v1.3.3  
**整合功能**: Telegram 远程控制 + 无 SIM 数量限制

---

## 一、架构说明

本项目是容器化架构，需要 Docker 运行：

```
┌─────────────────────────────────────────────────────────────┐
│                     Host (aarch64 Debian)                   │
│                                                             │
│  ┌──────────────────────┐    ┌──────────────────────────┐  │
│  │  Control Plane       │    │  Engine Containers       │  │
│  │  (FastAPI + React)   │◄──►│  (per-SIM VoWiFi 引擎)   │  │
│  │                      │    │                          │  │
│  │  - HTTPS WebUI 8443  │    │  - SWu IKEv2/IPsec      │  │
│  │  - REST API          │    │  - Asterisk IMS          │  │
│  │  - Telegram Bot      │    │  - USIM 桥接             │  │
│  │  - ModemManager      │    │                          │  │
│  └──────────┬───────────┘    └──────────┬───────────────┘  │
│             │                            │                  │
│  ┌──────────▼────────────────────────────▼───────────────┐  │
│  │  Host Orchestrator (mdd_orchestrator.py)               │  │
│  │  - VPCD 桥接调制解调器 SIM 槽                          │  │
│  │  - sing-box TUN (国家出口)                             │  │
│  │  - USB 设备管理                                        │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  硬件层                                               │  │
│  │  - QMI 模组 (EC20/EC25/410WiFi)                       │  │
│  │  - USB PC/SC 读卡器                                   │  │
│  │  - ModemManager + NetworkManager                      │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、系统要求

- **架构**: aarch64 (ARM64)
- **OS**: Debian 12+ / Ubuntu 22.04+ / Armbian
- **Docker**: 20.10+
- **内核**: 支持 TUN/TAP + IPsec (XFRM)
- **USB**: 用于连接调制解调器/读卡器
- **网络**: 稳定互联网连接（下载依赖）

---

## 三、安装 Docker

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | sh

# 添加用户到 docker 组
sudo usermod -aG docker $USER
newgrp docker

# 启动 Docker
sudo systemctl enable docker
sudo systemctl start docker

# 验证
docker --version
docker info | grep "Server Version"
```

---

## 四、安装系统依赖

```bash
sudo apt update
sudo apt install -y \
    docker.io \
    pcscd \
    modemmanager \
    network-manager \
    libccid \
    git \
    curl \
    wget
```

---

## 五、编译和部署

### 5.1 克隆源码（如需要）

```bash
cd /home
sudo rm -rf sim_vowifi_cs_sms_voice_gateway 2>/dev/null || true
sudo mkdir -p sim_vowifi_cs_sms_voice_gateway
sudo chown $USER:$USER sim_vowifi_cs_sms_voice_gateway

# 或者直接使用已整合的版本
cd /home/sim_vowifi_cs_sms_voice_gateway
```

### 5.2 运行安装脚本

```bash
# 进入项目目录
cd /home/sim_vowifi_cs_sms_voice_gateway

# 安装（自动检测 aarch64 + Debian）
sudo ./install.sh install
```

安装脚本会自动：
1. 检查并安装 Docker
2. 安装 pcscd、ModemManager、NetworkManager
3. 按架构下载 sing-box 1.13.15
4. 编译 lpac 2.3.0（eSIM 支持）
5. 构建 Control Plane Docker 镜像
6. 构建 Engine Docker 镜像（需要 10-15 分钟）
7. 安装 systemd 服务并启动

### 5.3 查看状态

```bash
sudo ./install.sh status
sudo ./install.sh logs
```

### 5.4 首次访问

打开浏览器访问：
```
https://<服务器IP>:8443
```

接受自签名证书，创建管理员账号。

---

## 六、配置 Telegram 机器人

### 6.1 创建 Bot

1. 在 Telegram 搜索 `@BotFather`
2. 发送 `/newbot`
3. 按提示设置 Bot 名称
4. 保存获得的 Token（格式：`123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`）

### 6.2 获取 Chat ID

1. 搜索 `@userinfobot`
2. 发送任意消息
3. 获取你的 Chat ID（数字，如 `987654321`）

### 6.3 在 WebUI 配置

1. 登录 WebUI (https://<IP>:8443)
2. 进入 Settings → Telegram
3. 填写：
   - `Bot Token`: 从 BotFather 获取
   - `Chat ID`: 你的 Telegram Chat ID
   - `Commands Allowed Chat IDs`: 同上（或多个 ID 用逗号分隔）
   - `Enabled`: 开启
4. 保存配置

### 6.4 测试

在 Telegram 中发送：
```
/status
```

应该收到网关状态回复。

---

## 七、配置 Telegram 命令

支持的命令：

```
/status          — 查看网关和线路状态
/lines           — 列出所有线路 ID
/sms <line> <num> <text> — 发送短信
/call <line> <num>          — 拨打电话
/hangup <line>               — 挂断电话
/messages <line> [count]     — 查看短信历史
/calls <line> [count]        — 查看通话记录
/help                        — 显示帮助
```

**智能回复**: 收到短信通知后，直接在 Telegram 回复即可回信。

---

## 八、配置 VoWiFi 线路

### 8.1 插入 SIM

将 SIM 卡插入 USB 读卡器或 QMI 模组的 SIM 槽。

### 8.2 自动识别

WebUI 会自动检测 SIM 并创建草稿线路，显示：
- IMSI / ICCID
- MCC / MNC
- 运营商名称
- SIM 状态

### 8.3 配置线路

点击线路 → Provision：
1. 输入 SIM PIN（如果有）
2. 设置 IMEI（可选，会自动生成）
3. 选择国家出口（如有 Clash 订阅）
4. 点击 Start

### 8.4 查看状态

状态流程：
```
NO_CARD → PIN_PROBLEM → EPDG_UNRESOLVED → TUNNEL_DOWN → REGISTERING → OK
```

---

## 九、Telegram 远程控制演示

### 9.1 查看状态

```
/status
```

回复示例：
```
MDD Sim Gateway v1.3.3
2 line(s) configured
• 234-33 (CTExcel UK) [a1b2c3] — OK
• 262-01 (Vodafone DE) [d4e5f6] — REGISTERING
```

### 9.2 发送短信

```
/sms 234-33 +447700900123 Hello from remote
```

### 9.3 回复短信通知

收到通知：
```
[Incoming SMS] From: +447700900123
Line: 234-33
Message: "Your code is 123456"
```

直接回复：
```
Thanks!
```

系统会自动通过对应线路回复。

---

## 十、故障排查

### 10.1 查看日志

```bash
# Control plane 日志
sudo ./install.sh logs

# Engine 日志
docker logs mdd-sim-gateway-engine-1

# Host orchestrator 日志
journalctl -u mdd-sim-gateway-orchestrator -f
```

### 10.2 重启服务

```bash
sudo ./install.sh restart
```

### 10.3 重新编译

```bash
sudo ./install.sh reload --no-cache
sudo ./install.sh reload --engines
```

### 10.4 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| Telegram 无响应 | Bot Token 错误 | 检查 Settings → Telegram |
| Chat ID 未授权 | allowed_chat_ids 未配置 | 添加你的 Chat ID |
| SIM 无法识别 | pcscd 未运行 | `sudo systemctl restart pcscd` |
| 隧道无法建立 | ePDG 不可达 | 检查网络/防火墙 UDP 500/4500 |
| 编译失败 | 网络问题 | 检查 GitHub/sysmocom 访问 |

---

## 十一、目录结构

```
/home/sim_vowifi_cs_sms_voice_gateway/
├── control/
│   ├── app/
│   │   ├── main.py          # FastAPI 主应用 (4882 行)
│   │   ├── config.py        # 配置管理 (931 行)
│   │   ├── telegram_bot.py  # Telegram 机器人 (574 行) [新增]
│   │   ├── engine.py        # Docker 引擎管理
│   │   ├── sim.py           # SIM 识别
│   │   ├── store.py         # 持久化存储
│   │   └── ...
│   ├── run.py               # 启动脚本
│   ├── requirements.txt     # Python 依赖
│   └── Dockerfile           # Control 镜像
├── engine/
│   ├── swu_ike.py           # SWu IKEv2 隧道 (6511 行)
│   ├── pin_keeper.py        # PIN 保活
│   ├── ami_usim.py          # USIM↔AMI 桥接
│   └── Dockerfile           # Engine 镜像
├── host/
│   ├── mdd_orchestrator.py  # 主机编排器
│   ├── vpcd_modem_bridge.py # VPCD 桥接
│   └── mdd_update.py        # 自动更新
├── webui/
│   ├── src/                 # React 前端
│   └── package.json         # Node.js 依赖
├── install.sh               # 安装脚本
├── INTEGRATION.md           # 整合说明
└── VERIFICATION.md          # 验证说明
```

---

## 十二、后续步骤

1. 安装 Docker
2. 运行 `sudo ./install.sh install`
3. 访问 https://<IP>:8443
4. 配置 Telegram 机器人
5. 插入 SIM 并配置线路
6. 测试 Telegram 远程控制

---

## 十三、版本信息

| 组件 | 版本 |
|------|------|
| MDD Sim Gateway | v1.3.3 (整合版) |
| Python | 3.12 |
| FastAPI | 0.141.1 |
| React | 18.3.1 |
| sing-box | 1.13.15 |
| lpac | 2.3.0 |
| pcsc-lite | 2.3.3 |
