# 字幕流水线（rawsub）

115 网盘视频自动中文字幕流水线。

每晚挂机跑一遍，脚本自动扫描片库里没有中文字幕的片子 → 抽音轨 → Whisper 转写 →
本地大模型（llama.cpp + GGUF）翻译成简体中文 → 生成 `.zh.srt` 回写到原目录。
第二天在任何播放器里打开都带中文字幕。

全程本地推理，无 API 费用，不改动 OpenList 里的文件结构。

---

## 目录结构

```
subtitle/
├─ run.py                  统一入口（所有命令都从这里进）
├─ config.yaml             你的配置（由 config.example.yaml 复制而来，含密码，不入库）
├─ requirements.txt
│
├─ rawsub/                 核心包，按职责分层
│  ├─ config.py            配置模型（dataclass + YAML 加载 + 校验 + ${ENV} 占位符）
│  ├─ cuda.py              Windows 下修复 CTranslate2 找不到 cuDNN/cuBLAS DLL
│  ├─ logging_setup.py     日志
│  ├─ errors.py            分层异常类型
│  ├─ utils.py             格式化 / 重试 / 分批 / 幻觉过滤
│  │
│  ├─ webdav/
│  │  ├─ client.py         PROPFIND / GET(Range 续传) / PUT / MKCOL，auto 认证
│  │  └─ scanner.py        递归扫描片库，产出「待处理」视频列表
│  ├─ media/
│  │  ├─ ffmpeg.py         定位 ffmpeg、拼带认证的 URL、探测音轨
│  │  └─ audio.py          音轨抽取：流式优先 + 整片下载兜底 + 卡死看门狗
│  ├─ asr/
│  │  ├─ srt.py            SRT 解析 / 生成 / 长句切分 / 短句合并
│  │  └─ transcriber.py    faster-whisper 封装，显式 load/unload 管理显存
│  ├─ llm/
│  │  └─ server.py         llama-server 进程托管（启动 / 健康检查 / 关闭）
│  ├─ translate/
│  │  ├─ base.py           ChatClient 抽象（换后端只实现这一个接口）
│  │  ├─ openai_compat.py  OpenAI 兼容客户端，显式关闭 thinking
│  │  ├─ prompts.py        提示词 + 编号格式 + 响应解析
│  │  └─ batcher.py        分批 → 行数校验 → 重试 → 二分降级
│  ├─ state.py             SQLite 状态库（增量、断点续跑、失败重试）
│  ├─ pipeline.py          分阶段编排
│  ├─ notify.py            Server酱 / Bark 推送
│  └─ cli.py               命令行
│
├─ scripts/
│  ├─ install.ps1          一键装机（venv + 镜像源 + ffmpeg + llama.cpp + 模型 + 配置）
│  ├─ fetch_models.py      模型下载（ModelScope 优先，hf-mirror 兜底，支持续传）
│  ├─ run_nightly.bat      定时任务入口
│  └─ register_task.ps1    注册 Windows 计划任务
│
├─ bin/                    ffmpeg、llama.cpp（安装脚本生成）
├─ models/                 GGUF 与 Whisper 模型（安装脚本生成）
├─ work/                   临时音轨与中间字幕
├─ data/state.db           处理进度
└─ logs/
```

---

## 快速开始

### 1. 装机

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

会依次完成：建 venv → 用清华源装依赖 → 下 ffmpeg → 下 llama.cpp（CUDA 预编译）→
下模型 → 生成 config.yaml → 跑自检。

已经装过一部分的，可以用开关跳过：

```powershell
.\scripts\install.ps1 -SkipFfmpeg -SkipLlamaCpp       # 只补依赖和模型
.\scripts\install.ps1 -SkipModels -SkipDoctor         # 只装工具和依赖
.\scripts\install.ps1 -Quant Q5_K_M                   # 换更大 quantization
.\scripts\install.ps1 -PipMirror https://mirrors.aliyun.com/pypi/simple
```

### 2. 填配置

编辑 `config.yaml`，至少改这几项：

```yaml
webdav:
  base_url: "http://192.168.1.100:5244/dav"
  username: "admin"
  password: "your_password"
  root: "/电影"
```

### 3. 自检 + 试跑

唯一入口是项目根目录的 `run.bat`（自动使用 venv 的 Python）：

```powershell
run.bat doctor          # 基础自检
run.bat doctor --deep   # 把两个模型真加载一遍
run.bat scan --limit 10 # 看看待处理清单
run.bat run --limit 1   # 先拿一部试水
```

**直接双击 `run.bat`** 就是跑完整流水线，跑完窗口会停住显示结果。

### 4. 挂机

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1 -Time 23:00
```

想「睡前点一下，早上全部处理完」，也可以直接双击 `run.bat`（等价于 `run` 全流程），
再把 `config.yaml` 里的 `pipeline.shutdown_after_run` 改成 `true`。

---

## 命令一览

> 以下 `run.py` 均可通过 `run.bat` 调用，例如 `run.bat status`。

| 命令 | 作用 |
|------|------|
| `run.py init` | 从模板生成 `config.yaml` |
| `run.py doctor [--deep]` | 环境自检：Python / ffmpeg / CUDA / 模型 / WebDAV / 磁盘 |
| `run.py scan [--limit N] [--all]` | 扫描片库，列出待处理影片 |
| `run.py run [--limit N] [--dry-run] [--phases a,b]` | 跑流水线 |
| `run.py serve-llm [--keep] [--test]` | 单独拉起 llama.cpp（调试用） |
| `run.py test-llm [--text "..."]` | 用一句话验证翻译链路通不通 |
| `run.py status [--failed]` | 查看处理进度统计 |
| `run.py reset --failed / --all / --path <路径>` | 清除记录（不影响已上传的字幕） |

全局参数：`-c/--config` 指定配置文件，`-v/--verbose` 输出 DEBUG 日志。

`--phases` 可以只跑其中几段，排查问题很好用：

```powershell
run.py run --phases transcribe      # 只转写，不翻译不上传
run.py run --phases translate,upload
```

---

## 它是怎么跑的

按**阶段**而不是「一部片子从头跑到尾」，因为 8GB 显存塞不下两个模型：

```
阶段一 转写      Whisper 常驻显存，一部接一部转；音轨转完立刻删，磁盘占用有界
   │  抽音轨 → Whisper → .en.srt
   ↓ 卸载 Whisper，显存归还
