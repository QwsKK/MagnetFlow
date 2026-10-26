# 磁力采集器

配置驱动的静态站点磁力采集工具。站点规则写在 YAML 里，脚本本身不含任何站点特有逻辑。

采集结果落 SQLite（infohash 级去重，重复运行只产出新链接），并导出 JSON / CSV / 纯磁力 TXT。

## 快速开始

运行环境：系统 Python `D:\DevHub\python\python.exe`（3.11.7），依赖已装好，不用虚拟环境。
`D:\DevHub\python` 已加入系统 PATH，重开终端后 `python` 命令直接可用。

```powershell
# 1. 复制站点模板，填好选择器
Copy-Item sites\example.yaml sites\mysite.yaml

# 2. 把 enabled 改成 true，先小范围试跑
python harvest.py --site mysite --pages 1 1 --limit 5 --dry-run

# 3. 确认结果没问题，正式跑
python harvest.py --site mysite
```

如果不想依赖 PATH，也可以用启动包装或完整路径，效果一样：

```powershell
.\run.cmd --site mysite              # 启动包装，内部固定调用系统 Python
py harvest.py --site mysite          # py 启动器默认就是 3.11
D:\DevHub\python\python.exe harvest.py --site mysite
```

### 关于 `python` 命令的历史问题（已解决）

之前 PATH 里只有 `D:\DevHub\python\Scripts`，缺了 `D:\DevHub\python` 本身，导致 `python` 命中微软商店的应用别名存根（`C:\Users\...\WindowsApps\python.exe`）——它只静默弹商店，不报错也不执行，看起来像"没反应"。

**本机已解决**：`D:\DevHub\python` 已加入系统 PATH（Machine），且排在 WindowsApps 存根之前，`python` 现在解析到 `D:\DevHub\python\python.exe`。

新装的机器若遇到同样现象，两个修法选一个：

```powershell
# 方案 A：把 python.exe 所在目录加进用户 PATH（重启终端后生效）
[Environment]::SetEnvironmentVariable("Path",
    [Environment]::GetEnvironmentVariable("Path","User") + ";D:\DevHub\python", "User")

# 方案 B：关掉商店别名（设置 → 应用 → 高级应用设置 → 应用执行别名 → 关闭 python.exe 和 python3.exe）
```

## 配置站点

`sites/example.yaml` 里有完整字段注释。最小可用配置只有四项：

```yaml
name: mysite
enabled: true

request:
  base_url: "https://example.com"

list:
  url_template: "https://example.com/vod/list/page/{page}.html"   # 页码位置写 {page}
  # 搜索型站点：关键词位置写 {keyword}，运行时用下面 keyword 或 --keyword 替换
  # url_template: "https://example.com/search/{keyword}/{page}"
  keyword: "8k"                                                   # 默认搜索词（可省略）
  pages: [1, 3]
  item_selector: "div.vod-list > div.item > a"                    # 指向详情页的 <a>

detail:
  magnet_attr_selectors:
    - "a[href^='magnet:']"
```

填选择器的办法：浏览器打开列表页 → F12 → 右键目标链接 → 复制 selector。

几个容易踩的点：

- **中文/日文乱码** → `request.encoding` 改成 `gbk`（国内老站常见）。不确定就写 `auto`：脚本优先采信页面 `<meta charset>` 的自报编码，并会在解出乱码时自动换编码重试（日志里会打「按 gbk 解码疑似乱码」）
- **要登录才看得到** → 在 `request.headers` 里贴浏览器 Cookie
- **磁力抓不到** → 脚本会依次尝试「元素 href → 页面文本 → 原始 HTML 正则」，通常够用
- **磁力是异步加载的**（详情页 HTML 里只有表头 + 「读取中」占位符）→ 在 `detail.ajax` 里配上 XHR 接口地址，脚本会额外请求一次该接口并把返回的片段并入详情 HTML（见 `sites/mysite.yaml` 注释）。接口校验 Referer 的，用 `ajax.referer: true`（默认）带上即可；接口地址以 DevTools → Network → Fetch/XHR 里实际看到的为准
- **体积取错**（文件名里的 `8K`、`720p` 被当成体积）→ 配 `detail.magnet_size_selector`，指定该磁力所在 `<tr>` 里体积单元格的位置，比在整行文本里猜要准
- **限速** → `config/default.yaml` 里 `request.delay: [1.0, 2.5]` 是每次请求的随机等待区间，默认对站点比较友好，不建议调太快

## 命令行

