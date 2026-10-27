# Gen Studio — 本机生成模型 Web 控制台

`web/` 是 `deploy-models` 仓库的可视化控制台：登录后启动 / 停止 **MiniMax H3** 与
**Qwen-Image 2.0** 两个生成模型，指定运行显卡，并在状态页核对真实运行情况；
另提供 H3 视频生成页，可直接提交提示词、查看进度并播放结果。技术栈为
FastAPI + Jinja2 服务端渲染 + 原生 CSS/JS，无前端构建步骤。控制台默认
监听 `127.0.0.1:8090`；部署时可用 `GEN_STUDIO_HOST` 或 `web/data/settings.json` 改为 `0.0.0.0`
（保存后需重启，见下文）。每个模型另有独立的**模型访问地址**（`127.0.0.1` /
`0.0.0.0`），决定该模型进程自身监听在回环还是所有网卡；默认回环。

> 前 5 项为最初约定的范围；视频生成页是后续按要求新增的页面，不改变前 5 项行为。
> 这里**没有**作品画廊、生成历史列表、用户注册、模型下载或公网暴露入口；控制台
> 监听地址只影响控制台自身，模型访问地址只影响对应模型端口。默认二者都不对
> 局域网暴露；只有显式把某个模型的访问地址设为 `0.0.0.0` 时，该模型端口
> （H3 30010 / ComfyUI 30020）才会对可达网络开放，且模型 API 没有鉴权，仅限
> 局域网使用。视频生成页只调用本机 H3，不代理 Qwen-Image。

## 快速开始

```bash
cd web
uv venv .venv
uv pip install -r requirements.txt
./serve.sh
# 浏览器打开 http://127.0.0.1:8090
# 初始账号 / 密码：admin / admin
```

系统要求：Python 3.12+、可执行的 `nvidia-smi`。首次启动会自动创建
`web/data/auth.json`（权限 0600，仅存 PBKDF2 哈希）。启动模型前需保证对应模型目录
的环境与权重已就绪（`h3/`、`qwen-image/` 各自的 README / 脚本）。

也可以直接运行：

