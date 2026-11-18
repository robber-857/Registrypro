# Registrypro

按澳洲邮编整理 Active ABN 主体和商号，在 Chrome 中核对 ASIC 商号续期日，导出 Excel 或 CSV。工作台运行在使用者自己的 Windows 电脑上。

## 在新电脑上部署

准备 **Windows、Python 3.10 或以上版本、Git 和桌面版 Google Chrome**。下面的命令在 PowerShell 中执行。ABN 官方快照和基本网页查询使用 Python 标准库，无需 ABR GUID。可选 OCR 配置见 [OCR 说明](docs/OPENAI-OCR.md)。

**1. 获取项目并确认 Python 可用**

```powershell
git clone https://github.com/robber-857/Registrypro.git
cd Registrypro
python --version
```

**2. 初始化 ABN 官方数据**。要使用「ABN 官方公开数据」来源，在项目根目录执行下面的命令，等待两份官方压缩包下载并完成 SQLite 索引构建。下载和索引都写入 `output/abn_data/`。预留数 GB 空间，首次启动时间取决于网络和磁盘。更新官方快照时可重新执行同一条命令。仅使用地图来源时可跳过这一步。

```powershell
python sydney_business_leads/update_abn_data.py
```

**3. 启动工作台**。双击根目录的 `start.cmd`，或在 PowerShell 中运行：

```powershell
.\start.cmd
```

在准备运行插件的 Chrome 窗口打开工作台，看到「输入邮编，整理商家名单」即表示本机网页服务已启动。保持启动窗口打开。若端口 8765 已被占用，先核对占用程序；插件默认连接此端口。[打开本机工作台](http://127.0.0.1:8765/)

**4. 在同一个 Chrome 配置中安装扩展**。地址栏打开 `chrome://extensions/`，开启「开发者模式」，点「加载已解压的扩展程序」，选择仓库里的 `browser_extension` 文件夹（其中包含 `manifest.json`）。确认扩展列表显示 **Registrypro ASIC Assistant**，可从拼图菜单将它固定到工具栏。

**5. 运行第一批**。在主页选 **NSW、2134、ABN 官方公开数据、25 个主体**，点「开始自动整理」。名单生成后，展开「ASIC 浏览器插件」并复制配对码。在工具栏 R 插件粘贴配对码，选择「最近一次查询」，取消「等待系统连续邮编队列」，点「连接并开始」。队列完成后，从主页下载续期日 Excel。连续邮编、暂停恢复和结果字段的解释见[完整中文使用说明](使用说明.md)。

**版本范围。** 此仓库当前插件为 2.4.0。主页提供查询表格与 Excel / CSV 下载；独立的 20 行分页结果页及批末 Cookie 清理属于本机 2.4.1 工作版，此次文档更新未发布对应代码。

## 功能

- 无需 GUID 的公开商家目录查询与网站联系方式提取。
- 连续邮编任务：填写起始及可选结束邮编，每批最多 20 家，间隔可选 5/15/30/60 秒；每个邮编独立保存 CSV，并支持暂停、重启后继续和跳过。文件位于 `output/automation/`。
- 搜索进度、停止查询、缓存、结果去重、失败标记与来源记录。
- 按实际州和邮编筛选，邻区候选单独标注。
- 初步服务方向建议，明确标注未经商家确认。
- 自动注册资料研究：新查询自动执行，已有名单支持批量／单行补查。提取网站 ABN 并读取官方详情；唯一编号、名称匹配、门店电话与邮编及 Active 状态满足规则时填入注册名称，否则保留候选和原因。人工核实记录受保护。原始 Burwood XLSX 不含新增核验字段，请重新下载 CSV。
- Business Name 续期日与公司年度审核日分列。自动尝试读取对应 ASIC 摘要；JavaScript 页面或来源失败时日期留空并记录原因，不保证日期自动取得、不推算日期。
- 附带 20 家 Burwood 商家的官网资料核对名单，以及 XLSX / CSV 文件。
- 保留官方 ABN API 查询和离线批量数据筛选模式。

## 工作流 v2 实跑

原始 20 家 ABN 与 ASIC 队列已完整处理：最终 5 家确认主体、4 个确认续期日、7 个候选续期日。ASIC 实跑包含人工刷新与修复恢复，不代表无人值守稳定性已经通过。支持单 ABN 首选名称、缓存／强制刷新、暂停恢复、默认关闭公司审核日查询及本机 OCR 备用。详细验证范围见验证记录。

## 文件与使用说明

- [完整中文使用说明](使用说明.md)
- [官方 ABN 查询脚本说明](sydney_business_leads/README_zh.md)
- [Burwood 20 家商家 Excel](outputs/burwood/Burwood_20_businesses.xlsx)
- [Burwood 20 家商家 CSV](outputs/burwood/Burwood_20_businesses.csv)
- [验证记录](VALIDATION-2026-11-17.md)

## 测试

```powershell
cd sydney_business_leads
$env:PYTHONUTF8 = "1"
python -m unittest discover -v
```

94 项 Python 与 10 项 JavaScript 本地测试覆盖原有 ABN 流程、商家查询、连续邮编任务，以及自动注册研究、名称核验、日期来源、编号校验和 CSV 导出。网页自动注册研究无需 GUID；原有 ABN Web Services 认证查询仍需配置自己的 `ABR_GUID`。

## 数据范围

可选择 Bing Maps 公开目录或 Google Places Text Search (New)。Google 模式需配置 GOOGLE_PLACES_API_KEY；未配置时明确报错，不回退 Bing。本机真实 Google 联调尚未进行。公开页面可能遇到改版或访问限制。它生成可进一步核查的商家候选名单，不是该邮编全部注册公司的名册。目录联系方式、网站归属与服务需求须复核；没有找到网站不代表没有官网。ABN 不做推测匹配。

连续任务在所选行业分页没有新增条目、或达到每行业 5 页安全上限后保存并切换邮编；该结束条件不代表全量覆盖。无法识别的空响应和目录错误会暂停。数字递增不等于地理相邻，也不会自动切换州；结束邮编留空以 9999 为终点。

静态 Burwood 名单核查日期为 2026-11-17，公开联系方式不代表已完成电话或邮件验证。工具不发送营销信息。仅监听本机地址，不对公网开放。

缓存、运行日志、临时下载和 API 凭证不提交到版本库。
