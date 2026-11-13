# Gen Studio — 本机生成模型 Web 控制台

`web/` 是 `deploy-models` 仓库的可视化控制台：登录后启动 / 停止 **MiniMax H3** 与
**Qwen-Image-2.1-Turbo int8**（模型 id 仍为 `qwen-image2.0`，unit/路由依赖）两个生成模型，指定运行显卡，并在状态页核对真实运行情况；
另提供 H3 视频生成页（提示词 → 视频，进度 + 播放）与图像生成页（参考图 + 提示词 → 生图 → 下载）。技术栈为
FastAPI + Jinja2 服务端渲染 + 原生 CSS/JS，无前端构建步骤。控制台默认
监听 `127.0.0.1:8090`；部署时可用 `GEN_STUDIO_HOST` 或 `web/data/settings.json` 改为 `0.0.0.0`
（保存后需重启，见下文）。每个模型另有独立的**模型访问地址**（`127.0.0.1` /
`0.0.0.0`），决定该模型进程自身监听在回环还是所有网卡；默认回环。

> 前 5 项为最初约定的范围；视频 / 图像生成页是后续按要求新增的页面，不改变前 5 项行为。
> 这里**没有**作品画廊、生成历史列表、用户注册、模型下载或公网暴露入口；控制台
> 监听地址只影响控制台自身，模型访问地址只影响对应模型端口。默认二者都不对
> 局域网暴露；只有显式把某个模型的访问地址设为 `0.0.0.0` 时，该模型端口
> （H3 30010 / ComfyUI 30020）才会对可达网络开放，且模型 API 没有鉴权，仅限
> 局域网使用。视频生成页只调用本机 H3；图像生成页经控制台同源代理调用本机 Qwen-Image
生图服务（`http://127.0.0.1:30021`），浏览器不直连、不跨域。

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
| 7 | 图像生成（后续扩展，2026-11-08） | `/image`：参考图可选上传（png/jpg/webp/gif，≤20 MB，前端缩略图）、提示词（≤20000 字符）、分辨率 768/1024/1328/1536/2048（默认 1024）、宽高比（自动 / 1:1 / 4:3 / 3:2 / 16:9 / 21:9 / 3:4 / 2:3 / 9:16）、随机种子可选；`POST /api/image/generate`（CSRF + 登录）在服务端用 multipart 转发到 Qwen-Image `/generate`（无参考图时转发 JSON），同步等待结果（单张几十秒），返回 `images[0].b64` 供页面直出预览；下载按钮用 base64 生成 Blob 并以服务返回的 `filename` 命名 | 打开「图像生成」：上传参考图 + 提示词提交后看到已用时间与不确定进度条；完成后显示图片与参数回显，点击下载得到 PNG；不传参考图时纯文生图同样可用；空提示词、错误分辨率、非图片文件、服务不可达、超时均有可见中文错误 |

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
│   ├── apikeys.py           # 生图服务 API Key：签发/启停/重置（web/data/api_keys.json，0600）
│   ├── modelaccess.py       # 每个模型独立的访问地址（web/data/model_access.json）
│   ├── generation.py        # H3 视频生成任务：校验、提交、轮询、下载
│   ├── imagegen.py          # Qwen-Image 生图：校验、multipart 转发、base64 结果
│   ├── auth.py              # PBKDF2 密码、用户库、会话与 CSRF
│   ├── gpu.py               # nvidia-smi 探测（绝不回退默认显卡）
│   ├── catalog.py           # 两个受支持模型的静态能力与启动配置
│   ├── manager.py           # 启动 / 停止 / 归属识别 / 状态持久化
│   ├── units.py             # systemd --user unit 的启动、观察与停止
│   ├── proc.py              # /proc、ss、端口、进程组工具
│   ├── status.py            # 状态采集（如实标注不可用）
│   ├── templates/           # base / login / control / generate / image / apikeys / status / password
│   └── static/              # app.css / generate.js / image.js / apikeys.js / status.js / control.js / favicon.svg
├── tests/                   # pytest：登录、改密、启停、状态、视频/图像生成、API Keys、设备仪表盘
└── data/（运行时生成，不入库）  # auth.json / state.json / model_access.json / api_keys.json / api_keys_usage.json / logs/ / generated/
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

### 图像生成（2026-11-08）

