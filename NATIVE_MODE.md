# MDD Sim Gateway - Native Engine Mode

**版本**: v1.3.3 整合版  
**新增功能**: Native 引擎模式（无需 Docker 运行引擎）

---

## 一、模式对比

| 特性 | Docker 模式（默认） | Native 模式 |
|------|---------------------|-------------|
| Asterisk | 编译自源码（~10-15分钟） | 使用系统包（秒级安装） |
| Docker 需求 | 控制面 + 引擎 | 可选（仅控制面需要） |
| 内存占用 | ~500MB+ per SIM | ~200MB per SIM |
| 磁盘占用 | ~2GB+ | ~500MB |
| 隔离性 | 容器隔离 | 系统进程 |
| 推荐场景 | 服务器、云部署 | 小主机、边缘设备 |

---

## 二、Native 模式安装

### 2.1 环境要求

```bash
# Debian/Ubuntu
sudo apt update
sudo apt install -y \
    asterisk asterisk-pjsip asterisk-mp3 \
    pcscd libpcsclite-dev \
    modemmanager network-manager \
    python3 python3-pip python3-venv
```

### 2.2 安装步骤

```bash
cd /home/sim_vowifi_cs_sms_voice_gateway

# 方式1: 使用环境变量（推荐）
export MDD_ENGINE_MODE=native
sudo ./install.sh install

# 方式2: 使用 .env 文件
cat > .env <<EOF
MDD_ENGINE_MODE=native
EOF
sudo ./install.sh install

# 方式3: 直接指定
MDD_ENGINE_MODE=native sudo ./install.sh install
```

### 2.3 安装输出

```
==> MDD Sim Gateway install — repo: /home/sim_vowifi_cs_sms_voice_gateway  (mode: local)
==> using NATIVE engine mode (no Docker required for engines)
==> installing Asterisk from system packages…
==> Asterisk installed
==> installing sing-box 1.13.15 (arm64)…
==> host pcscd already at pinned version 2.3.3
==> ...
==> install complete (mode: local, engine: native)
   WebUI:   https://192.168.1.100:8443
   Data:    /home/sim_vowifi_cs_sms_voice_gateway/data
   Control: native systemd service (mdd-sim-gateway-control); engines run natively
```

---

## 三、架构差异

### 3.1 Docker 模式

```
┌────────────────────────────────────────┐
│  Host                                  │
│  ┌──────────────┐    ┌───────────────┐ │
│  │ Control      │    │ Engine 1      │ │
│  │ (Docker)     │◄──►│ (Asterisk)    │ │
│  └──────────────┘    └───────────────┘ │
│  ┌──────────────┐    ┌───────────────┐ │
│  │              │    │ Engine 2      │ │
│  │ Orchestrator │◄──►│ (Asterisk)    │ │
│  └──────────────┘    └───────────────┘ │
└────────────────────────────────────────┘
```

### 3.2 Native 模式

```
┌────────────────────────────────────────┐
│  Host                                  │
│  ┌──────────────────────────────────┐  │
│  │ Control Plane (FastAPI + WebUI)  │  │
│  └──────────────┬───────────────────┘  │
│                 │                      │
│  ┌──────────────▼───────────────────┐  │
│  │ Engine 1 (pin_keeper + swu_ike + │  │
│  │           Asterisk)              │  │
│  └──────────────▲───────────────────┘  │
│                 │                      │
│  ┌──────────────┴───────────────────┐  │
│  │ Engine 2 (same structure)        │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
```

---

## 四、配置文件

### 4.1 .env 文件

```bash
# 引擎模式: docker | native
MDD_ENGINE_MODE=native

# WebUI 端口
MDD_PORT=8443

# 数据目录
MDD_DATA_DIR=/home/sim_vowifi_cs_sms_voice_gateway/data

# 绑定地址
MDD_BIND=0.0.0.0
```

### 4.2 运行时配置

在 WebUI 的 Settings 中也可以配置：

```yaml
settings:
  engine_mode: native  # docker | native
```

---

## 五、功能保留

Native 模式完全保留以下功能：

- ✅ Telegram 远程控制
- ✅ 无 SIM 数量限制
- ✅ sing-box 国家出口
- ✅ eSIM/lpac 管理
- ✅ 蜂窝电话/短信
- ✅ VoWiFi 注册
- ✅ USB PC/SC 读卡器支持

