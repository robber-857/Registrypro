# Registrypro

按澳洲邮编整理公开商家候选名单的本地工具。中文界面、官网联系方式提取、CSV 导出，以及独立的官方 ABN Lookup / Bulk Extract 查询脚本。

## 快速开始

需要 Python 3.10 或更新版本，仅使用标准库，无需安装额外依赖。

```powershell
git clone https://github.com/robber-857/Registrypro.git
cd Registrypro
python sydney_business_leads/web_app.py
```

Windows 也可以双击根目录 `start.cmd`。打开 http://127.0.0.1:8765 ，输入邮编、选择州和行业，再点击“开始自动整理”。查询结果可下载为 Excel 能打开的 CSV。

默认包含目录返回的同州邻区候选，并分别记录实际邮编。取消勾选邻区选项可严格按目标邮编筛选。数量上限为 1–100，结果受来源覆盖和访问限制影响。

## 功能

- 无需 GUID 的公开商家目录查询与网站联系方式提取。
- 连续邮编任务：填写起始及可选结束邮编，每批最多 20 家，间隔可选 5/15/30/60 秒；每个邮编独立保存 CSV，并支持暂停、重启后继续和跳过。文件位于 `output/automation/`。
- 搜索进度、停止查询、缓存、结果去重、失败标记与来源记录。
- 按实际州和邮编筛选，邻区候选单独标注。
- 初步服务方向建议，明确标注未经商家确认。
- 附带 20 家 Burwood 商家的官网资料核对名单，以及 XLSX / CSV 文件。
- 保留官方 ABN API 查询和离线批量数据筛选模式。

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

52 项本地测试覆盖原有 ABN 流程、商家查询，以及连续邮编分批、递增、恢复、分页上限、文件保存失败与导出行为。真实 ABN 认证查询仍需配置自己的 `ABR_GUID`。

## 数据范围

自动收集读取 Bing Maps 公开目录网页及其列出的网站，不使用稳定的官方目录 API，可能遇到改版或访问限制。它生成可进一步核查的商家候选名单，不是该邮编全部注册公司的名册。目录联系方式、网站归属与服务需求须复核；没有找到网站不代表没有官网。ABN 不做推测匹配。

连续任务在所选行业分页没有新增条目、或达到每行业 5 页安全上限后保存并切换邮编；该结束条件不代表全量覆盖。无法识别的空响应和目录错误会暂停。数字递增不等于地理相邻，也不会自动切换州；结束邮编留空以 9999 为终点。

静态 Burwood 名单核查日期为 2026-11-17，公开联系方式不代表已完成电话或邮件验证。工具不发送营销信息。仅监听本机地址，不对公网开放。

缓存、运行日志、临时下载和 API 凭证不提交到版本库。