- 页面 `/image`（nav「图像生成」），登录保护；左表单：提示词、参考图（可选，前端即时缩略图 + 移除）、分辨率（720/768/1024/1080/1328/1536/2048，默认 1024）、宽高比（默认「自动」）、随机种子（可选，留空即随机）。分辨率一栏下方实时显示「预计输出 W × H」。
- 分辨率档位按服务的 32 倍数网格对齐（1080→1088、720→704、1328→1344），页面上有「· 对齐 N」标注并实时显示预计输出；`GET /api/image/size?resolution=&aspect=` 返回换算结果，前端只做展示，不重复实现算尺寸逻辑。
- 宽高比不改变服务契约：服务只认 `resolution`（面积预算）+ 可选 `width`/`height`，所以控制台把所选比例按 `resolution²` 的面积算成 32 倍数的宽高对再当 `width`/`height` 传下去（`app/imagegen.py: resolve_size`），长边上限 2048——21:9 这类超宽比例在 2048 档会因上限而面积偏小。
- 宽高比默认「自动」＝不传 `width`/`height`：无参考图输出方形、有参考图跟随参考图。选了具体比例就会强制指定画布；编辑模式下若与参考图比例差太多，画面会漂移（官方 `custom_size` 的已知行为）。
- `POST /api/image/generate`：CSRF → 校验 → 有参考图时拼 `multipart/form-data`（`prompt`、`resolution`、`return_b64=true`、`file=参考图`）转发到生图服务 `POST /generate`；无参考图时转发等价的 JSON。上游调用是**同步**的（单张几十秒），用 `run_in_threadpool` 跑，避免阻塞控制台事件循环。
- 服务端把 `images[0]` 的 `filename` / `subfolder` / `width` / `height` / `b64` 回给页面；页面用 base64 直接渲染预览，下载按钮从同一份 base64 构造 Blob 并以 `filename` 命名，**不需要再访问上游 `/result`**。
- 代理而不是直连的原因：生图服务与页面不同源（端口不同），且 CSP 的 `connect-src 'self'` 会拦下浏览器对 `:30021` 的 fetch；代理同时让服务地址集中在 `GEN_STUDIO_IMAGE_API_BASE` 一处配置。
- 结果图可点击放大（全屏 lightbox，Esc / 点空白关闭，也支持 Enter/Space 键盘打开）。预览内**鼠标滚轮以光标为中心缩放 100%–800%**，放大后按住拖拽平移，左上角百分比按钮 / 双击图片 / 数字键 `0` 复位，`+` / `-` 也可缩放；100% 时点图关闭，放大状态下点图不关闭以免误触。放大同样用 `data:` URL，不额外请求服务。
- 预览与参考图缩略图都走**同源 `data:` URL**（页面的 CSP 为 `img-src 'self' data: blob:`）；`blob:` 只用于 `<a download>`。早期预览直接用了 `blob:` URL，被 CSP 拦下导致破图，现已修正并有回归测试。
- 生图服务没有进度接口，所以进度条是不确定状态、只显示已用时间；上游超时（600 秒）映射为 504，不可达映射为 502，参数/上传问题映射为 400。
- 参考图只接受 png / jpg / webp / gif（按 magic bytes 判定，不信声明），单张 ≤20 MB；提示词为空时不发请求。

### API Keys（2026-11-12）

- 页面 `/apikeys`（nav「API Keys」），登录保护；仿千问/DashScope 的 API Keys 页：访问模式警示条 + 两行 Base URL（OpenAI 兼容 `/v1` 与原生，按 `_lan_ip()` 与生图服务端口拼出，带复制按钮）+ 常驻创建表单 + 七列表格（ID / 掩码 key / 描述 / 创建时间 / 调用 / 状态 / 操作）。
- 访问模式三种，与生图服务的行为一一对应：key 文件不存在 →「开放」（局域网免凭证）；存在且至少 1 个启用 →「需 Key」；存在但 0 个启用 →「已锁定」（仅回环）。规则直接写在页面警示条上。
- `app/apikeys.py: ApiKeyStore` 是 `web/data/api_keys.json` 的**唯一写方**：0600、临时文件 + `os.replace` 原子替换、`threading.Lock`，模式照抄 `ModelAccessStore`；key 为 `sk-` + 48 位 hex，`next_id` 单调递增（删除不复用 id），文件损坏时页面显示警示条、创建即自愈。描述 ≤200 字可留空。
- 表格只显示掩码 `sk-xxxx****yyyy`，完整值只放行内 `data-key` 属性，由「复制」按钮取用；复制走 `execCommand('copy')` 回退——`http://<lan>` 非安全上下文没有 `navigator.clipboard`。搜索框纯前端按描述过滤行。
- 调用统计 `api_keys_usage.json` 由**生图服务**写、本页只读（读失败静默忽略），表格展示次数与最近使用时间（`localtime` 过滤器）。
- 写操作全部是表单 POST + CSRF（创建 / 编辑描述 / 启停 / 重置 / 删除），每个路径另有 GET 兜底友好重定向；重置与删除带前端二次确认（服务端语义不受 JS 影响，无 JS 也能完成全部操作）。
- `ImageClient` 新增可选 `api_key_provider`（接 `store.first_enabled_key`）：`GEN_STUDIO_IMAGE_API_BASE` 指向局域网地址时，控制台代理会自动带 `Authorization: Bearer`；默认回环路径下行为与之前完全一致。
- 服务侧对应行为见 `qwen-image/API.md` 第 2 节（鉴权）与第 7 节（OpenAI 兼容端点）：文件存在即开、回环豁免、Bearer / X-API-Key / `?api_key=` 三种传法、每请求 stat 重读（控制台建 key 免重启）。

