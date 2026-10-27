---
description: 提交当前会话的工作：英文 Conventional Commits、push、更新并同步 second-brain / Obsidian vault、按需 codegraph sync。
---

# /commit

提交当前会话的工作：英文 Conventional Commits、push、更新并同步 second-brain / Obsidian vault、按需 codegraph sync。

**仓库优先级**：若项目另有更具体的提交约定，先读它；项目特有规则优先于本流程。

## 1. 先取真实工作区

- 运行 `git status --short`、`git diff HEAD`、`git branch --show-current`、`git log --oneline -10`。
- 任何“当前 diff”都可能是过期快照；文件集合以 `git status --short` 为准，逐文件改动以 `git diff -- <file>` 为准。
- 无参数：只提交与**本次会话**直接相关的工作文件。拿不准就列出候选问用户。**禁止 `git add -A` / `git add .` 盲提交**。
- 有描述参数：只提交匹配描述的文件。多提交请求：按变更集拆分（代码与 `docs(second-brain)` 通常分开）。

## 2. 写提交信息

- 格式 `type(scope): subject`；类型：`feat` / `fix` / `refactor` / `docs` / `chore` / `test` / `style` / `perf`。
- **subject 与 body 全英文**，即使对话或改动内容是中文；标识符、路径、代码、中文数据原样保留，不翻译。
- 祈使语气、首字母小写、句末无句号；简洁描述，不写指标或夸张措辞。
- 只 stage 相关文件再 `git commit`；多段 body 用多条 `-m` 或 `git commit -F -`。

## 3. push

- 所有提交完成后 `git push`。失败（如需 rebase）报错并建议 `git pull --rebase`，**不要自动 rebase**。无 remote 则静默跳过。

## 4. 更新 second-brain（仓库存在 `second-brain/` 时）

- 先读 `second-brain/CLAUDE.md` 的「更新协议」「命名规则」与相关 `notes/`；只记录本次提交里有证据的事实/教训/决策，不编造、不重复、不整篇重写。
- 派生一个子代理（提示词里给 commit hash、分支、规则，并要求它只改 `second-brain/`、不执行 git 操作；让它自己跑 `git show <hash>` 取 diff）。
- 子代理超时或失败时：可按同一规则手工落盘，但**必须逐文件复核** `git diff second-brain/` 再继续，不能把未复核的子代理产物直接提交。
- 复核后单独提交为 `docs(second-brain): ...` 并 push。若本次提交本身就是 `docs(second-brain)`，跳过本步。

## 5. 同步 Obsidian vault（second-brain 有实质更新时）

- 读 `second-brain/CLAUDE.md` 的「vault 同步」节；按映射表把本次变化**定向**写入 vault：优先 `patch_note` / `append_to_note`，不整篇覆盖，不新建未获准的篇目。
- vault 是面向人的合并重写稿，不是 1:1 镜像：只同步真正变化的事实，重复内容不写。
- MCP 不可用 → 跳过并明确说明，不阻塞提交。无 second-brain、无映射节 → 跳过。
- 追加内容可能引入 LF 行尾而原文件是 CRLF，这是 MCP 的已知限制；不要为行尾重写整篇。新增笔记/改动 frontmatter 后按 vault 规矩更新 `updated` 日期。

## 6. codegraph sync

- 仅当本次 diff 含 `*.py` / `*.js` / `*.yaml` 时，在仓库根运行一次 `codegraph sync`；本次 diff 里没有 `*.py|*.js|*.yaml` 就不跑。没有 `.codegraph/` 或没有 `codegraph` 命令则静默跳过。

## 7. 汇报

- 报告每条提交的 hash 与标题、push 结果、second-brain 改了哪些文件、vault 同步了哪些篇目、codegraph 是否已同步。
- 如果代码改动需要重启运行中的服务（后端 Python 等），明确提醒；静态资源按项目约定说明是否需要硬刷新。
