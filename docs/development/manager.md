# 开发与验证

后端纯单元测试使用临时目录和模拟的 Docker 调用：

```bash
uv sync --project backend --group dev
backend/.venv/bin/python -B -m pytest -q -p no:cacheprovider backend/tests --ignore=backend/tests/test_live_roundtrip.py
```

`test_live_roundtrip.py` 依赖真实 Docker、运行中的管理台和专用测试实例，应单独阅读并配置环境后执行。`scripts/acceptance.sh` 会临时改写示例实例代理，仅适用于隔离测试环境。测试产生的 `evidence/`、截图和日志不提交。

前端构建包含 TypeScript 检查：

```bash
pnpm install --dir frontend --frozen-lockfile
pnpm --dir frontend run build
```

外层浏览器真实窗口自适应验证应使用 `viewport: null` 的专用测试上下文。不要在日常使用的管理台标签页上固定模拟视口；模拟尺寸变化无法证明原生窗口缩放行为。

所有新建的测试数据、实例配置和凭据均应留在 Git 忽略目录。公开提交前检查 `git diff --cached` 和 `git status --ignored`。