### 运行状态（诚实呈现）

- 每个字段为 `{label, value, available, source, note}`；`available=false` 时
  `note` 必须解释原因。模板对不可用值渲染「不可用」徽标 + 原因，而不是空白或假值。
- **设备仪表盘**（2026-11-12）：页面顶部新增汽车仪表盘区——CPU、内存、每块 GPU 各一枚
  270° 弧形表盘（SVG 服务端渲染，随 `/status/partial` 每 5 秒整体刷新），含 11 根刻度、
  指针、中央数值与分级配色（`app/status.py: LEVELS/level`，正常/偏高/告警三档）。
  - 取数：CPU 用 `/proc/stat` 两次采样差值，由**后台线程固定每 5 秒独立采样**
    （与页面轮询解耦——标签页隐藏/页面关闭/控制台部署期间照常出数；样本滞后 >15s 才
    留空并注明，窗口无效（挂起恢复/时钟回拨）只重置不产出假数。旧实现由页面轮询喂
    采样点，断档 >60s 即留空，被报「CPU 经常不更新」，2026-11-12 改为常驻采样）；
    内存用 `/proc/meminfo`（used 口径与 `free` 一致，实测逐字节相等）；
    GPU 复用现有 `GpuProbe`，每卡一枚表盘（指针=利用率，卡底细条=显存占比，
    副文案含型号/显存/温度）。
  - `payload.device = {cpu, memory}` 挂进 `collect()`，`GET /api/status` 同步返回；
    阈值：CPU 70/90、内存 75/90、GPU 利用率 70/90、显存 80/95、温度 75/90（°C）。
  - 边界：nvidia-smi 失败 → GPU 表盘整块不渲染（只留 CPU/内存 + 告警文案），单卡字段
    缺失 → 该盘显示「不可用」徽标；`/proc` 读取失败 → 该盘留空并给出原因；页面任何
    位置不出现 NaN/undefined。
- **磁盘空间**（2026-11-13）：显卡概览下方新增磁盘表，逐挂载点列出挂载点 / 设备 /
  文件系统 / 已用 / 总量 / 可用 / 使用率（SVG 分级进度条，阈值 80/95，`LEVELS["disk"]`）。
  - 取数：挂载清单来自 `/proc/mounts`（只收真实磁盘文件系统白名单 ext4/xfs/btrfs 等，
    排除 tmpfs/overlay/vfat；同设备的 bind 挂载去重），容量来自 `os.statvfs`，无子进程。
  - 使用率口径与 `df(1)` 完全一致：已用 = 总块数 − 空闲块数（含 root 预留）、可用 =
    bavail、百分比**向上取整**（df 不四舍五入；本机 /、/boot/efi、/mnt/sdb1、/mnt/sdc1
    实测 39/1/33/38 逐点对齐）。
  - 边界：`/proc/mounts` 读不到 → 整块「不可用」+ 原因；单个挂载点 statvfs 失败 →
    该行「不可用」+ 原因、其余行照常显示；容量为 0（挂载未就绪）同样标注原因。
    `payload.device.disk` 随 `GET /api/status` 返回，随 `/status/partial` 每 5 秒刷新。
- **MiniMax H3**：健康检查 `GET /health`、`/server_info`（已加载模型、SGLang 版本、
  并行度）、`/v1/models`（任务类型、管线、精度）、PID / 运行时长 / 端口 / 显卡 /
  显存 / 利用率 / 温度；队列与生成耗时因 SGLang 0.5.22 未暴露对应接口而标注不可用。
- **Qwen-Image-2.1-Turbo int8**：`GET /system_stats`（ComfyUI / PyTorch 版本、后端可见显存）、
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
| `GEN_STUDIO_IMAGE_API_BASE` | `http://127.0.0.1:30021` | 图像生成页经控制台代理调用的 Qwen-Image 生图服务地址 |

运行时文件：

