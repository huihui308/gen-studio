# gen-studio

在本机（2 × RTX 3080 Ti 12 GB）上部署开源生图 / 生视频模型的可复现脚本仓库，外加访问这些模型的 web 生成工作台（`web/`，规划中）：环境构建 → 权重下载 → 起服务 → 发请求 → 界面，**一个模型一个子目录**。当前只有 `h3/`（MiniMax-H3 FL2VA + SGLang）。

- 起服务：`cd h3 && ./serve_recipe_a.sh`（监听 `127.0.0.1:30010`）
- 发请求：另开终端 `cd h3 && ./request_480p.sh`（产物落 `h3/output/`）
- 运行资产（`h3/env/`、`h3/models/`、`h3/output/`、`h3/logs/`）不入 git

## 记忆查找顺序

> 下面两行用 `@` 导入 second-brain 索引到本文件，随会话自动加载。改内容请编辑 second-brain/ 源文件，勿在此维护副本。
@./second-brain/MEMORY.md
@./second-brain/CLAUDE.md

需要查记忆/历史知识时，先分类再定位，命中即答，不翻遍所有源。

1. **代码结构问题**不属记忆 → 直接读 `h3/*.sh` / `README.md`（工程没有 `.codegraph/`）。
2. **仓库 / 本机环境知识** → **金标准在 second-brain**：先看 `second-brain/MEMORY.md`（核心事实）+ `second-brain/CLAUDE.md`（notes 索引表），命中主题即 Read `second-brain/notes/<file>.md` 作答，**不再翻别的源**。
3. **second-brain 无对应内容**才 fallback：auto-memory（`~/.claude/projects/*/memory/`）→ Hindsight → 问用户，不无限挖。
4. **全局约定 / 个人工作偏好 / 行为反馈**（非本仓库知识）→ auto-memory + 全局 CLAUDE.md，不必先扫 second-brain。

### 金标准归位
- second-brain 是**本仓库 + 本机 Claude 环境知识的唯一权威**；同主题若与 auto-memory 分叉，**以 second-brain 为准**，发现 auto-memory 副本过时即改为指针（不镜像全文）。
- 写入侧同向：新事实/踩坑/决策优先落 second-brain；auto-memory 对重叠主题只留一行「详见 second-brain/notes/X.md」指针。
- auto-memory 保留其真正专属内容：跨仓库也生效的个人偏好与全局反馈。

## 文档

- `second-brain/` — 知识索引（金标准），按主题拆分，详见 `second-brain/CLAUDE.md`
- `second-brain/decisions.md` — 决策记录
- `README.md` — 仓库总览（英文主版，默认入口）；`README.zh-CN.md` 为中文版，两版需同步
