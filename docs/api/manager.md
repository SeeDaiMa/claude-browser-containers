# 管理台 API

只接受 Host 为 `127.0.0.1:5790` 或 `localhost:5790`。除 GET 外必须带 `X-CBC-Request: 1`，并且 Origin 要么为空，要么是本机页面。没有 CORS。

- `GET /api/instances`：实例、回收站、Docker 状态、资源。`config_errors` 单独列出损坏配置；正常实例继续返回。实例的 `monitor` 包含自动恢复是否启用、故障提示、已尝试次数和下次允许重试的 Unix 时间（秒）。顶层 `monitor` 是监测服务状态。
- `GET /api/settings`：只读的管理台版本、资源上限、共用镜像名称和本机数据目录，不包含代理或订阅凭据。
- `POST /api/instances`：创建并启动。返回任务。输入、订阅引用、节点支持及上游解析检查在创建目录前完成；合法配置的后续启动失败会保留实例用于修正和重试。任一实例配置损坏时暂停分配新端口和子网。
- `POST /api/instances/{id}/start|stop|restart|trash|restore`
- `POST /api/instances/{id}/purge`：正文里的 `confirm_name` 必须等于显示名称，并且实例已经在回收站。
- 停止和移入回收站会检查 Docker 命令结果并确认实例组件已清理；无法连接 Docker、停止失败或仍有组件残留时返回失败，不写入回收站状态。启动后期失败也执行正常关闭和组件清理；无法正常关闭时保留写盘保护并报告收尾失败。Sandbox 校验失败先停止代理，避免等待关闭确认期间继续联网。
- 彻底删除开始后，实例公开字段 `deleting=true`。数据或配置清理失败时保留登记，可以按原 ID 和确认名称重试；已开始删除的实例不能恢复、启动或重启，只能继续清理。配置、数据和共享引用都清理完成后，最后移除实例登记。
- `PATCH /api/instances/{id}`：只改显示名称。
- `POST /api/instances/{id}/proxy`：保存代理，下次启动生效。只允许从未启动成功的实例（例如创建时代理连接失败）使用，并且实例必须已停止。实例第一次启动成功后写入 `proxy_locked_at`，之后返回 400“代理已锁定”。命令行选节点（`nodes.sh`、`use-node.sh`）已移除，节点只能在网页选择。
- `POST /api/instances/{id}/proxy/reconfirm`：锁定后唯一允许的代理操作。只在原订阅节点需要重新确认时（例如服务商更换了节点密码），在同一订阅里找唯一的同名节点重新绑定，下次启动生效。找不到或有多个同名节点时拒绝，不会换成别的节点。
- 已移除 `POST /api/instances/{id}/proxy/restore`（恢复上次成功配置）：代理锁定后不再有运行中切换。
- `POST /api/instances/{id}/exit`：由该实例自己的代理访问 `https://claude.ai/cdn-cgi/trace`，直接检测 Claude 看到的出口 IP。请求失败或 IP 无效时显示“Claude 出口未测得”，不使用其他检测站的出口代替 Claude。国家／地区 `country_code` 优先通过本地归属库识别，归属库无记录时保留 Claude trace 的国家信息；无法取得时为 `null`，不影响有效 IP 的检测结果。列表会在本地补全旧检测记录的国家，不向外部查询接口发送 IP。上游可能按网站分流，Cloudflare 官网、ipify 等站点仅用于独立排障，不能据此保证 Claude 使用相同 IP。
- `POST /api/instances/{id}/reload-page`：刷新容器内网页，不重连画面。出口检测、刷新网页、改名、节点重新确认和生命周期操作共用同实例操作锁；冲突的同步请求返回 400，提示稍后重试。长操作仍在任务队列中执行，不同实例的同步操作不共享长操作锁。
- `GET /api/subscriptions`、`POST /api/subscriptions`（返回后台任务，不在请求线程里下载）、`POST /api/subscriptions/{id}/refresh`、`DELETE /api/subscriptions/{id}`
- 添加订阅的名称和 HTTPS 地址在接受任务前校验，非法地址返回 400。下载与解析在受总期限约束的子进程执行：响应最多 2 MiB、最多 5000 个节点、总耗时 30 秒，解析后的 JSON 最多 8 MiB。添加/刷新使用独立订阅队列，不占用实例启停队列或全局生命周期锁；刷新失败仍保留旧缓存。损坏或缺少实例登记时，绑定文件内的订阅引用仍会阻止删除订阅。
- `GET /api/subscriptions/{id}/nodes`
- `POST /api/subscriptions/{id}/nodes/test`：正文可选 `{ "node_ids": ["节点 ID"] }`，省略时测试该订阅全部支持的节点。返回 `test-nodes` 后台任务，公开 `subscription_id` 和 `node_ids`。同一订阅同时只接受一个测速任务；网页任务按节点逐项检测并保存结果，使用独立队列和临时 Mihomo 容器，不占用实例生命周期队列，不修改实例代理。
- `POST /api/subscriptions/{id}/nodes/quality`：同样接受可选的 `node_ids`，返回 `check-node-quality` 后台任务。与测速共用独立队列和临时 Mihomo 容器；每个本地 HTTP 入口固定通过对应节点查询 IPPure，失败不回退到本机直连或其他节点。请求串行进行、节点间隔 2 秒，单次请求超时 12 秒。同订阅的质量任务去重，不修改实例代理和延迟结果。
- `PATCH /api/subscriptions/{id}`：正文 `{ "name": "新名称" }` 只改显示名称并立即返回公开名称。含 `url` 时先校验名称和 HTTPS 地址，返回独立订阅队列中的 `update-subscription` 后台任务，接受请求时不下载。更新保留订阅编号，仍能对上的实例绑定保持不变，消失的节点标记为需要重新确认，不重启实例。名称、地址、缓存及绑定更新失败会恢复原配置；下载期间被其他操作更改的订阅会拒绝过期提交。响应和公开任务不返回订阅地址。
- `GET /api/tasks`、`GET /api/tasks/{id}`。节点检测任务带 `progress`：`completed`、`total` 和当前节点名称 `label`。检测按节点逐个执行，每完成一个更新进度。
- `POST /api/tasks/{id}/cancel`：排队中的任务立即进入 `interrupted`。已经开始的节点测速或 IP 检测返回 `cancel_requested=true`，当前节点结束前仍保持 `running`，之后进入 `interrupted`，不再检测剩余节点。已完成节点结果逐项保存并保留，未检测节点不记为失败。取消不停止浏览器，也不退出后台队列工作线程。正在启动、重启或自动恢复的任务不能用此接口取消；自动恢复期间仍可提交停止，停止会更换运行代次并使恢复失效。

