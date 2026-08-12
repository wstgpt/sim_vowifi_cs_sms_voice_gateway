# MDD Sim Gateway Native 模式安装指南

## 服务器要求

- Ubuntu 24.04 LTS (aarch64/ARM64)
- 1GB+ RAM
- 8GB+ 存储
- 网络连接

## 安装步骤

### 1. 克隆仓库

```bash
git clone https://github.com/wstgpt/sim_vowifi_cs_sms_voice_gateway.git
cd sim_vowifi_cs_sms_voice_gateway
```

### 2. 配置 Native 模式

```bash
# 方式1: .env 文件
echo 'MDD_ENGINE_MODE=native' > .env

# 方式2: 环境变量
export MDD_ENGINE_MODE=native
```

### 3. 运行安装

```bash
sudo ./install.sh install
```

安装过程会自动：
- 安装 Asterisk 20.x（系统包）
- 编译 pcsc-lite 2.3.3
- 安装 sing-box 1.13.15
- 编译 lpac 2.3.0（eSIM 支持）
- 构建 WebUI
- 安装 Python 依赖
- 启动 systemd 服务

### 4. 查看状态

```bash
sudo ./install.sh status
```

### 5. 访问 WebUI

```
https://<服务器IP>:8443
```

接受自签名证书，创建管理员账号。

## 配置 Telegram 机器人

1. 在 Telegram 搜索 @BotFather
2. 发送 /newbot 创建机器人
3. 保存 Bot Token
4. 搜索 @userinfobot 获取你的 Chat ID
5. 在 WebUI 中配置：
   - Settings → Telegram
   - Bot Token: 填写从 BotFather 获取
   - Chat ID: 填写你的 Chat ID
   - Commands Allowed Chat IDs: 同上

## Telegram 命令

```
/status          - 查看网关状态
/lines           - 列出所有线路
/sms <line> <num> <text> - 发送短信
/call <line> <num>          - 拨打电话
/hangup <line>               - 挂断电话
/messages <line> [count]     - 短信历史
/calls <line> [count]        - 通话记录
/help                        - 帮助信息
```

## 服务管理

```bash
# 查看状态
sudo ./install.sh status

# 查看日志
sudo ./install.sh logs

# 重启服务
sudo ./install.sh restart

# 禁用自动启动
sudo ./install.sh disable-autostart

# 卸载
sudo ./install.sh uninstall
```

## 配置示例

编辑 `.env` 文件：

```bash
MDD_ENGINE_MODE=native
MDD_PORT=8443
MDD_DATA_DIR=/home/sim_vowifi_cs_sms_voice_gateway/data
MDD_BIND=0.0.0.0
```

## 故障排查

### Asterisk 未启动

```bash
sudo systemctl status asterisk
sudo systemctl start asterisk
```

### PC/SC 问题

```bash
sudo systemctl status pcscd
sudo systemctl start pcscd
pcsc_scan  # 查看读卡器
```

### 查看引擎日志

```bash
# Asterisk 日志
tail -f data/instances/*/logs/asterisk.log

# IKE 隧道日志
tail -f data/instances/*/run/charon.log
```

### 服务日志

```bash
journalctl -u mdd-sim-gateway-control -f
journalctl -u mdd-sim-gateway-orchestrator -f
```

## 已安装组件

| 组件 | 版本 | 来源 |
|------|------|------|
| Asterisk | 20.6.0 | 系统包 |
| pcsc-lite | 2.3.3 | 源码编译 |
| sing-box | 1.13.15 | 官方 release |
| lpac | 2.3.0 | 源码编译 |
| Python | 3.12 | 系统包 |
| FastAPI | 0.141.1 | pip |

## 系统资源占用

- 内存: ~150MB (控制面) + ~200MB/SIM (引擎)
- 磁盘: ~500MB (运行) + ~100MB (日志)
