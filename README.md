# MagnetFlow

**磁力采集 → 115 离线下载 → 自动中文字幕**，一条本地自动化流水线 + 一个 Web 控制台。

```
① 采集站点磁力 ──► ② 推送 115 云添加 ──► (115 自动下载落盘) ──► ③ WebDAV 字幕流水线
   harvester/          mflow/push115          WebDAV 挂载可见        subtitle/ (rawsub)
```

- **① 采集**：按 `harvester/sites/*.yaml` 里的站点规则抓磁力，SQLite 按 infohash 去重入库
- **② 推送**：库里未推送的记录逐条提交给 115「云添加」，成功打标记、失败下次重试
- **③ 字幕**：文件经 WebDAV（OpenList / Alist）可见后，自动抽音轨 → 本地 GPU 转写翻译 → `.zh.srt` 回写原目录，全程断点续跑

三段都能单独用，也能用 `run.bat chain` 一次性串起来。v0.2 起提供**本地 Web 控制台**（OpenList / qBittorrent 那种风格），全部配置在界面里改，不用读源码。

## 特性

- **零外链前端**：原生 HTML/CSS/JS，无 CDN、无框架、无字体图标库 —— 内网/无外网环境照样能用
- **配置外科手术式写回**：改一个字段只动那一行，注释、排版、键顺序原样保留；每次保存前自动备份，落盘前必过 YAML 语法校验
- **任务可挂起**：子进程支持挂起 / 恢复 / 终止（Windows 进程树），日志 1.5s 增量拉取、实时滚动
- **扫码登录 115**：不用抠浏览器 Cookie，手机扫一下就能拿到长效凭据
- **一键体检**：Cookie / WebDAV / 转写引擎三处连通性自检，红灯带修复引导

## 快速开始

需要 **Python 3.12+**（`p115client` 的硬性要求）和 Windows 10/11。

