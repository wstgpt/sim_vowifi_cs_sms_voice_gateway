# MDD Sim Gateway 整合版说明

**源码路径**: `/home/sim_vowifi_cs_sms_voice_gateway`  
**基于**: MddIdd/mdd-sim-gateway v1.3.3  
**新增功能**: Telegram 远程控制 + 移除 SIM 数量限制

---

## 一、整合内容

### 1.1 Telegram 远程控制（来自 heyzg/mdd-sim-gateway）

新增文件：
- `control/app/telegram_bot.py` (574 行) — Telegram 双向控制机器人

修改文件：
- `control/app/main.py` — 添加 `TelegramActions` 类，集成 Telegram 命令处理
- `control/app/config.py` — 添加 `commands.allowed_chat_ids` 配置项

支持的 Telegram 命令：
```
/status          — 查看网关状态
/lines           — 列出所有线路
/sms <line> <num> <text> — 发送短信
/call <line> <num>          — 拨打电话
/hangup <line>               — 挂断电话
/messages <line> [count]     — 短信历史
/calls <line> [count]        — 通话记录
/help                        — 帮助信息
```

智能功能：
- 回复短信通知即可回信
- 命令过期时间 180 秒
- 只允许授权的 Chat ID 执行命令
- 每次操作写入审计日志

### 1.2 移除 SIM 数量限制

官方版限制公开版最多 5 条 SIM 线路，整合版移除了这个限制：

移除的代码：
- `config.py`: `PUBLIC_MAX_SIM_LINES = 5` 常量
- `config.py`: `LineLimitError` 异常类
- `config.py`: `public_line_allowed()` 函数
- `main.py`: 3 处 `LineLimitError` 异常处理

---

## 二、Telegram 配置

在 WebUI 的 Settings 中配置：

```yaml
settings:
  telegram:
    enabled: true
    bot_token: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"  # BotFather 获取
    chat_id: "987654321"  # 你的 Telegram Chat ID
    proxy_mode: "direct"  # direct | manual | country
    proxy_url: ""  # 手动代理 URL
    proxy_country: ""  # 国家出口代理
    commands:
      allowed_chat_ids: "987654321,123456789"  # 允许执行命令的 ID
    events:
      incoming_sms: true
      incoming_call: true
      activation_reminder: true
```

获取 Chat ID：
1. 在 Telegram 搜索 `@userinfobot`，发送任意消息
2. 它会回复你的 Chat ID（数字）

---

## 三、安全说明

整合后的版本：
- Telegram 命令需要授权 Chat ID 才能执行
- 不记录短信内容和电话号码到审计日志
- 命令过期时间 180 秒（防止延迟执行）
- 回复通知回信有严格的路由验证

**注意**: 公开版的合规限制（5 条 SIM）已被移除。请遵守当地法律法规。

---

## 四、代码统计

| 文件 | 行数 | 说明 |
|------|------|------|
| `main.py` | 4,882 | +60 行（TelegramActions + 启动逻辑） |
| `config.py` | 931 | +10 行（commands 配置） |
| `telegram_bot.py` | 574 | 新增文件 |
| **总计** | **13,151** | Python 代码 |

---

## 五、编译测试

```bash
cd /home/sim_vowifi_cs_sms_voice_gateway
python3 -c "import ast; ast.parse(open('control/app/main.py').read())" && echo "main.py OK"
python3 -c "import ast; ast.parse(open('control/app/config.py').read())" && echo "config.py OK"
python3 -c "import ast; ast.parse(open('control/app/telegram_bot.py').read())" && echo "telegram_bot.py OK"
```

所有文件语法检查通过。

---

## 六、使用方法

1. 部署到服务器
2. 安装依赖: `pip install -r control/requirements.txt`
3. 配置 Telegram bot token 和 chat ID
4. 启动服务: `python control/run.py`
5. 在 Telegram 中测试命令

---

## 七、与官方版对比

| 功能 | 官方版 | 整合版 |
|------|--------|--------|
| Telegram 通知 | ✅ | ✅ |
| Telegram 远程控制 | ❌ | ✅ |
| SIM 数量限制 | 5 条 | 无限制 |
| 蜂窝电话 | ✅ | ✅ |
| 蜂窝短信 | ✅ | ✅ |
| VoWiFi | ✅ | ✅ |
| 国家出口 | ✅ | ✅ |
| eSIM | ✅ | ✅ |