```bash
cd web
GEN_STUDIO_DATA_DIR=./data .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

## 模型独立 unit 与重启隔离

生产环境把每个模型放进独立的 transient systemd user unit，控制台只负责写环境、
`systemd-run --user` 启动、`systemctl --user stop` 停止和 `systemctl show` 观察：

- unit 名：`gen-studio-minimax-h3`、`gen-studio-qwen-image2.0`。
- 每个 unit 位于控制台自己的 cgroup 之外，因此 `systemctl --user restart gen-studio`
  不会杀死正在运行的模型，也不会触发权重重载。
- GPU 与访问地址在每次启动时通过 unit 环境注入：`CUDA_VISIBLE_DEVICES`、
  `GEN_STUDIO_MODEL_HOST`。
- unit 使用 `--collect`，停止后自动卸载；不随开机/系统重启自动拉起模型。
- 控制台重启后通过 `systemctl show` + 端口/健康检查重新识别已在运行的模型，
  不需要重新启动或重新加载。
- 可通过 `GEN_STUDIO_MODEL_UNITS=0` 回退到旧的子进程模式（仅用于开发/测试；
  该模式重启控制台会杀模型）。

快速验证：

```bash
# 启动 H3 后，记录 PID
systemctl --user show gen-studio-minimax-h3 -p MainPID --value
# 重启控制台
systemctl --user restart gen-studio
# PID 应保持不变，H3 的 /health 仍为 200
systemctl --user show gen-studio-minimax-h3 -p MainPID --value
curl -s http://127.0.0.1:30010/health
```

## 需求对应与验证方式

| # | 需求 | 实现要点 | 如何验证 |
|---|------|----------|----------|
| 1 | 用户名 + 密码登录（默认 admin/admin） | `app/auth.py`：PBKDF2-HMAC-SHA256（260,000 次迭代 + 随机盐）用户库；`app/main.py`：服务端 session（HttpOnly / SameSite=Lax / 12h）+ CSRF token；未登录访问功能页由 `AuthRequired` 统一 303 到 `/login?next=...`，`/api/*` 返回 401 JSON | 未登录访问 `/control`、`/status`、`/password` 应跳转登录页；用 admin/admin 登录后进入「模型控制」 |
| 2 | 密码设置页（登录后修改密码） | `/password` 页面：校验当前密码 → 密码策略（6–64 字符、不得与当前相同、两次一致）→ 原子写入新哈希 → 注销其它会话、保留当前会话；全程校验 CSRF | 登录后在「密码设置」修改密码；旧密码失效、新密码可登录；另一个浏览器的会话被踢出 |
| 3 | 模型控制：启动 / 停止，必须选择显卡；可分别配置模型访问地址 | `/control` 为两个模型各渲染一张卡片；启动表单强制选择 GPU（占位项「请选择显卡…」不可提交，服务端再次校验 `gpu_index` 必填且必须存在于实时 `nvidia-smi` 列表）；启动时以 `CUDA_VISIBLE_DEVICES=<所选卡>` 与 `GEN_STUDIO_MODEL_HOST=<该模型的访问地址>` 启动独立的 transient systemd user unit；每张卡片可独立把模型监听地址保存为 `127.0.0.1` 或 `0.0.0.0`；停止对 unit 发 `systemctl --user stop`（SIGTERM → 等待 → SIGKILL） | 无显卡信息时页面明确提示并禁用启动；不选显卡提交会被拒绝且不会启动进程；选定显卡启动后 `nvidia-smi` 可见占用；点击停止后 unit、进程与端口释放；重启控制台后模型 PID 不变 |
| 4 | 运行状态页 | `/status` 服务端渲染，`/status/partial` 每 5 秒刷新；数据源为 `/proc`、`nvidia-smi`、模型自身 HTTP API；每个指标都带 `available` / `source` / `note`，取不到的值显示「不可用」并写明原因；H3 / Qwen 两块状态以 Sheet 页签切换，一次只显示一个 | 打开「运行状态」：出现 `minimax-h3` / `qwen-image2.0` 两个页签，点击只显示对应模型；H3 显示健康检查、PID、GPU 占用、SGLang 运行期信息；其队列与生成耗时明确标注不可用；Qwen-Image 显示 ComfyUI 版本与真实队列 |
| 5 | 商务风视觉 | `app/static/app.css`：深海军蓝 + 暗金点缀、白色卡片、细边框表格、状态药丸、登录页左右分栏；无外部字体/CDN，CSP 保持严格 | 直接观察登录页、模型控制页、运行状态页的排版与配色 |
| 6 | H3 视频生成（后续扩展） | `/generate`：提示词多行输入；分辨率默认 480（短边，可选 736）、时长默认 10 秒（4–15 秒）、画幅固定 16:9；`POST /api/generate` 调用本机 H3 `/v1/videos`，后台线程轮询 `/v1/videos/{id}`，成功后下载 MP4 到 `web/data/generated/`；`GET /api/generate/{id}` 返回进度；`GET /api/generate/{id}/video` 返回需登录的视频流。H3 未运行时页面明确提示，失败保留输入 | 打开「视频生成」：默认 480 / 10 秒；输入提示词提交后看到排队/生成/下载进度；完成后播放视频并回显本次提示词与参数；空提示词、非法分辨率/时长、H3 未启动均有可见中文错误 |

## 目录结构

```
web/
├── serve.sh                 # 启动脚本（读取监听地址后启动 uvicorn）
├── requirements.txt         # 运行依赖
├── requirements-dev.txt     # 测试依赖
├── pytest.ini
├── app/
│   ├── main.py              # FastAPI 应用、路由、鉴权依赖、安全响应头
│   ├── config.py            # Settings / 路径 / 默认账号
│   ├── modelaccess.py       # 每个模型独立的访问地址（web/data/model_access.json）
│   ├── generation.py        # H3 视频生成任务：校验、提交、轮询、下载
│   ├── auth.py              # PBKDF2 密码、用户库、会话与 CSRF
│   ├── gpu.py               # nvidia-smi 探测（绝不回退默认显卡）
│   ├── catalog.py           # 两个受支持模型的静态能力与启动配置
│   ├── manager.py           # 启动 / 停止 / 归属识别 / 状态持久化
│   ├── units.py             # systemd --user unit 的启动、观察与停止
│   ├── proc.py              # /proc、ss、端口、进程组工具
│   ├── status.py            # 状态采集（如实标注不可用）
│   ├── templates/           # base / login / control / generate / status / password
│   └── static/              # app.css / generate.js / status.js / control.js / favicon.svg
├── tests/                   # pytest：登录、改密、显卡必选、启停、状态、生成
└── data/（运行时生成，不入库）  # auth.json / state.json / model_access.json / logs/ / generated/
```

## 关键实现

### 登录与安全

- 密码只以 `pbkdf2_sha256$<iter>$<salt>$<digest>` 形式落盘；比较使用
  `hmac.compare_digest`，用户名不存在时也执行一次等价哈希，避免用户名探测。
- 会话保存在服务端内存，cookie 仅存随机 token（HttpOnly、SameSite=Lax、12h）；
  控制台重启后需重新登录，但不会持久化任何 bearer token。
- 所有写操作（登录后的 POST）校验每会话独立的 CSRF token，并校验 `Origin` 与
  `Host` 一致；响应附带 CSP、`X-Frame-Options: DENY`、`nosniff`、`no-store`。
- 登录跳转 `next` 参数只接受站内绝对路径，拒绝 `//host`、`http://...` 等开放重定向。

### 显卡选择（必选项）

- `GpuProbe` 调用
  `nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu --format=csv,noheader,nounits`。
- 任何失败（找不到命令、超时、非零退出、空列表、重复索引）都返回
  `available=False` 和具体原因；页面显示警告并禁用启动按钮。
- 服务端在每次启动时重新探测，`gpu_index` 缺失直接拒绝，索引必须存在于当前列表；
  不存在任何「默认卡」回退路径。

### 进程归属与停止

- 生产模式通过 `systemd-run --user --unit gen-studio-<model> --collect` 启动独立
  transient unit；unit 环境只包含 `CUDA_VISIBLE_DEVICES`、`GEN_STUDIO_MODEL_HOST`
  等必要变量，工作目录为模型目录，日志追加到 `web/data/logs/<model>.log`。
- 单元名、PID、启动 tick、GPU、日志路径写入 `web/data/state.json`。判断运行状态以
  `systemctl show -p LoadState,ActiveState,MainPID` 为准，端口/命令行只用于识别
  控制台之外的手工进程；控制台重启后 unit 仍在，无需重新启动模型。
- 停止对 active unit 执行 `systemctl --user stop`；unit 内的进程组收到 SIGTERM，
  超时后 SIGKILL。端口上若是控制台之外启动的进程，仅当命令行同时匹配模型标记
  （如 `sglang` + `MiniMax-H3`）才允许停止，否则拒绝并提示，避免误杀无关进程。
- unit 启动后 3 秒内未进入 active 状态即判定失败，页面显示日志尾部；脚本立即退出
  的旧子进程模式保留 1.5 秒检查（仅 `GEN_STUDIO_MODEL_UNITS=0` 时使用）。

### 控制台监听地址（部署配置）

- 控制台监听地址在进程启动时决定，uvicorn 不能在运行时重绑；页面**不提供**修改
  入口，需要部署时通过环境变量或配置文件决定。
- `serve.sh` 的优先级：`GEN_STUDIO_HOST` 环境变量 > `web/data/settings.json`
  的 `bind_host` > `127.0.0.1`；非法值会告警并回退回环地址。默认 `127.0.0.1`。
- 修改后重启控制台：`systemctl --user restart gen-studio`（或手动 Ctrl-C 后重新
  `./serve.sh`）。独立 systemd unit 中的模型不会被停掉或重新加载；登录会话保存在
  内存中，需要重新登录。
- 监听 `0.0.0.0` 时从局域网用 `http://<主机局域网IP>:8090/`；`127.0.0.1` 时只有
  本机可访问。局域网访问还需要防火墙放行 8090/tcp（本机 ufw 只对指定网段放行），
  并且**仅限局域网**，控制台没有 HTTPS，不要直接映射到公网。

### 模型访问地址（按模型独立）

- 「模型控制」页每张模型卡片都有一个**模型访问地址**单选项，取值只能是
  `127.0.0.1`（仅本机）或 `0.0.0.0`（局域网可达）。它只控制该模型进程自己的
  监听地址，与控制台监听地址（部署配置）互不影响；两个模型也可分别设置。
- 配置保存在 `web/data/model_access.json`（0600，运行时目录、不入库），内容形如
  `{"minimax-h3": "127.0.0.1", "qwen-image2.0": "0.0.0.0"}`。未配置或文件损坏时
  回退到 `127.0.0.1`，页面顶部会提示配置异常。
- 保存只写配置，**下次启动该模型时生效**；正在运行的模型需要先停止再启动，控制台
  不会为了切换地址自动重启进程，也不影响另一个模型。
- 启动时控制台把值注入 `GEN_STUDIO_MODEL_HOST`；两个入口脚本用
  `${GEN_STUDIO_MODEL_HOST:-127.0.0.1}` 作为 `--host`（SGLang）或 `--listen`
  （ComfyUI）参数，因此在控制台之外手动启动时仍默认回环。
- 端口固定：H3 `30010`、ComfyUI `30020`。选择 `0.0.0.0` 后，局域网内可用
  `http://<主机局域网IP>:<端口>/` 访问该模型；需要防火墙放行对应端口（本机 ufw
  默认放行，8090 是例外），且**模型 API 没有鉴权**，仅限局域网，不要暴露到公网。
- 直接 GET `/control/<model_id>/access` 不会修改配置：返回 303 回控制页，并提示
  保存必须使用页面按钮（POST）。

### H3 视频生成

- `/generate` 是登录后才能访问的页面：提示词多行输入，分辨率默认 `480`（可选
  `736`，对应 H3 `target.short_edge`），时长默认 `10` 秒（可选 4–15 秒，对应
  `seconds` 与 `target.duration_seconds`），画幅固定 `16:9`。
- 浏览器只负责表单与进度展示。`POST /api/generate` 校验参数后，同步调用 H3
  `POST /v1/videos` 拿到任务 id，再交给后台 daemon 线程轮询 `GET /v1/videos/{id}`；
  完成后从 `GET /v1/videos/{id}/content` 下载 MP4 到 `web/data/generated/<job_id>.mp4`。
  页面每 2 秒读取 `GET /api/generate/{id}` 更新进度，完成后把视频地址设为
  `GET /api/generate/{id}/video`（同样需要登录）。
- 固定生成参数：`task=t2va`、`conditions=[]`、`num_outputs_per_prompt=1`、
  `num_inference_steps=20`、`flow_shift=12.0`、`audio_flow_shift=3.0`、`seed=1101`；
  模型 id 默认绝对路径 `h3/models/MiniMax-H3`。
- 空/纯空白提示词、超长提示词、非 480/736 分辨率、4–15 之外的时长都会在服务端
  拒绝并返回中文错误，不会静默截断或改写；H3 连接失败返回 502，页面保留输入。
- 任务状态与结果只保存在控制台进程内存中：重启控制台后旧任务无法从页面继续查看
  （已下载的 MP4 仍在 `web/data/generated/`，但当前实现没有历史列表）。后台线程
  只服务当前进程；H3 未启动时页面会提示先去「模型控制」启动。
- H3 本身没有鉴权；`/api/generate/*` 由控制台的登录会话保护，视频文件只通过
  鉴权端点提供，不挂静态目录。

### 运行状态（诚实呈现）

- 每个字段为 `{label, value, available, source, note}`；`available=false` 时
  `note` 必须解释原因。模板对不可用值渲染「不可用」徽标 + 原因，而不是空白或假值。
- **MiniMax H3**：健康检查 `GET /health`、`/server_info`（已加载模型、SGLang 版本、
  并行度）、`/v1/models`（任务类型、管线、精度）、PID / 运行时长 / 端口 / 显卡 /
  显存 / 利用率 / 温度；队列与生成耗时因 SGLang 0.5.22 未暴露对应接口而标注不可用。
- **Qwen-Image 2.0**：`GET /system_stats`（ComfyUI / PyTorch 版本、后端可见显存）、
  `GET /queue`（运行 / 等待数量、当前 prompt_id）、进程与显卡指标。
- 两个模型的状态卡片放在 Sheet 页签下，默认显示 `minimax-h3`；切换只改前端可见性，
  不改变 5 秒轮询（仍只请求一次 `/status/partial`），轮询后保留当前选中页签。
  页签选中项不做 URL / localStorage 持久化（待补充项按最小实现处理）。
- 状态按模型能力分组展示：左侧运行指标，右侧能力与运行期信息；外部进程会标注
  「运行中（外部进程）」，尚未监听端口的外部加载进程标注「启动中（外部进程）」，
  端口被无关进程占用标注「端口冲突」。
- 外部进程在加载权重、尚未监听端口时，通过 `/proc` 命令行标记扫描识别，避免误报
  「已停止」；此时健康检查显示「未就绪（加载中）」，并拒绝从控制台重复启动。
- 页面每 5 秒取一次 `/status/partial`（服务端渲染片段）替换内容，浏览器不计算任何
  指标；刷新失败时保留上次结果并提示。

### 商务风前端

- 深海军蓝顶栏与侧栏式登录页、暗金细节点缀、白色卡片 + 细边框表格、状态药丸、
  等宽字体呈现端口 / PID / 版本；桌面与移动端均有响应式断点。
- 不加载任何外部字体、CDN 或内联脚本，CSP 可保持 `script-src 'self'`；
  无 JS 时页面仍可读、表单仍可使用（仅自动刷新失效）。

## 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `GEN_STUDIO_HOST` | 未设置 | 监听地址覆盖；优先于 `settings.json`，仅接受 `127.0.0.1` / `0.0.0.0` |
| `GEN_STUDIO_PORT` | `8090` | 监听端口 |
| `GEN_STUDIO_DATA_DIR` | `web/data` | `auth.json`、`state.json`、日志、下载视频目录 |
| `GEN_STUDIO_REPO_ROOT` | `web/..` | 仓库根目录（定位模型入口脚本） |
| `GEN_STUDIO_NVIDIA_SMI` | `nvidia-smi` | nvidia-smi 路径 |
| `GEN_STUDIO_SESSION_TTL` | `43200` | 会话有效期（秒） |
| `GEN_STUDIO_MODEL_HOST` | `127.0.0.1` | 控制台启动模型时按模型配置注入；脚本内默认回环，通常不需要手动设置 |
| `GEN_STUDIO_MODEL_UNITS` | `1` | `1` 用独立 transient systemd user unit 启动模型（重启控制台不杀模型）；`0` 回退旧子进程模式，仅用于开发/测试 |
| `GEN_STUDIO_H3_BASE_URL` | `http://127.0.0.1:30010` | 视频生成页调用的 H3 API 地址；H3 按模型配置监听 `0.0.0.0` 时仍可用回环访问 |

运行时文件：

- `web/data/settings.json` — 可选的控制台监听地址配置（部署时手工创建/修改）。
- `web/data/model_access.json` — 每个模型独立的访问地址（0600；默认均为 `127.0.0.1`）。
- `web/data/auth.json` — 单用户凭据（0600，仅哈希）。
- `web/data/state.json` — 本系统启动的模型记录（0600，含 systemd 单元名，用于控制台重启后核对归属）。
- `web/data/logs/<model>.log` — 由控制台启动的模型 stdout/stderr。
- `web/data/generated/<job_id>.mp4` — 视频生成成功后下载的 MP4（目录 0700、文件 0600；没有历史索引）。

## 测试

```bash
cd web
uv pip install -r requirements-dev.txt
.venv/bin/python -m pytest        # 61 项，约 40 秒
```

测试使用临时仓库 + 假启动脚本 + 假 `nvidia-smi`，不会触碰真实模型进程或
`web/data`；覆盖：未登录拦截、登录成功/失败、开放重定向防护、CSRF、退出登录、
改密全流程与会话注销、密码策略、显卡必选、无显卡信息拒绝、非法显卡索引、选定
显卡启动并在停止后确认进程退出、拒绝停止无关进程、状态字段的不可用标注、
H3 真实 API 字段映射、Qwen 队列读取、状态片段鉴权、商务风静态资源、
每个模型访问地址的独立保存、0600 权限、默认回环、启动时注入
`GEN_STUDIO_MODEL_HOST`、非法值拒绝、CSRF、按模型互不影响，以及对写操作端点
直接发 GET 时的友好重定向。生成相关覆盖：页面默认 480/10、空提示词与非法参数、
CSRF、未登录 401、异步任务成功/失败、视频端点、提示词回显与 502 提交失败。
独立 unit 覆盖：启动时注入 unit/GPU/访问地址、控制台新实例识别已在运行的 unit
而不重复启动、启动失败落状态、停止通过 systemd 单元完成。

## 手动验收清单

1. 退出登录后访问 `http://127.0.0.1:8090/status` → 跳转登录页；登录后回到状态页。
2. 用 `admin / admin` 登录 → 「密码设置」改为新密码 → 退出 → 旧密码登录失败、新密码成功。
3. 「模型控制」不选显卡点启动 → 页面提示必须选择显卡；将 `nvidia-smi` 改名（或
   临时设 `GEN_STUDIO_NVIDIA_SMI=/nonexistent`）后刷新 → 明确提示无法获取显卡且启动禁用。
4. 打开「运行状态」→ 默认只显示 `minimax-h3` 面板；点 `qwen-image2.0` 页签只显示 Qwen
   面板，两块内容不再上下堆叠；等待一次自动刷新后仍停留在当前页签。
5. 选择 `GPU 0` 启动 Qwen-Image（或 `GPU 1` 启动 H3）→ 成功提示；`nvidia-smi`
   可见对应卡被占用；「运行状态」显示运行中或启动中。
6. 点击「停止模型」→ 二次确认 → 进程退出、端口释放，状态页变为已停止。
7. 在 H3 卡片把「模型访问地址」设为 `0.0.0.0` 保存 → 启动 H3 后 `ss -ltn` 应显示
   `0.0.0.0:30010`（Qwen-Image 同理为 `0.0.0.0:30020`）；局域网机器可直接访问该
   端口。改回 `127.0.0.1` 并重启模型后，监听应回到 `127.0.0.1`。
8. 打开「视频生成」→ 默认 `480` / `10 秒`；直接提交空提示词应被拦截；填入提示词并
   确保 H3 已启动后提交 → 看到已用时间、生成/下载状态，完成后视频可播放，结果区回显
   本次提示词与参数；把分辨率改成 `736`、时长改成 `6` 再提交，H3 请求应按新参数发出。
9. 观察三个页面的商务风样式：深蓝顶栏、白色卡片、暗金强调线、状态药丸。
10. 启动 H3 后记录 `systemctl --user show gen-studio-minimax-h3 -p MainPID --value`；
    执行 `systemctl --user restart gen-studio` → 控制台恢复后 H3 PID 不变、`/health`
    仍为 200，控制页显示「运行中（systemd 单元）」，无需重新加载模型。

## 边界与已知限制

- 只控制需求指定的两个模型；不提供任意命令执行或自定义脚本入口。
- 视频生成只支持 H3 的 `t2va` 纯文本任务；不支持图生视频、首尾帧、音频参考或
  多模型切换。生成页没有任务历史/队列管理，任务状态仅在控制台进程内存中，
  重启后旧任务不能再从页面打开（已下载文件仍在 `web/data/generated/`）。
- H3 的 `/v1/videos/{id}` 只返回 `queued` 或最终 `completed/failed`，不提供百分比
  进度；生成页因此显示已用时间与不确定进度条，不编造百分比。状态页的 H3 队列 /
  耗时接口同样不可用，两者是不同数据源。
- 控制台监听地址由部署配置决定；模型端口是否对局域网开放由各模型独立的
  「模型访问地址」决定（默认回环）。模型 API 没有鉴权，只应在可信局域网内使用。
- 控制台仍只有 admin 单账号 + HTTP，不含公网暴露、HTTPS、登录限速或多用户鉴权。
- 会话为进程内存储，控制台重启后需重新登录；模型通过独立 systemd 单元运行，
  重启控制台不会停止或重新加载模型，重启后会自动重新识别。`state.json` 仍记录
  单元名与 PID 用于核对。
- 模型 unit 是 transient（`systemd-run --collect`），不会开机自启，停止后自动卸载；
  重载 H3 只发生在显式重启模型或系统重启时。
- 使用独立 unit 需要可用的 systemd user manager（`systemctl --user` / `systemd-run`）；
  不可用时可设 `GEN_STUDIO_MODEL_UNITS=0` 回退子进程模式，但该模式重启控制台会
  杀模型。
- 外部启动的模型进程若无法读到其 `CUDA_VISIBLE_DEVICES`，显卡显示为不可用而不是猜测。
- 端口固定（30010 / 30020）：被占用时启动会被拒绝，需先处理占用进程。
- `qwen-image/env` 未安装完整依赖或权重未就绪时，启动会在几秒内失败（unit 3 秒
  内未 active，或子进程 1.5 秒内退出），页面与日志会给出原因和日志尾部；这是
  后端部署问题，不是控制台静默成功。
- 模型加载需要时间：H3 在本机实测约 12–17 分钟才通过健康检查，期间状态页显示「启动中 / 未就绪（加载中）」。