```bat
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

启动控制台（二选一）：

```bat
启动控制台.bat          :: 黑窗口=服务日志，浏览器自动打开
run.py web             :: 等价命令行
```

浏览器会打开 <http://127.0.0.1:8420/>。

## 控制台页面

| 页面 | 干什么 |
| --- | --- |
| 总览 | 统计卡片、环境红绿灯、一键体检 / 采集试运行 / 字幕清单、导出文件下载 |
| 磁力采集 | 选站点、页码、关键词、上限发起采集；任务可**挂起 / 恢复 / 终止**，日志实时滚动 |
| 磁力库 | 搜索 / 筛选 / 分页，勾选批量「标记已推送 / 取消 / 删除」，单行删除，点哈希即复制 |
| 推送 115 | 参数表单化编辑（**扫码登录** + 测试 Cookie + 测试保存目录），发起推送、每片选最大合集 |
| 字幕流水线 | WebDAV / 阈值 / 引擎路径表单化编辑（连通性测试），run / status / doctor 一键跑 |
| 站点规则 | 每站点一张卡：启用开关、规则表单、「原始 YAML」高级编辑（AJAX 规则走原始 YAML） |
| 设置 | 三份配置的完整表单 + 各自的「直接编辑 YAML」，数据目录一键打开 |

任务与日志页：表格列出任务（类型 / 任务 ID / 状态 / 开始 / 用时 / 退出码），点「日志」在下方终端里看实时输出，运行中的任务可直接挂起或终止。

## 配置

仓库里只有模板，真实配置一律不入库（`.gitignore` 已处理）：

```bat
copy config\push115.example.yaml  config\push115.yaml     :: 推送 115
copy subtitle\config.example.yaml subtitle\config.yaml     :: 字幕流水线
copy harvester\sites\example.yaml harvester\sites\mysite.yaml   :: 一个站点的采集规则
```

然后再把 `harvester\sites\mysite.yaml` 的 `name` 字段改成 `mysite`、`enabled: true`，填好选择器即可。

### 推送 115 的认证

**方式 A（推荐）**：控制台「推送 115」页点 **扫码登录** → 手机「115」App 扫码确认 → 凭据自动填入并保存。

**方式 B**：浏览器登录 <https://115.com>，F12 控制台执行 `document.cookie` 复制整串，粘进「115 Cookie」框 → 测试 → 保存。

**方式 C**：用 115 App 抓包的凭据格式 `UID=...; CID=...; SEID=...; KID=...`。

也可以用环境变量 `P115_COOKIE` 提供（优先级高于配置文件）。

> 推送走的是 115 **离线下载**接口，WebDAV 只能读写网盘文件、无法创建下载任务，两者不能互换。
> `save_path` 写 115 网盘内的目录；保存前点「测试保存目录」验证一下，解析不到会直接中止，不会偷偷落到默认目录。

### 字幕流水线的衔接

`save_path` 应该落在字幕配置 `webdav.root` 的扫描范围内，这样 115 下载完就能被扫到。文件够大（默认 ≥100MB）才会处理，冒烟测试可以把这个阈值调小。

## 命令行

```bat
run.bat chain                    :: 一键全流程：采集启用站点 -> 推送 -> 字幕
run.bat doctor                   :: 三合一环境体检
run.bat harvest --site mysite --pages 1 3 --limit 50 --dry-run
run.bat push --test
run.bat subtitle status          :: 字幕 CLI 透传（run/scan/status/reset/doctor）
run.py gui                       :: 备选的 tkinter 桌面界面
python run.py --help
```

局域网访问（手机上看控制台）：

```bat
run.py web --host 0.0.0.0 --no-browser
```

## 目录结构

```
MagnetFlow/
├── 启动控制台.bat / run.py web     Web 控制台入口（主界面）
├── app.py / 启动界面.bat           备选 tkinter 界面
├── run.py                          命令行入口（web/gui/chain/harvest/push/subtitle/doctor/setup）
├── requirements.txt                全项目依赖
├── config/push115.example.yaml     推送配置模板
├── mflow/
│   ├── web/server.py               FastAPI 后端（REST API 全量入口）
│   ├── web/static/                 控制台前端（原生 HTML/CSS/JS）
│   ├── yamlkit.py                  行级 YAML 写回（保注释、防写坏）
│   ├── tasks.py / proctree.py      任务管理（子进程 + 进程树挂起/恢复/终止）
│   ├── push115.py / chain.py       推送环节 / 一键全流程
│   └── app.py + tab_*.py           tkinter 界面页签
├── harvester/                      ① 采集（规则在 sites/*.yaml）
├── subtitle/                       ③ 字幕（rawsub 包 + WebDAV 客户端）
├── tests/                          UI / 接口冒烟脚本
└── logs/                           运行日志 + config_backups/（配置自动备份）
```

## 常见问题

- **总览里「115 Cookie」亮红灯**：还没配 Cookie，按上面「推送 115 的认证」走一遍；不影响采集和字幕
- **总览里「WebDAV」亮红灯**：确认 OpenList / Alist 服务在跑，且 `subtitle/config.yaml` 的 `base_url` 与账号密码正确
- **推送报 cookie 无效**：115 的凭据会过期，重新扫码即可；失败的记录不会标记，改好后直接再推
- **字幕体检说引擎不存在**：到「设置 → 字幕 → 引擎路径」填你自己的转写引擎可执行文件路径
- **某个站点抓 0 条**：站点改版了，去「站点规则」改选择器；异步接口的规则要到「原始 YAML」里改
- **配置改坏了**：`logs/config_backups/` 里按时间戳找回，拷回原位
- **想夜跑字幕**：`powershell -File subtitle\scripts\register_task.ps1` 注册 Windows 计划任务

## 免责声明

- 本项目只提供**技术工具**，不内置任何站点、不附带任何内容、不提供任何资源。
- 站点规则由使用者自行编写。请只在你**拥有合法访问权限**的站点上使用，并遵守目标站点的服务条款与当地法律法规；因使用本项目产生的任何后果由使用者自行承担。
- 与 115、OpenList、Alist 等第三方服务无任何关联，请遵守各自的服务协议。
- 作者不对因使用本工具导致的账号风险、数据丢失或法律纠纷负责。

## License

[MIT](LICENSE)
