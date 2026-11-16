# 启动管理台

需要 Apple Silicon Mac、Docker Desktop、Python 3.12+、Node.js、pnpm 和 uv。先按项目 README 准备 mihomo、图片剪贴板辅助程序和国家数据库，再运行：

```bash
cd claude-browser-containers
./scripts/init.sh
./scripts/setup.sh
./scripts/start-manager.sh
```

浏览器打开 http://127.0.0.1:5790 。在网页中添加自己的订阅或 SOCKS5 配置，然后创建实例。新安装不会自带任何实例、订阅或账号。

管理台启动时可登记已有实例，但不会自动重启它们。`init.sh` 保留已有实例配置与浏览器数据，不自动创建示例实例。脚本不会安装系统软件或修改系统代理。

如果页面提示尚未构建，重新运行 `./scripts/setup.sh`。Docker 不可用时，列表会显示“状态未知”。