- `web/data/settings.json` — 可选的控制台监听地址配置（部署时手工创建/修改）。
- `web/data/model_access.json` — 每个模型独立的访问地址（0600；默认均为 `127.0.0.1`）。
- `web/data/auth.json` — 单用户凭据（0600，仅哈希）。
- `web/data/sessions.json` — 已登录会话（0600：token、用户名、CSRF token、创建时间），控制台重启后仍有效。
- `web/data/state.json` — 本系统启动的模型记录（0600，含 systemd 单元名，用于控制台重启后核对归属）。
- `web/data/api_keys.json` — 生图服务的 API Key（0600；控制台唯一写方，生图服务只读；不存在 = 服务不鉴权）。
- `web/data/api_keys_usage.json` — 每个 key 的调用次数与最近使用（0600；生图服务写、控制台只读）。
- `web/data/logs/<model>.log` — 由控制台启动的模型 stdout/stderr。
- `web/data/generated/<job_id>.mp4` — 视频生成成功后下载的 MP4（目录 0700、文件 0600；没有历史索引）。

## 测试

```bash
cd web
uv pip install -r requirements-dev.txt
.venv/bin/python -m pytest        # 179 项，约 65 秒
```

测试使用临时仓库 + 假启动脚本 + 假 `nvidia-smi`，不会触碰真实模型进程或
`web/data`；覆盖：未登录拦截、登录成功/失败、开放重定向防护、CSRF、退出登录、
改密全流程与会话注销、密码策略、显卡必选、无显卡信息拒绝、非法显卡索引、选定
显卡启动并在停止后确认进程退出、拒绝停止无关进程、状态字段的不可用标注、
H3 真实 API 字段映射、Qwen 队列读取、状态片段鉴权、商务风静态资源、
每个模型访问地址的独立保存、0600 权限、默认回环、启动时注入
`GEN_STUDIO_MODEL_HOST`、非法值拒绝、CSRF、按模型互不影响，以及对写操作端点
直接发 GET 时的友好重定向。API Keys 覆盖：未登录拦截（页面 + 全部写端点）、创建
（描述可空/超长拒绝）→ 文件 0600 且完整 key 只在 `data-key`、编辑、启停与模式翻转、
重置换新值、删除同步文件且 id 不复用、GET 兜底重定向、伪造 CSRF 拒绝、usage 只读
展示、坏文件警示与自愈、`ImageClient` 带 `Authorization` 头（含空 key 不带）。
设备仪表盘覆盖：payload 含 `device` 块、CPU 差值采样（注入样本算出精确百分比）、
首采/过期窗口留空带原因、阈值分级、页面 4 盘渲染无 NaN/无空表盘、GPU 探针失败时
只画 CPU+内存两盘、partial 片段含仪表盘。磁盘覆盖：payload 含 `device.disk`、
阈值分级、确定性挂载渲染表格且无 NaN、`/proc/mounts` 整块不可用带原因、单挂载
statvfs 失败保留其余行、使用率向上取整与 df 对齐。生成相关覆盖：页面默认 480/10、空提示词与非法参数、
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
11. 打开「API Keys」→ 创建一个 key（描述可空）→ 表格出现掩码行，点「复制」得到完整
    `sk-` 密钥；再测编辑描述、启停、重置（二次确认后密钥换新）、删除（行消失）。用
    `curl -H 'Authorization: Bearer <key>'` 调生图服务 `POST /v1/images/generations`
    应通过鉴权；去掉头则 401。
12. 打开「运行状态」→ 顶部出现设备仪表盘（CPU / 内存 / 每卡一枚）；与 `vmstat`、
    `free`、`nvidia-smi` 对照数值一致；等 5 秒看指针随刷新移动；把浏览器窗口缩到
    760px 确认网格分列不错乱。
13. 同页「磁盘空间」表 → 与 `df --output=used,avail,pcent /` 对照，挂载点、容量与
    使用率一致（百分比向上取整，与 df 同为 39% 而非四舍五入的 38%）；等 5 秒看数值
    随 partial 刷新。

## 边界与已知限制

- 只控制需求指定的两个模型；不提供任意命令执行或自定义脚本入口。
- 视频生成只支持 H3 的 `t2va` 纯文本任务；不支持图生视频、首尾帧、音频参考或
  多模型切换。生成页没有任务历史/队列管理，任务状态仅在控制台进程内存中，
  重启后旧任务不能再从页面打开（已下载文件仍在 `web/data/generated/`）。
- H3 的 `/v1/videos/{id}` 只返回 `queued` 或最终 `completed/failed`，不提供百分比
  进度；生成页因此显示已用时间与不确定进度条，不编造百分比。状态页的 H3 队列 /
  耗时接口同样不可用，两者是不同数据源。
- 控制台监听地址由部署配置决定；模型端口是否对局域网开放由各模型独立的
  「模型访问地址」决定（默认回环）。H3 的模型 API 没有鉴权，只应在可信局域网内使用；
  生图服务（30021）自 2026-11-12 起支持 API Key 鉴权（控制台「API Keys」页签发，
  文件不存在时保持旧行为），回环调用始终豁免。
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
