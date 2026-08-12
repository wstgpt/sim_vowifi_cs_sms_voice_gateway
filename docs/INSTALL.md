# 安装与升级

## 支持环境

- 推荐 ARM64 Debian、Ubuntu 或 Armbian，systemd 可用。
- Docker、USB、内核 TUN、pcscd；蜂窝模块还需要 ModemManager/NetworkManager。
- 已实机验证的三体电子 SCR Prime（`04d9:c001`）提供标准 CCID 接口，但尚未进入 libccid 1.6.2 的设备表。连接该型号时执行 `sudo ./install.sh patchprime`，安装程序会从校验过的固定版本源码构建驱动并加入设备匹配；完成后支持热插拔。
- 至少 4 GB 可用磁盘。首次构建 Asterisk 在低功耗设备上可能需要 20–30 分钟。
- 全新 Engine 构建会按固定提交从 `gitea.sysmocom.de` 获取 sysmocom 的 pjproject 与
  Asterisk 源码。安装主机必须能通过 HTTPS 访问该站点；部分云服务商网络可能被上游
  拒绝，此时请在可访问该站点的可信 ARM64 构建机完成构建，或使用已经审核的
  `MDD_ENGINE_BASE_IMAGE`，不要绕过 TLS 验证。

## 安装

```bash
sudo ./install.sh install                 # 原生控制面 + Docker 引擎
sudo ./install.sh install --mode docker   # 控制面也运行在 Docker
```

可用环境变量：`MDD_PORT`、`MDD_DATA_DIR`、`MDD_BIND`、`MDD_ADVERTISE_ADDR`、`MDD_SINGBOX_VERSION`、`MDD_LPAC_VERSION`。更换固定依赖版本时必须同步审核并更新 SHA-256。离线迁移时可显式设置 `MDD_ENGINE_BASE_IMAGE`，从本机已审核的兼容引擎镜像创建只覆盖 MDD 运行脚本与模板的镜像；已经在可信构建机完成 `npm ci && npm run build` 时，也可设置 `MDD_REUSE_WEBUI=1` 复用随源码传入的 `webui/dist`。全新在线安装不要设置这两项，仍执行完整源码构建。

`MDD_DATA_DIR` 在首次安装后会写入系统状态；后续执行 `status`、`reload` 和 `uninstall` 时不必再次填写，避免自定义数据目录被误判为新安装。

如果系统 Docker 已经可以连接，安装脚本只复用它，不升级版本、不修改 daemon 配置、不执行 prune，也不操作其他项目的容器或镜像。MDD 容器带有归属标签；发现同名外部容器、8443 端口冲突或 rootless Docker 时会停止并给出错误。蜂窝与 TUN/PCSC 引擎需要系统级 Docker daemon，因此不支持 rootless 模式。

版本检查始终使用 GitHub 公共 Release API，不读取或发送 GitHub Token。仓库仍为私有或尚未发布 Release 时，界面显示“尚无公开发布版本”；仓库与 Release 公开后即可直接检查。

安装完成后，在受信的局域网或 VPN 中立即打开 `https://主机地址:8443`，创建至少 10 字符的管理员密码。首次设置完成前，任何能访问该端口的客户端都可申领初始管理员。配置自有证书时，证书和私钥应只允许 root 读取。运行数据目录默认为 `0700`，凭据文件为 `0600`。

## 更新

发现新版本时，左下角版本号会出现红点。点击后确认“立即升级”即可一键更新：控制面把请求写入编排器目录，主机上的 `mdd-sim-gateway-orchestrator` 以独立的临时 systemd 单元（`mdd-sim-gateway-update`）运行 `host/mdd_update.py` —— 下载对应 `vX.Y.Z` Release 资产、校验 SHA-256、版本和发行版类型，备份当前代码到数据目录 `backups/` 后覆盖安装，最后复用资产内已构建的 WebUI 并执行 `install.sh reload --no-engines` 重启控制服务。升级绝不会自动发生：只有管理员在界面中确认后才开始，且 `data/`、`.env`、`.git`、虚拟环境和已有 Engine 镜像、容器全部保留。Engine 变更仍按本项目的构建机部署流程单独发布。日志见 `journalctl -u mdd-sim-gateway-update` 与数据目录下 `update/reload.log`。

“系统设置 → 备份与更新”可为版本检查和升级下载选择联网方式：默认直连、手动 HTTP/HTTPS/SOCKS5 代理，或复用已就绪的国家出口。选择 SOCKS5 时建议使用 `socks5h://`，使 DNS 解析也通过代理。手动代理凭据仅保存在主机权限为 `0600` 的配置/临时文件中，不写入 systemd 命令行或升级状态。一键升级会把同一代理环境传给 `install.sh reload`；Docker daemon 自身的镜像代理仍属于独立的主机配置。
正式 Release 归档包内含 CI 预构建的 `webui/dist`，一键升级校验整个归档后直接复用，因此不需要在树莓派上下载 Node 镜像或编译前端。完整版和公开版的 `EDITION` 必须一致；完整版不会接受 GitHub 公开版归档。

也可以随时在主机上手动更新：备份并用受信任来源更新源码后执行：

```bash
sudo ./install.sh reload --engines
```

该方式保留数据并重建依赖与引擎（一键升级不重建引擎镜像；需要重建引擎时使用上述命令）。

正式发布前请逐项完成 [发布检查清单](RELEASE_CHECKLIST.md)。推送与 `VERSION` 一致的 `vX.Y.Z` 标签后，Release 工作流会运行全套测试，并生成带 SHA-256 校验文件的源码包。

## 卸载

`sudo ./install.sh uninstall` 保留数据；`--purge` 会删除运行数据与虚拟环境，无法恢复。卸载只移除确认属于 MDD 的容器；Docker 本身及其他项目不受影响。
