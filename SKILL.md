---
name: agentchat
description: 本地双 Agent 轻量级 HTTP 通信工具。当用户要求"两个 Agent 互相通信 / 派发任务 / 让另一个 Agent 干活并等结果 / 监听端口接收任务 / 双 Agent 协作"，或提到 agentchat、Agent1、Agent2、i{id}.md、o{id}.md 时使用。
---

# AgentChat

只做三件事：**把任务文本从 Agent1 送到 Agent2**、**把结果送回来**、**把每轮交流落盘成 `data/i{id}.md` / `data/o{id}.md`**。
它不会思考，也不靠 data 传话 —— data 只是日志，两端都不许读它做业务。

## 0 先定角色

| 用户意思 | 角色 | 动作 |
| --- | --- | --- |
| 你负责监听 / 接收任务 / 你是 Agent2 | **Agent2** | 起中继 + 上闹钟，然后结束会话 |
| 把任务发给 Agent2 / 你是 Agent1 | **Agent1** | 一条 `send` 阻塞拿结果 |
| 没说清 | — | 先问 |

一个会话只当一种角色。

## 1 环境

命令都在**工具根目录**（有 `run.py` 那一层）执行，免安装：

```bash
python run.py <子命令>      # 缺依赖先 python -m pip install -r agentchat/requirements.txt
```

默认地址 `http://127.0.0.1:18765`，端口冲突时两端同时改 `--url` / `--port`。

## 2 Agent2：起中继 + 上闹钟

起两个**后台任务**（bash 用 `run_in_background`，PowerShell 用 `Start-Job`，别用 `nohup`/`&`），然后就可以结束会话：

```bash
python run.py serve     # ①中继：常驻，占端口，持有 Agent1 那条请求
python run.py watch     # ②闹钟：阻塞等任务，任务一到就退出
```

**闹钟退出 = 后台任务结束 = 你的会话被唤醒**，所以 `watch` 的 stdout 里就是任务本身。

被唤醒后（通知里带任务文本和 id），做三件事：

```bash
# 1. 干活，把结果整理成文本
python run.py reply --id 3 --text "结果"         # 2. 交结果，二选一
python run.py reply --id 3 --file .tmp/结果.md   #    长结果；先写进 .tmp/（mkdir -p .tmp）
python run.py watch                              # 3. 重新上闹钟（后台任务），再结束会话
```

`--file` 只是"从文件读文本"，只读一次、不生成也不删文件；本工具自己只写 `data/i{id}.md` 和 `data/o{id}.md`。**要落文件一律写进 `.tmp/`**（先 `mkdir -p .tmp`）。

铁律：

- **临时文件只写 `.tmp/`**，仓库根目录和 `agentchat/` 里不许留任何产物。
- 中继和闹钟都必须是**后台任务**；前台跑 `watch` 会一直阻塞。
- 闹钟**不能**自己拉起中继：闹钟结束时它的整棵进程树会被回收，派生出来的中继活不过它。
- 一次唤醒只对应一个任务。`reply` 之后必须重新 `watch`，否则下一单没人接、只能等超时。
- 中继不用重启，每轮重启的只是闹钟。

## 3 Agent1：发任务

```bash
python run.py send --text "帮我统计项目里有多少个 Python 文件"
```

阻塞到结果回来，stdout 就是结果，**最多等 20 分钟**（`--timeout`，中继侧同样 1200s）。

超时（退出码 3）会把 id 打给你，事后 `python run.py wait --id 3` 补取，别重发。
想先拿 id 再慢慢等：`send --detach` → `wait --id 3`。
**调用方的命令超时短于 20 分钟时，必须先 `--detach`**，否则进程被杀、id 也一起丢了。

## 4 退出码

| 码 | 含义 | 处理 |
| --- | --- | --- |
| 0 | 成功（send 拿到结果 / watch 收到任务） | — |
| 2 | `--once` 等了一轮没任务 | 正常，继续 |
| 3 | 超时：20 分钟内没有结果 | 用 `wait --id N` 补取 |
| 4 | 连不上中继 | 让 Agent2 起 `serve` |
| 5 | 闹钟上不去（中继不在） | 先 `serve`，再 `watch` |

## 5 排障

| 现象 | 处理 |
| --- | --- |
| health 显示「未上闹钟」 | 没有 watch 在等，任务会躺着直到超时 |
| `send` 立刻报「连不上」 | 中继没在跑，起 `serve` |
| `reply` 报 404 | id 不对，或中继重启过（内存队列已丢） |
| 通知里说「中继没在运行」 | 中继挂了，起 `serve` 再 `watch` |
| 401 | 两端 `--token` 不一致 |
| 中文变成 `��ã�` | 调用方按 GBK 解码了；工具输出固定 UTF-8，让调用方按 UTF-8 读 |

`python run.py health` 随时可查：中继在不在、闹钟在不在、队列里几单。

## 6 落盘

`data/i{id}.md` 是第 id 轮的任务，`o{id}.md` 是结果，id 从 1 递增。
两端各写一份、内容一致；**只写不读**，绝不用 `data/` 传递信息。
