# 验证记录

日期：2026-11-16。环境：Python 3.12.14；程序目标最低版本 Python 3.10。

## 已完成

- 官方 ABN Lookup 方法、响应字段、实体类型、公开地址范围和当前 API schema 核对。
- `python3 -m unittest discover -v`：**31 项测试通过**（API 13、bulk 7、CLI 11）。
- 通过合成 XML 的 CLI 实际导出验证：默认 2 个 active PRV/PUB 主体，3 行 registered business names。
- 多 business names、主体去重、无 business name 的 legal-name 回退、sole trader、取消状态、跨州、suburb 的邮编范围说明。
- XML 命名空间、ZIP/GZIP 流式读取、目录递归、损坏文件的部分结果标记。
- API 响应数量不一致与重复 ABN 检查、GUID 脱敏、HTTP 重试、SQLite 缓存命中和失效。
- 明确标注 `--limit` 的部分结果；已有输出不被失败任务替换；CSV 公式转义。
- 复核并修正显式 demo 地区选择的问题；针对已匹配 ABN 的冲突名称记录新增失败保护。

## 未完成的真实联调

- 未提供用户的 ABN Lookup GUID，因此没有执行已认证的真实 API 名单查询。
- data.gov.au 数据集下载端在本环境返回 HTTP 403，因此没有下载、跑完或确认当前全量发布的各分卷。
- bulk 解析测试基于官方说明结构构造的合成 XML，不能替代当期完整生产数据的联调。
- 未在 Windows/macOS 机器实测；使用跨平台 Python 标准库，并给出 PowerShell / Bash 命令。

## 演示数据说明

`fixtures/demo_bulk.xml` 和 `examples/demo_companies.csv` 内的名称、ABN、ASIC number 全部用于测试，不是实际客户。演示输出不附虚构企业的查询链接。

真实运行步骤见 `README_zh.md`。API 模式需要用户自己的 GUID；bulk 模式需要用户下载同一批次所有官方数据分卷。
