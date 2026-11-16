# 安全边界与验证方式

本项目隔离的是容器进程、Profile、文件和代理网络。它不伪装设备指纹，也不等同于两台物理设备。Docker Engine、Linux VM、宿主 Mac 和能够访问 Docker Socket 的用户属于信任边界；本项目不向浏览器挂载 Docker Socket 或整个 Home。

## 必须保持的约束

- 所有宿主发布端口只绑定 `127.0.0.1`；只有 HTTP Web UI。VNC 5900 和代理 8080 不发布，防火墙拒绝外部连接。节点选择页 `panel.sh` 也只监听 `127.0.0.1:5790`，跑在 Mac 上，不放进浏览器容器；接口不返回节点密码或服务器地址。
- 浏览器无 privileged、host networking、NET_ADMIN、NET_RAW、SYS_ADMIN。守卫/代理在自己的 network namespace 中初始化规则，不改宿主防火墙。
- 浏览器新建出站流量只能是 TCP 到自己的 GOST。手动 SOCKS5 时，GOST 只能连接指定上游的 IPv4:端口。选择订阅节点时，GOST 只连接本容器的 `127.0.0.1:1080`，mihomo 只能连接所选节点服务器的固定 IPv4:端口。不导入订阅里的分流规则，因此没有 DIRECT。不转发 IP 包，无其他 UDP/DNS/IPv6 例外。
- 代理故障不改变规则、不选择 DIRECT、不选用其他实例。停掉守卫会同时失去健康的浏览器 namespace，应通过 `restart.sh` 恢复整实例。
- `proxy.json` 和运行时配置权限为 600、父目录 700，并由 `.gitignore` 排除；不要把项目 data/configs 的私密内容提交或同步到公开仓库。
- Web terminal、file manager 和不必要通知默认关闭；正常模式 CDP 关闭。
- 按本机使用需求启用文本剪贴板双向同步。只有打开的浏览器画面在浏览器允许剪贴板权限后参与同步；本机复制的文本可在容器粘贴，容器复制的文本可在本机粘贴。此通道不提供图片或文件传输，不读取宿主文件，也不增加宿主端口。

## Chromium Sandbox

固定版本官方镜像在自己的 SUID 探测失败时会追加 `--no-sandbox`。本机在 no-new-privileges 下复现该行为后，本项目挂载了独立参数脚本，使用实际的非特权 `unshare -U` 预检，并禁止这种降级。

`seccomp-default.json` 来自 [Moby v24.0.6](https://github.com/moby/moby/blob/v24.0.6/profiles/seccomp/default.json)（Apache-2.0）；`seccomp-chromium.json` 保留默认拒绝策略，只增加受 flags 限制的 user namespace unshare 和 user/PID/network namespace clone。mount/cgroup/UTS/IPC namespace 及 clone3 的默认限制保持；没有 seccomp=unconfined。

验收时实际读取 `chrome://sandbox`，应显示 Namespace、PID/Network namespaces、Seccomp-BPF 及 adequately sandboxed。仅看到 `/proc` 的 Seccomp=2 只能证明 Docker 的外层 seccomp，不能单独证明 Chromium 内层 Sandbox。代码还检查实际进程，发现 `--no-sandbox` 时停止实例。

## 自动化证据

`verify-isolation.sh` 对正在运行的实例做独立的正向 GOST 连接、非代理直连、跨实例、IPv6 和 GOST 停止测试。`acceptance.sh` 在空代理配置下使用短暂认证代理，补充错误密码、暂停上游、浏览器请求、存储、远端 DNS 协议和 UDP/DNS 抓包测试。输出 JSON、文本和 pcap 到 `evidence/`。

DNS 的肯定证据是上游收到目标域名 `ATYP=3`，否定证据是向 Docker DNS 和外部 DNS 的主动探测被拒绝、相关接口抓包没有 DNS/UDP 包。WebRTC 的证据限于指定 STUN 服务器、当前 Chromium 版本和当前策略下的 ICE candidates 及抓包结果。没有真实代理参数时，对真实提供商的 DNS/WebRTC 行为仍需复验，不能据此宣称所有环境绝无泄漏。

检查命令返回 FAIL 或异常时，应看具体证据并修复。空代理导致无法访问公网是预期的离线保护状态，不应标为真实代理验收成功。验收结果保存在本机忽略的 `evidence/` 目录，不随源码发布。

## 限制

- 本地 HTTP UI 默认无账号认证；该 Mac 上能连接 loopback 的其他程序/用户也可能操作浏览器。它不适合共享主机或远程公开访问。如需远程访问，另行设计 HTTPS、认证和访问控制，不要把绑定改成 0.0.0.0。
- 标准 SOCKS5 的 RFC1929 用户名密码认证本身不加密，账号凭据会通过 TCP 发给提供商。网站 HTTPS 仍端到端加密；上游应为可信服务或在可信加密隧道内使用。这是用户指定 SOCKS5 协议的边界。
- 目标网站可以看到代理出口和浏览器常规特征；未提供防指纹或账号风控规避功能。
- 项目为每个容器显式附加 seccomp；更换 Docker Engine、运行环境、镜像或网络后应重新验证。
- 本机访问检查与其他设备上的端口检查是两种验证方式，应分别记录结果。
- Profile 内包含 Cookie/登录状态，应私密备份。项目不会自动登录 Claude，也不会读取现有宿主浏览器的账号数据。