响应不含订阅 URL、密码、UUID 或密钥。重复的 `request_id` 也只返回公开任务。非法 JSON 返回 400，超过 16 KiB 返回 413。校验失败只返回“请求参数不正确。”

私有任务记录保存 request_id、输入摘要和原请求实例 ID，用于管理服务重启后的去重，不保存原始 payload 或密码。这些字段不公开给 API。任务快照与写入串行提交，避免旧状态覆盖新状态。去重范围沿用现有任务保留策略：最近 50 个已结束任务和所有活动任务；升级前未保存的去重字段无法补回。

订阅刷新时，同一个稳定 node_id 的名称会同步到已有绑定，连接配置变化仍需显式重新确认。固定节点 IPv4 时，TLS HTTP/SOCKS5、Trojan、AnyTLS、VMess 和 VLESS 会保留原隐含的 sni/servername，已有显式 TLS 名称保持原值。

节点的 `test` 为 `null`（未测速），或 `{ "status": "ok|failed", "delay_ms": 123, "detail": "公开说明", "checked_at": "ISO 时间" }`，失败时 `delay_ms` 为 `null`。使用 Mihomo `/proxies/{name}/delay` 测试经节点访问 `https://www.gstatic.com/generate_204` 的 HTTP 延迟，单节点超时 8 秒。该数值不是下载带宽，也不代表 Claude 访问状态。测速失败不隐藏节点或阻止原本支持的节点被选择。

`GET /api/subscriptions` 的 `nodes_tested_at` 单独标识节点测速结果的最新时间，不改变订阅成功刷新时间。相同节点配置刷新后保留测速记录，配置变化生成新节点 ID 并显示未测速；测速期间刷新或删除订阅时，只保存仍存在的相同 ID 的结果，不恢复旧节点。

节点的 `quality` 为 `null`（未检测），或 `{ "status": "ok|partial|failed", "ip": "出口 IP", "asn": 15169, "is_residential": false, "is_broadcast": false, "fraud_score": 0, "detail": "公开说明", "checked_at": "ISO 时间" }`。来源为 [IPPure 公开接口](https://ippure.com/MyIP-Info-API)。`is_residential` 为真表示住宅 IP，为假表示机房 IP；`is_broadcast` 为真表示广播 IP，为假表示原生 IP。`fraud_score` 范围 0–100，越低风险越低。缺失或无效字段为 `null`，不能据此判断机房、原生或 0 分。请求成功但属性或系数不完整时为 `partial`，连接失败时为 `failed`。IP 为此次访问 IPPure 时的出口，不代表 Claude 或其他网站必然使用同一出口。

质量结果保存在现有私有节点缓存中。`nodes_quality_checked_at` 单独标识最新结果时间，更新规则与延迟结果一致：相同配置保留，配置变化失效，并发删除或刷新不恢复旧节点。质量检查与测速互不覆盖。

出口记录保持原有 `status/ip/country_code/checked_at` 语义；失败或未知时当前 `ip` 仍为 `null`。`last_success` 单独保留上次成功的 IP、国家和时间，`previous_ip/changed_at` 记录最近一次检测到的 IP 变化。自动复测使用与手动检测相同的 Claude trace，不拿其他网站的 IP 替代。

`recover` 是内部恢复任务，不提供手动调用接口。只恢复 `desired_running=true` 的原绑定节点。用户提交停止或移入回收站后立即更换 `run_generation`，旧恢复任务失效；恢复回收站实例后仍需手动启动。网页启动、CLI 配置代理后的启动和自动恢复均检查 Claude 出口国家：必须能确认，并且与该实例 `last_success` 记录的出口国家相同（旧记录缺国家代码时用本地库查上次出口 IP）；从未成功过的实例只要求出口国家能确认。入口 IPv4 的位置不参与判断。出口国家无法确认、与上次不同或出口记录无法读取时，立即停止本实例代理并保留原绑定和原记录，自动恢复暂停等待人工检查。停止代理失败时明确显示无法确认断网，不宣称已停止。离线 CLI 启动仍可使用，但不开放公网。国家比较不使用节点名称。
