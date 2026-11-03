# AgentChat

本地双 Agent 轻量级通信工具。基于 Python FastAPI，只做三件事：**用 HTTP 传任务**、**用 HTTP 传结果**、**把每轮交流落盘**。

- Agent2 起一个**中继**（常驻）和一个**闹钟**（等任务），然后会话就可以结束；
- Agent1 `POST` 任务文本并阻塞等待，最多 20 分钟；
- 任务一到，闹钟进程**退出**，后台任务结束 → Agent2 的会话被唤醒；
- 每轮交流写成 `data/i{id}.md`（输入）和 `data/o{id}.md`（输出），id 从 1 递增；
- 两端**只通过 HTTP 交流**，谁都不读 `data/` 来拿任务或结果 —— 那只是日志。

## 目录结构

```
AgentChat/                  ← 工具根目录
├── run.py                  # 唯一入口：免安装启动器（任何目录可用）
├── SKILL.md                # 给 Agent 用的使用说明
├── README.md               # 本文件
└── agentchat/              # 代码
    ├── server.py           # 中继：FastAPI、任务队列、挂起 /task、唤醒 /reply
    ├── beacon.py           # 闹钟：长轮询等任务，任务一到打印并退出
    ├── hub.py              # 中继探测与停止
    ├── client.py           # 纯标准库 urllib 客户端
    ├── store.py            # data/i{id}.md · o{id}.md 落盘
    ├── cli.py              # serve / watch / send / reply / wait / health / stop
    └── requirements.txt    # fastapi / uvicorn / pydantic
```

下文命令都在**工具根目录**执行（`python run.py ...`）。

## 为什么是两个进程

Agent2 是 LLM 会话，它不能像程序一样常驻，只能"执行命令 → 等结果 → 再执行"。所以要让它"睡着但能被叫醒"，只有一条路：**让它挂一个后台任务，任务结束会把它唤醒**。

但一个进程没法同时做两件事：

| | 必须做到 | 结论 |
| --- | --- | --- |
| 闹钟 | 收到任务后**立刻死掉**，死掉才能唤醒 Agent2 | 一次性进程 |
| 中继 | **活着**持有 Agent1 那条 HTTP 请求，直到结果回来 | 常驻进程 |

所以拆开：中继常驻、闹钟一次性。Agent1 的请求挂在中继上，闹钟的死活与它无关。

> 实测注意：闹钟结束时，它派生出去的一切（包括 `DETACHED_PROCESS`、`CREATE_BREAKAWAY_FROM_JOB`、`ShellExecute` 起的进程）都会被一起回收。所以**中继必须自己是一个后台任务**，不能由闹钟拉起。

## 快速开始

**Agent2 —— 起两个后台任务，然后结束会话：**

```bash
python run.py serve      # ①中继：常驻
python run.py watch      # ②闹钟：阻塞，任务一到就退出
```

```
──────────────────────────────────────────────────────────────
 agentchat 0.1.0 · 中继常驻
 地址      http://127.0.0.1:18765
 数据目录  ...\data  (i{id}.md / o{id}.md)
 等待上限  1200s（Agent1 单次等结果）
 鉴权      off
 闹钟      由 watch 挂载；本进程只负责收发，不会思考
──────────────────────────────────────────────────────────────
```

**Agent1 —— 发任务：**

```bash
python run.py send --text "帮我统计项目里有多少个 Python 文件"
# → 阻塞，最多 20 分钟，stdout 直接就是结果文本
```

**闹钟被唤醒**，`watch` 的后台任务结束，输出里就是任务本身：

```
==============================================================
 agentchat 唤醒：收到任务 #1
==============================================================
帮我统计项目里有多少个 Python 文件

==============================================================
 醒来后三件事（做完就结束会话）
==============================================================
 1. 干活，把结果整理成文本（临时文件只写 .tmp/）
 2. python run.py reply --id 1 --text "结果"    # 短结果
    python run.py reply --id 1 --file .tmp/结果.md   # 长结果，先 mkdir -p .tmp
 3. python run.py watch     # 重新上闹钟（后台任务），再结束会话
==============================================================
```

Agent2 干完活，交结果并重新上闹钟：

```bash
python run.py reply --id 1 --text "pong"          # 短结果直接写
python run.py reply --id 1 --file .tmp/答案.md    # 长结果：先写进 .tmp/
python run.py watch                               # 下一轮；中继不用重启
```

`--file` 只是"从文件读文本"，读一次就发出去。**工具不生成、也不删除任何结果文件** —— `.tmp/答案.md` 是 Agent2 自己干活时写下的，跟本工具无关。本工具只写 `data/i{id}.md` 和 `data/o{id}.md`。

约定：**临时文件一律写进 `.tmp/`**（已在 `.gitignore` 里），别在仓库根目录或 `agentchat/` 里留产物。