---

## 六、限制说明

### 6.1 Asterisk 版本

Native 模式使用系统 Asterisk 包，版本由发行版决定：

| 发行版 | Asterisk 版本 |
|--------|---------------|
| Ubuntu 22.04 | 20.x |
| Debian 12 | 20.x |
| Armbian | 根据内核版本 |

**注意**: 系统版 Asterisk 可能缺少 VoWiFi 的 IMS 补丁。如果遇到问题，请使用 Docker 模式。

### 6.2 功能差异

- ❌ 某些调试功能仅在 Docker 模式可用
- ❌ 容器日志查看方式不同
- ✅ 核心功能（注册、通话、短信）完全一致

---

## 七、故障排查

### 7.1 Asterisk 未启动

```bash
# 检查 Asterisk 状态
sudo systemctl status asterisk

# 手动启动
sudo systemctl start asterisk

# 查看状态
asterisk -rx "core show status"
```

### 7.2 PC/SC 问题

```bash
# 检查 pcscd
sudo systemctl status pcscd

# 手动启动
sudo systemctl start pcscd

# 查看读卡器
pcsc_scan
```

### 7.3 查看引擎日志

```bash
# Native 模式日志位置
ls -la data/instances/*/logs/

# Asterisk 日志
tail -f data/instances/*/logs/asterisk.log

# IKE 隧道日志
tail -f data/instances/*/run/charon.log
```

---

## 八、性能对比

### 8.1 内存占用（单卡）

| 组件 | Docker 模式 | Native 模式 |
|------|-------------|-------------|
| 控制面 | ~150MB | ~150MB |
| 引擎 | ~300MB | ~200MB |
| Asterisk | (in container) | ~150MB |
| **总计** | **~450MB** | **~300MB** |

### 8.2 磁盘占用

| 组件 | Docker 模式 | Native 模式 |
|------|-------------|-------------|
| 控制镜像 | ~600MB | 无 |
| Engine 镜像 | ~1.5GB | 无 |
| Asterisk 包 | (in image) | ~200MB |
| **总计** | **~2.1GB** | **~200MB** |

---

## 九、迁移指南

### 9.1 从 Docker 模式切换到 Native 模式

```bash
# 1. 备份当前配置
cp -r data data.backup

# 2. 停止服务
sudo ./install.sh stop

# 3. 切换模式
export MDD_ENGINE_MODE=native
sudo ./install.sh install

# 4. 恢复配置（如需要）
# cp data.backup/config.yaml data/
# cp data.backup/auth.json data/

# 5. 重启
sudo ./install.sh restart
```

### 9.2 从 Native 模式切换回 Docker 模式

```bash
export MDD_ENGINE_MODE=docker
sudo ./install.sh install
```

---

## 十、常见问题

### Q: Native 模式是否稳定？

A: 核心功能稳定，但 Asterisk 版本可能缺少某些 VoWiFi 补丁。建议先测试基本注册功能。

### Q: 为什么 Asterisk 无法注册？

A: 系统版 Asterisk 可能不支持 IMS-AKA。检查版本：
```bash
asterisk -rx "core show version"
```
如果版本 < 20，建议升级到 Ubuntu 22.04+ 或使用 Docker 模式。

### Q: 如何查看引擎日志？

A: 
```bash
# Asterisk 日志
tail -f data/instances/*/logs/asterisk.log

# IKE 日志
tail -f data/instances/*/run/charon.log
```

---

## 十一、部署示例

### 11.1 410WiFi 小主机（推荐 Native 模式）

```bash
# 1. 克隆仓库
git clone https://github.com/wstgpt/sim_vowifi_cs_sms_voice_gateway.git
cd sim_vowifi_cs_sms_voice_gateway

# 2. 安装依赖
sudo apt install -y asterisk asterisk-pjsip pcscd modemmanager

# 3. 配置环境变量
cat > .env <<EOF
MDD_ENGINE_MODE=native
MDD_PORT=8443
EOF

# 4. 安装
sudo ./install.sh install

# 5. 查看状态
sudo ./install.sh status
```

### 11.2 云端服务器（可用 Docker 模式）

```bash
# 无需特殊配置，默认使用 Docker 模式
sudo ./install.sh install
```
