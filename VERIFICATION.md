# 验证脚本 - MDD Sim Gateway 整合版

## 修改内容

### 1. Telegram 远程控制 (新增)
- 新增 `control/app/telegram_bot.py` (574 行)
- 在 `main.py` 中添加 `TelegramActions` 类
- 在 `lifespan()` 中启动 Telegram bot 任务
- 在 `config.py` 中添加 `commands.allowed_chat_ids` 配置

### 2. 移除 SIM 数量限制
- 删除 `PUBLIC_MAX_SIM_LINES = 5` 常量
- 删除 `LineLimitError` 异常类
- 删除 `public_line_allowed()` 函数
- 移除 3 处异常处理

## 验证命令

```bash
# 语法检查
python3 -c "import ast; ast.parse(open('control/app/main.py').read())"
python3 -c "import ast; ast.parse(open('control/app/config.py').read())"
python3 -c "import ast; ast.parse(open('control/app/telegram_bot.py').read())"

# 导入检查
cd /home/sim_vowifi_cs_sms_voice_gateway
python3 -c "import sys; sys.path.insert(0, '.'); from control.app import config; print(config.DEFAULTS['settings']['telegram']['commands'])"
```

## 测试结果

- ✅ 语法检查: 全部通过
- ✅ Telegram bot 导入: 成功
- ✅ Config 配置: 正确
- ✅ SIM 限制移除: 完成

## 依赖安装

```bash
pip install -r control/requirements.txt
```