## 时间规划

| 谁 | 等什么 | 等多久 | 到点之后 |
| --- | --- | --- | --- |
| Agent1 `send` | 等结果 | **20 分钟**（`--timeout`，中继侧 `--wait-timeout` 同值） | 退出码 3，打印 id，可 `wait --id N` 补取 |
| Agent2 `watch` | 等任务 | 单轮 1 小时（`--wait`），**到点无缝续上** | 只在真没任务时续，不会漏 |
| Agent2 干活 | — | **不限** | 干一小时也不会被打断，Agent1 那头挂着 |
| 中继 | 挂起 `/task` | 20 分钟 | 返回 504，任务留在队列里等晚到的回复 |

Agent2 侧没有"轮询空档"：会话是睡着的，不是每 120 秒醒一次问"有活吗"。

## 命令

| 命令 | 角色 | 说明 |
| --- | --- | --- |
| `serve` | Agent2 | 启动中继（**后台任务**，常驻）。`--host --port --data-dir --token --wait-timeout` |
| `watch` | Agent2 | 上闹钟（**后台任务**，一次性）。`--wait --once` |
| `reply` | Agent2 | 交回结果，`--id` + `--text`/`--file`（`--file -` 读 stdin） |
| `send` | Agent1 | 发任务并阻塞等结果，`--timeout`、`--detach` |
| `wait` | Agent1 | 等待某个 id 的结果（超时后补取） |
| `health` | 任意 | 中继在不在、闹钟在不在、队列几单 |
| `stop` | 任意 | 停掉中继 |
| `next` | 调试 | 低层接口，手动领一次；正常流程用 `watch` |

通用参数：`--url`（默认 `http://127.0.0.1:18765`）、`--token`。

## HTTP 协议

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/task` | `{"text":"...","wait":true,"timeout":1200}` → `{"id":1,"reply":"..."}`；超时 504 |
| `GET` | `/next?wait=3600` | 领任务（闹钟用）；无任务 204 |
| `POST` | `/reply` | `{"id":1,"text":"..."}` → 唤醒挂起的 `/task` |
| `GET` | `/result/{id}` | `{"id":1,"status":"pending\|answered\|timeout","reply":...}` |
| `GET` | `/health` | 运行状态，含 `watching`（在线闹钟数） |
| `POST` | `/shutdown` | 停掉中继 |

带 `--token` 时需附 `X-Agent-Token` 或 `Authorization: Bearer`。交互式文档在 `/docs`。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | `watch --once` 等了一轮没任务 |
| 3 | 等结果超时（504），任务已入队，晚到的回复仍会落盘 |
| 4 | 连不上中继 / 鉴权失败 |
| 5 | 闹钟上不去：中继没在运行 |

## 设计边界

- **data 只写不读**：落盘仅用于留档和 id 续编（只扫文件名）。两端若共用同一目录，写入内容完全一致，不会冲突。
- **中继重启**：id 接着历史最大值继续；内存任务表清空，重启前未完成的 id 用 `/result` 查会 404。
- **单机单进程**：uvicorn 单 worker，任务按 FIFO 处理；同一时刻多个 `send` 会排队，不会串号。
- **闹钟只有一次**：一次唤醒对应一个任务，`reply` 之后必须重新 `watch`。`health` 里 `watching=0` 就表示现在没人接单。
- **没有闹钟时**：任务照样入队，但没人领，Agent1 等到 20 分钟超时；任务不会丢，Agent2 回来上闹钟即可领到。
- **编码**：全链路 UTF-8。启动时会把 stdin/stdout/stderr 强制改成 UTF-8 —— Windows 上 Python 写管道默认用本地代码页（GBK），调用方（codex 等）按 UTF-8 读就会得到 `��ã�` 这种乱码，不设置 `PYTHONIOENCODING` 也不会再出现。`--file` 读文本时容忍 BOM，UTF-8 解不开会自动按 GBK 再试一次。

## 自测

```bash
python run.py serve --data-dir .tmp/data &   # 后台任务①
python run.py watch &                        # 后台任务②（在 Agent 里用 run_in_background）
python run.py health                         # 应显示「已上闹钟（1 个 watch 在线）」
python run.py send --text "ping" --data-dir .tmp/c   # 阻塞；闹钟立刻结束
python run.py reply --id 1 --text "pong"     # Agent1 拿到 pong
python run.py watch &                        # 重新上闹钟
```

## 给 Agent 装 skill

把根目录的 `SKILL.md` 复制成 `~/.workbuddy-ai/skills/agentchat/SKILL.md`（或项目内 `.workbuddy-ai/skills/agentchat/SKILL.md`）。交给 Agent2，它就会起中继和闹钟；交给 Agent1，它就只用 `send`。