阶段二 翻译      拉起 llama.cpp，一批批翻；翻完关掉服务，显存归还
   │  .en.srt → Qwen → .zh.srt
   ↓
阶段三 回写      PUT 到 115 原目录，秒级完成
   │  .zh.srt → WebDAV
```

每个阶段的进展都写进 `data/state.db`，跑到一半关机也不会白干，下次从断的地方继续。

### 几个关键设计

**音轨抽取：流式优先，整片下载兜底**
默认让 ffmpeg 直接以 WebDAV 的 HTTP 地址当输入边下边抽，只落一个 16kHz 单声道 WAV
（2 小时片约 230MB）。服务端支持 Range 时收益最大——ffmpeg 只取音频相关的字节块；
不支持 Range 时，至少也省了「先把 30GB 原片写进磁盘再读一遍」。
配了看门狗：输出超过 `audio.stall_timeout_s` 没增长就杀进程，自动切整片下载。

**翻译：行数校验是生命线**
LLM 偶尔吞行、加行，一旦发生后面所有时间轴全错位，片子基本废掉。三层防御：

1. 输入输出都用 `[n] 文本` 强格式，解析时校验编号序列完整连续；
2. 行数不一致就原批次重发（换严格措辞 + 温度归零）；
3. 一批重试耗尽后二分拆开各自再走一遍，直到单行为止。

实在不行按 `translate.on_failure` 处理——默认是 `keep_original`（保留原文，不破坏时间轴）。

**模型：llama.cpp + GGUF**
用独立进程而不是 `llama-cpp-python`，就为了显存能被**彻底释放**：进程一杀，显存立刻归还。
翻译模型在 `config.yaml` 的 `llm` 段指定，任意 llama.cpp 兼容的 GGUF 都行（示例配置里是一个 4B 量化模型）。
Qwen3 默认会输出思考内容，脚本在服务端（`--reasoning-budget 0`）和请求端
（`enable_thinking: false`）两处都关掉了，翻译能快一倍。

---

## 排错

| 现象 | 原因 / 处理 |
|------|-------------|
| `Could not load library cudnn_ops64_9.dll` | `cuda.py` 已自动注入 pip 装的 NVIDIA DLL 目录；仍报错就确认 `nvidia-cudnn-cu12` 装上了 |
| Whisper 报 CUDA 错后变很慢 | 自动降级到了 CPU。跑 `run.py doctor` 看 CTranslate2 那一项 |
| 流式抽取反复卡死 | 115 对 Range 限流了。把 `audio.strategy` 直接设成 `download` |
| 字幕时间轴错位 | 看日志里「行数校验失败」；加大 `translate.max_retries`，或调小 `batch_lines` |
| 翻译结果里夹英文/思考过程 | 确认 `llm.api.enable_thinking: false`，且模型是支持 `chat_template_kwargs` 的版本 |
| 挑错了音轨（选到国语配音） | 设 `audio.track_index`，或调整 `prefer_languages` / `avoid_languages` |
| 播放器里看不到字幕 | 确认 `output.encoding` 是 `utf-8-sig`；有些播放器需要在字幕菜单里手动选一次 |
| 想重做某部片子 | `run.py reset --path "/电影/xxx.mkv"` 再跑 |

---

## 配置速查

只列最常改的，完整说明见 `config.example.yaml`。

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `webdav.root` | `/电影` | 只扫这个目录 |
| `scan.min_size_mb` | 100 | 小于此体积视为预告片，跳过 |
| `audio.strategy` | `auto` | `stream` 流式 / `download` 整片 / `auto` 自动 |
| `audio.track_index` | `null` | 指定第几条音轨（从 0 起） |
| `asr.model` | `models/faster-whisper-large-v3` | 也可填 `medium` / `small` 降显存 |
| `asr.compute_type` | `int8` | 显存够可换 `int8_float16`，更快 |
| `llm.model_path` | `models/qwen3-4b.i1-Q4_K_M.gguf` | 换 8B 就改这里 |
| `llm.server.ctx_size` | 8192 | 加大 `batch_lines` 时同步调大 |
| `translate.batch_lines` | 40 | 每批发给模型的行数 |
| `pipeline.max_items_per_run` | 0 | 挂机建议设 15~20，控制单晚时长 |
| `pipeline.shutdown_after_run` | false | 跑完自动关机 |

密码不想写进文件，可以用环境变量占位符：`password: "${OPENLIST_PASS}"`。

---

## 换云端翻译（可选）

`translate` 那层只依赖 `ChatClient` 接口，换云端改配置就行，代码不用动：

```yaml
llm:
  api:
    base_url: "https://api.deepseek.com/v1"
    api_key: "sk-xxxxxxxx"
    model: "deepseek-chat"
    enable_thinking: false
```

因为不再需要本地服务，把 `llm.server.exe` 那一段留着也无妨——`api.base_url` 填了就会直连云端，
不会去拉 llama.cpp。
