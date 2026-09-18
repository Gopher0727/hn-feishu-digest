# Hacker News → 飞书

每天按 Hacker News 排名抓取可访问正文，调用 DeepSeek 生成约 15 条中文摘要，最后发送一张可折叠飞书卡片。

## 运行时间

- 北京时间 09:27 主运行。
- 10:27、11:27、12:27 补偿运行。
- 当天已经发送成功时，发送指纹会让后续运行直接退出，不会重复推送。

## 本地配置

复制配置模板并填写真实密钥：

```sh
cp .env.example .env.local
chmod 600 .env.local
```

`.env.local` 需要包含：

```dotenv
FEISHU_WEBHOOK=飞书机器人Webhook
FEISHU_SIGN_SECRET=飞书签名密钥
LLM_API_KEY=DeepSeek_API_Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

## tmux 管理命令

```sh
./manage.sh start     # 后台启动定时器
./manage.sh status    # 查看是否运行
./manage.sh logs      # 持续查看日志，按 Ctrl-C 退出日志
./manage.sh attach    # 进入 tmux，会话中按 Ctrl-B D 返回后台
./manage.sh run-now   # 立即执行一次真实抓取、总结和发送
./manage.sh stop      # 停止定时器
```

日志保存在 `logs/scheduler.log`。摘要缓存、诊断和发送指纹保存在 `state/`。

## 内容处理规则

1. 每天先读取 `state/backlog.json` 中上次多抓到的有效文章，再从 HN 官方 API 读取排行榜补足。
2. 按排名每 10 条抓取一批，取得 15 条合格正文后停止。最后一批多出的有效文章写回缓存，第二天优先使用。
3. HN 自带正文的帖子直接保留；外部文章正文不足 1000 个字符、无法访问、非文本或失效时直接跳过。必要时遍历完整个 `topstories`；仍不足 15 条时发送实际成功内容，卡片不显示成功或跳过数量。
4. DeepSeek 每批处理 8 条，只生成逐条摘要和偏好相关度，不生成主题；每批独立重试并校验所有条目都有摘要。
5. 卡片顺序编号。前 7 条直接显示，其余内容放在“显示更多 / 收起”折叠面板中。
6. 每条保留原标题、完整中文摘要和原链接。只有所有选中条目都完成摘要后才发送飞书。

当天选中的来源保存在 `state/work-cache/YYYY-MM-DD/sources.json`，补偿运行直接复用该快照，不会再次消费候选缓存。`state/backlog.json` 只保存已成功取得正文、但尚未进入日报的文章。

## 验证

```sh
UV_CACHE_DIR=/tmp/hn-feishu-uv-cache uv run python -m unittest discover -s tests -v
UV_CACHE_DIR=/tmp/hn-feishu-uv-cache uv run python -m py_compile digest.py local_scheduler.py
```

### 摘要失败恢复

每批最多尝试 3 次，等待 1 秒、2 秒。校验失败时，下一次请求附带预期 ID、实际 ID、缺失及多余 ID，仍严格校验摘要与原文对应关系。整批内容校验持续失败后自动二分，最小到单篇；单篇仍失败则停止发送，等待补偿运行。

成功子批次即时写入当天缓存；拆分标记确保补偿运行直接恢复子批次，不重复请求失败的大批或已经成功的子批。网络或服务异常不触发拆分，以免放大请求量。

每次失败记录保存在 `state/work-cache/YYYY-MM-DD/failures/`，包含错误、预期及实际 ID，以及可解析的模型响应（请求失败时为空）。不记录 API Key 或请求头。这些文件沿用工作缓存的三天保留规则；需要长期排查时请提前备份。所有摘要校验通过后才发送飞书。

## 双机运行与 GitHub 采集兜底

默认 `PUSH_MODE=local`，保留个人电脑抓取、DeepSeek 总结、飞书推送。`PUSH_MODE=company` 时按 `TASK_ROLE` 分工。只修改 `.env.local` 即可切换；修改配置后请重启常驻调度器。切换推送端之前先停用旧端，勿让两台机器同时推送：发送记录在各自本地，不能跨机器自动去重。

| 执行端 | 配置 | 北京时间 |
| --- | --- | --- |
| 个人电脑 | `company` + `collector` | 08:30，09:30、10:30、11:30 补偿 |
| GitHub Actions | 仅采集兜底 | 09:07、10:07、11:07、12:07 |
| 公司电脑 | `company` + `consumer` | 09:27，10:27、11:27、12:27 补偿 |
| 原有完整模式 | `local` | 09:27，10:27、11:27、12:27 补偿 |

### 1. GitHub 私有数据仓库

创建一个私有仓库（例如 `YOUR_USERNAME/hn-digest-data`），初始化 README。正文数据与公开代码分开。配置使用默认分支，不需要运行 `git pull`。

个人电脑和 Actions 的 `DATA_TOKEN` 使用限定该数据仓库、Contents 读写权限的 fine-grained token；公司电脑使用 Contents 只读 token。凭证只放 `.env.local` 或 GitHub Secrets。

公司电脑需要能访问 `api.github.com`、内网模型地址和飞书接口，不需要访问 HN 或文章原站。GitHub 网页能打开不等于 API 一定能访问。

### 2. 个人电脑配置

将 `config/collector.env.example` 的内容放入个人电脑 `.env.local`，填写 `DATA_REPO`、`DATA_TOKEN`。无需模型或飞书密钥。

`DATA_DIR` 指定 JSON 存放目录，默认为项目的 `data/`。每天生成 `YYYY-MM-DD.json`，上传到数据仓库 `daily/YYYY-MM-DD.json`。资料含标题、原链接、已提取正文、顺序、备用文章和 SHA256 指纹。沿用当前文本抓取器：外部文章至少 1000 字符，但当前最多保留 1800 字符的文本摘录，并非全文或多媒体快照；摘要本身不截断。

已发布的日资料包不可覆盖。个人电脑与 Actions 同时采集时，GitHub 文件创建冲突会阻止覆盖，双方以已发布包为准。次日从最近发布包恢复备用文章，并排除上一包选中 ID；这表示上一包已选，不代表公司已成功发送。个人电脑切换到采集模式的第一天从新资料开始，不迁移旧完整模式的 backlog。

### 3. 公司电脑配置（llama.cpp）

将 `config/consumer.env.example` 的内容放入公司电脑 `.env.local`，填写 GitHub 只读凭证、飞书配置及模型地址。

`LLM_BASE_URL=http://127.0.0.1:8080/v1`；本机部署无需外网模型 API。`LLM_MODEL` 与服务的 `--alias` 或 `/v1/models` 返回的 id 一致。没有启用认证时 `LLM_API_KEY` 留空。

