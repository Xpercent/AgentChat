---
name: agentchat
description: 双 Agent 通信。确定 Agent1/Agent2 角色，启停中继，发送任务、回复结果，并在 watch 退出后重新监听。
---

# AgentChat

所有命令在包含 `run.py` 的项目目录执行。

## 确定角色

- 发送任务、获取结果：Agent1。
- 接收任务、监听待命、处理 watch 完成通知：Agent2。
- 根据用户要求和会话上下文确定；仍不明确时再询问。

## Agent2：启动监听

用宿主的后台任务工具，分别启动：

```bash
python run.py serve
python run.py watch
```

已有正常运行的 `serve` 就复用。确认中继启动成功后再挂 `watch`，不要重复启动。
`serve` 长期运行；`watch` 必须允许完整运行 9 分钟，并在结束时通知当前会话。后台启动成功后结束会话，不要前台等待。

## Agent2：watch 退出后

读取刚结束的后台任务输出，按内容执行：

| 输出 | 操作 |
| --- | --- |
| 收到任务 #N | 完成任务，使用实际 ID 回复；成功后重新后台启动 `watch`，结束会话 |
| 保活唤醒、这不是任务 | 不执行任务、不调用 `reply`；立即重新后台启动 `watch`，结束会话 |
| 报错或异常退出 | 检查中继和错误；恢复后重挂 `watch`，恢复失败则报告 |

回复方式二选一，示例中的 `1` 替换为本轮任务 ID：

```bash
python run.py reply --id 1 --text "实际结果"
python run.py reply --id 1 --file .tmp/结果.md
```

每个新 `watch` 从零开始计时，9 分钟无任务就输出保活指引并退出。任务和保活的退出码都是 `0`，以输出内容区分。不要每轮重启 `serve`。

## Agent1：发送与取结果

```bash
python run.py send --text "具体任务"
```

长任务可改用 `python run.py send --file .tmp/任务.md`。
默认等待结果；若调用工具无法等待约 20 分钟，先分离提交，再用返回的实际 ID 补取：

```bash
python run.py send --text "具体任务" --detach
python run.py wait --id 1
```

分离提交的 ID 在标准错误的“已提交 #N”中。超时后仍用原 ID 执行 `wait`，不要直接重发任务。

## 停止服务

用户要求停止时，先通过宿主终止自己的后台 `watch`，再执行：

```bash
python run.py stop
```

停止后不再重挂。

任务从 `watch` 输出读取，结果通过 `send` 或 `wait` 获取，不读 `data/` 传话。文件输入先写好，临时文件放在 `.tmp/`。
