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