例如已安装 llama-server 并准备模型后，在另一终端启动：

```sh
llama-server -m /path/to/model.gguf --alias hn-local --host 127.0.0.1 --port 8080 -c 16384
```

模型须支持聊天指令与中文摘要，实际上下文和输出长度取决于模型、硬件。公司示例每批 2 条、输出上限 2048 tokens、请求超时 600 秒；可通过 `LLM_BATCH_SIZE`（1–10）、`LLM_MAX_TOKENS`、`LLM_TIMEOUT_SECONDS` 调整。默认保留 JSON 约束；服务不支持时可设 `LLM_JSON_MODE=false`，程序仍严格验证 JSON 和 ID。上下文溢出类 HTTP 错误需要减小批次或增大服务上下文，不会当作 ID 错误拆批。

当天文件不存在时本轮正常退出，等待补偿运行；不发送昨天的数据。模型失败则保存诊断，下轮恢复已成功子批。公司端固定当天输入指纹，拒绝中途替换资料。生成卡片与原来一致。

### 4. 两台电脑启动

在各自项目目录执行相同命令，角色由 `.env.local` 决定：

```sh
uv sync
uv run python local_scheduler.py --once  # 手动执行一次对应角色
uv run python local_scheduler.py         # 常驻定时器
```

Mac 后台运行继续使用 `./manage.sh start`，现在使用 `caffeinate -is`，允许自动灭屏；保持接电开盖。`./manage.sh stop` 停止常驻任务。

Windows 安装 uv 后使用以上 Python 命令；不用 `manage.sh`。项目已为 Windows 声明 `tzdata` 依赖，`uv sync` 会自动安装 IANA 时区数据库。也可在任务计划程序按公司端四个时间触发 `uv run python local_scheduler.py --once`，把“起始于”设为项目目录。llama-server 必须另外保持运行。

调度器入口有跨平台进程锁，同一 `STATE_DIR` 下不会并发执行。请统一通过 `local_scheduler.py` 或 `manage.sh run-now` 启动；直接运行 `digest.py` 不经过该锁。`STATE_DIR`、`DATA_DIR` 是本机持久化路径，不能每天清空；重装或迁移消费端时保留发送记录。JSON 与数据仓库 Git 历史不会自动清理。

### 5. 启用 GitHub Actions

把 `.github/workflows/collect-fallback.yml` 推送到代码仓库默认分支。在代码仓库 Settings → Secrets and variables → Actions 配置：

- Variables：`PUSH_MODE=company`、`DATA_REPO=YOUR_USERNAME/hn-digest-data`。
- Secrets：`DATA_TOKEN`，使用私有数据仓库 Contents 读写凭证。

未配置变量时采集作业跳过；切回个人电脑完整模式时同时把仓库变量 `PUSH_MODE` 改为 `local`。电脑上的开关不会自动修改 GitHub 变量。

Actions 仅兜底采集，不调用公司模型、不发送飞书。当天资料已存在时退出；失败诊断保留 7 天 Artifact。定时任务可能延迟，不能保证分钟级准点；所有补偿时间错过后可在 Actions 手动执行，再在公司端手动执行一次。

### 参考文档

- [llama.cpp server 接口与配置](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [GitHub Contents API](https://docs.github.com/en/rest/repos/contents)
- [GitHub 定时事件及延迟说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