| 参数 | 作用 |
| --- | --- |
| `--site NAME` | 指定 `sites/NAME.yaml` |
| `--list-sites` | 列出所有站点配置及启用状态 |
| `--pages 1 5` | 临时覆盖配置里的页码区间 |
| `--keyword 4K` | 列表页模板含 `{keyword}` 时替换进 URL（可重复传，取第一个）；否则并入「包含关键词」过滤 |
| `--limit 10` | 本次总共只处理 10 个详情页（跨所有列表页累计），调试用 |
| `--dry-run` | 只跑不写数据库 |
| `--include-seen` | 忽略历史去重，重新输出全部 |
| `-v` | 输出调试日志 |

## 输出

```
out/<site>/magnets-20260830-102400.json     # 完整结构化数据
out/<site>/magnets-20260830-102400.csv      # 表格
out/<site>/magnets-20260830-102400.txt      # 纯磁力，每行一条
out/<site>/latest.{json,csv,txt}            # 最新一次快照，覆盖式
out/<site>/detail-20260830-102400.json      # 详情页原始 HTML（需 output.dump_detail_html: true）
out/<site>/latest_detail.json               # 同上，覆盖式快照
```

JSON 按详情页分组，一个详情页一个对象，它下面的全部磁力放在 `magnets` 数组里：

```json
{
  "site": "mysite",
  "count": 2,
  "magnet_count": 3,
  "items": [
    {
      "site": "mysite",
      "title": "详情页标题",
      "source_url": "https://.../ABC-123",
      "magnet_count": 2,
      "magnets": [
        {
          "infohash": "6cd66b80...",
          "magnet": "magnet:?xt=urn:btih:6cd66b80...&dn=abc-123",
          "filename": "abc-123",
          "size_gb": 16.1,
          "extension": ""
        }
      ],
      "first_seen": "2026-08-30T15:09:07"
    }
  ]
}
```

`count` 是详情页（分组）数，`magnet_count` 是磁力总条数。每条磁力的 `size_gb` 是它自己的体积，取不到时才退回详情页级体积。

CSV / TXT 是二维结构，会摊平成**每行一条磁力**，并重复带上所属详情页的 `title` 和 `source_url`；CSV 字段为 `infohash / title / filename / size_gb / extension / magnet / source_url / first_seen`。

`dump_detail_html` 打开后，每个抓到的详情页会额外存一份完整 HTML 原文（`source_url / list_title / title / size_gb / magnet_count / magnets / html`），不参与去重和过滤——调试选择器或事后补字段时很有用，但文件较大，默认关闭。

## 数据库

`state/harvest.db`，表 `magnets`。去重和推送队列都以 infohash 为单位，所以这里**按磁力一行一条**存储（不是分组结构），`title` 取所属详情页标题：

| 字段 | 说明 |
| --- | --- |
| `infohash` | 主键，去重依据 |
| `site` | 来自哪个站点配置 |
| `magnet` | 完整磁力链接 |
| `title` / `filename` / `size_gb` / `extension` | 解析出的元信息 |
| `source_url` | 所属详情页，和 JSON 的分组对应 |
| `first_seen` | 首次收录时间 |
| `pushed` | **预留**，接 115 后标记是否已推送，默认 0 |

## 下一步：接 115 云添加

采集段已经跑通，115 那段的接入位置很明确——读 `pushed = 0` 的记录，批量提交，成功后置 1：

```sql
SELECT infohash, magnet, title FROM magnets WHERE site = 'mysite' AND pushed = 0;
```

115 侧用哪个库：

- `p115client`（[ChenyangGao/p115client](https://github.com/ChenyangGao/p115client)）—— 封装了 web / app / open 三类接口，Cookie 方式扫码登录一次即可，无需申请审核。它带 `clouddownload` 相关模块，就是云下载（云添加）那套。
- `py115`（[silencoo/py115](https://github.com/silencoo/py115)）—— 更轻量，`offline.add_url(...)` 直接支持 magnet / ed2k / http。

接入时先在交互式环境里用**单条**磁力试通接口，确认接口名和返回结构（库版本差异较大，别照抄文档里的名字），再套到批处理上。115 对批量提交有频率和并发限制，建议串行 + 失败重试 + 记录结果。

需要的时候直接说，我把这段补成一个 `push115.py`。

## 冒烟测试

```powershell
py tests\run_smoke.py
```

会生成一个本地静态站点（含翻页、跨页重复磁力、GBK 编码、纯文本磁力、应被过滤的条目），起本地服务跑完整链路并断言结果。不联网，可以放心跑。

测试脚本内部会把子进程的 `PYTHONIOENCODING` 固定成 UTF-8，所以断言不受终端代码页影响。

## 说明

工具本身是通用的页面采集框架，不含任何具体站点的解析规则。请用在自己有合法权利的内容上——自制视频、已授权素材、公有领域或开源资源。批量搬运受版权保护的影视作品不在本工具的用途范围内。
