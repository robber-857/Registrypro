# 悉尼按地区导出企业名称

这套 Python 脚本把指定地区的 **Active ABN 主体名称、注册 business name 和公开邮编** 导出为 Excel 可打开的 CSV，方便逐个了解潜在客户。使用澳洲官方 ABN Lookup / ABN Bulk Extract 数据。

核对日期：2026-11-16。Python 3.10 或更新版本；仅使用标准库，**不用 pip 安装依赖**。

## 先了解结果的范围

1. **地区依据是主营业地点的 state + postcode。** 官方公开 ABN 数据不提供完整街道地址、准确 suburb、LGA、电话、邮箱、网站或行业代码。suburb 输入只是换算到邮编，同邮编的其他 suburb 也会被查到。[1][2]
2. **默认只保留 Active ABN 下的 PRV/PUB 类型**，即 Australian Private Company / Australian Public Company。需要 sole trader、partnership、trust 等类型时，使用 `--entity-types ALL`。这里的结果是 ABN 企业候选名单，不是 ASIC 全部公司登记的穷尽清单。[2][3]
3. **legal name 和 business name 分开保存。** 同一 ABN 有多个 registered business name 时，每个名称一行；没有 business name 时用 legal name 作为展示名，并标为 `legal_name_fallback`，business_name 列留空。历史 trading names 不冒充当前注册 business names；官方提醒这类旧 trading names 自 2012 年以来未更新。[4]
4. ABN 为 Active 只表示 ABN 状态；不证明商家目前开门营业，也不证明登记邮编对应可拜访的门店。
5. CSV 里的 Google 和 Google Maps 链接是**生成的搜索入口**，不是自动查证的官网或准确门店匹配。地址、网站、公开电话留空，供后续核实。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `sydney_leads.py` | 命令行入口 |
| `abr_api.py` | 官方 API 查询、重试、缓存 |
| `abr_bulk.py` | 本地 XML / ZIP / XML.GZ 流式解析 |
| `lead_common.py` | 共用错误处理和时间函数 |
| `suburbs_example.csv` | 可编辑的 suburb → postcode 示例对照表及来源 |
| `postcodes_example.txt` | 可编辑的批量邮编清单 |
| `fixtures/demo_bulk.xml` | 完全虚构的测试数据，不能当作客户名单 |
| `examples/demo_companies.csv` | 虚构数据生成的输出格式示例 |
| `test_*.py` | 解析、筛选、缓存、错误处理等测试 |
| `VERIFICATION.md` | 本次验证范围和实际限制 |

请解压整个目录，保留几个 Python 文件在同一个目录。以下命令都在该目录内执行。Windows 可将 `python` 替换为 `py -3`；macOS/Linux 常用 `python3`。

## 1. 先用虚构数据确认能运行

```bash
python sydney_leads.py demo --out demo.csv
```

这会生成 `demo.csv` 和 `demo.meta.json`。默认测试结果为 **2 个 ABN、3 行名称**；DEMO 名称和数据来源都明确标注为虚构，搜索链接为空。

```bash
python sydney_leads.py --help
python sydney_leads.py api --help
python sydney_leads.py suburbs
python sydney_leads.py sources
```

## 2. 方式 A：官方 API，适合先查几个区

### 2.1 免费申请 ABN Lookup GUID

打开 [官方 Web services 页面](https://abr.business.gov.au/Tools/WebServices)，阅读说明及服务协议，完成注册。官方说明服务免费，申请处理后会把 authentication GUID 发到你的邮箱。[5]

**自己的 ABN 不是 API GUID。** 无须把 GUID 发给别人，设置在本机当前终端即可。

Windows PowerShell：

```powershell
$env:ABR_GUID = "在这里填你收到的GUID"
python sydney_leads.py api --suburbs Chatswood Burwood --limit 100 --out first_100.csv
```

macOS / Linux：

```bash
export ABR_GUID='在这里填你收到的GUID'
python3 sydney_leads.py api --suburbs Chatswood Burwood --limit 100 --out first_100.csv
```

`--limit 100` 表示最多导出 **100 个符合筛选条件的 ABN 主体**，名称行数可能超过 100。结果会明确标记 `limited`。顺序由源数据决定，前 100 个不代表最新或最优客户。

### 2.2 按邮编导出

```bash
python sydney_leads.py api --postcodes 2000 2067 2134 2150 --out sydney_companies.csv
```

脚本先调用 `SearchByPostcode` 取得 active ABN 清单，再逐个调用 `SearchByABNv202001` 取得名称和实体详情，并再次检查州、邮编、状态。[6]

内置示例范围：

| 输入 suburb | 使用的邮编 | 范围说明 |
| --- | --- | --- |
| Sydney / Haymarket | 2000 | 也可能包含这个邮编的其他地点 |
| Chatswood / Chatswood West | 2067 | 无法仅凭 ABN 数据拆分两个 suburb |
| Burwood / Burwood North | 2134 | 不是 Burwood Council 的完整边界 |
| Parramatta / Harris Park | 2150 | 不是 City of Parramatta 的完整边界 |

这是方便试运行的小型对照表，**不是全悉尼 suburb 清单**。可查任何四位澳洲邮编，州默认 NSW。不要把 `2000–2999` 当成悉尼范围：邮编不是城市或 council 边界。

### 2.3 包含更多类型的商家

```bash
python sydney_leads.py api --suburbs Burwood --entity-types ALL --out burwood_all_entities.csv
```

`ALL` 会包括 sole trader、partnership、trust 等所有 ABN 实体类型，也可能带入不适合拓客的基金、机构等。若只想增加 sole trader：

```bash
python sydney_leads.py api --suburbs Burwood --entity-types PRV PUB IND --out burwood_businesses.csv
```

### 2.4 名称关键词初筛

```bash
python sydney_leads.py api --postcodes 2000 2067 --name-contains dental beauty restaurant --out name_matches.csv
```

逻辑是：legal name 或任一 registered business name 包含任一关键词，则保留这个主体及其所有 registered business names。**不是行业分类**，同一主体附带导出的其他名称不一定含关键词。普通的控股公司名称也可能完全不体现真实业务。

### 2.5 缓存与续跑

- 默认网络请求至少间隔 1 秒，这是脚本设置，不是官方公布的配额。
- ABN 详情默认缓存 7 天；邮编清单缓存最多 1 小时。每条成功结果即时写入本地 SQLite。
- 重新执行相同命令会复用有效缓存，因此中断后不用重新请求已经缓存的详情。程序会重新生成输出 CSV。
- 同名正式输出默认不覆盖。确认要替换时，加 `--overwrite`；要重新获取最新 API 数据，再加 `--refresh`。
- 输入错误、GUID 错误、返回数量不一致、接口失败会明确报错，不会伪装成“这个区没有公司”。

```bash
python sydney_leads.py api --postcodes 2067 --out chatswood.csv
python sydney_leads.py api --postcodes 2067 --out chatswood.csv --overwrite --refresh
```

大量首次查询需要一条 ABN 一次详情请求；如果准备反复查询很多地区，下面的 bulk 模式更合适。缓存是一种有时间差的数据复用，不是同步实时视图；每行保留获取时间。

## 3. 方式 B：官方批量数据，无需 GUID

### 3.1 获取官方文件

打开 [ABN Bulk Extract 数据集](https://data.gov.au/data/dataset/abn-bulk-extract)，下载**同一个发布批次的所有数据分卷**，保存在专用文件夹，例如 `abr_data`。

官方说明每周发布 XML 快照。[1] 下载量以网页当时的文件大小为准。不要只下载其中一卷就把结果当成整个地区的全部记录，也不要将不同周的快照混在一个目录。

下载目录只放数据 XML / ZIP / XML.GZ；schema 或其他示例 ZIP 放到别处。**ZIP 不需要解压**，脚本会直接流式读取里面的 XML。

### 3.2 本地筛选

```bash
python sydney_leads.py bulk --input ./abr_data --postcodes 2000 2067 2134 2150 --out sydney_from_bulk.csv
```

也可以明确指定多个文件，文件名以你实际下载的为准：

```bash
python sydney_leads.py bulk --input ./abr_data/part1.zip ./abr_data/part2.zip --suburbs Chatswood Burwood --out local_leads.csv
```

脚本逐条处理 XML，不将整个全国数据文件一次性装入内存。它会扫描提供的所有数据文件，按 ABN + 展示名去重；对已匹配主体的冲突名称记录会报错，避免把不同快照的名称静默合并。程序不能证明你下载了当期的所有分卷，所以 metadata 不会虚称“已验证全量”。

需要重新换区时，修改参数再运行即可；这是重新扫描本地文件，不需要 GUID。当前版本未建立全国范围的数据库索引。

## 4. 添加自己的地区

### 邮编清单

编辑 `postcodes_example.txt`，每行一个邮编，可用 `#` 写注释：

```bash
python sydney_leads.py api --postcodes-file postcodes_example.txt --out my_areas.csv
```

### 自定义 suburb 对照表

CSV 至少包含这三列；同一 suburb 对应多个邮编时可以填写多行：

```csv
suburb,state,postcode
Chatswood,NSW,2067
Burwood,NSW,2134
```

```bash
python sydney_leads.py api --suburb-map my_suburbs.csv --suburbs Chatswood Burwood --out custom_areas.csv
```

自定义表覆盖同名 suburb 的内置映射。未知 suburb 会报错并让你提供邮编，程序不会猜测。可以用 [Australia Post postcode finder](https://auspost.com.au/postcode) 核对映射。[7]

若你说的“区”是 **LGA / Council**，可以先把目标区对应邮编整理成查询清单，以此生成候选企业；邮编可能跨越 council，最终精确归属需要公开营业地址等额外数据。当前脚本不声称支持精确 LGA 边界筛选。

## 5. 输出字段

| CSV 字段 | 含义 |
| --- | --- |
| `display_name` | 优先显示 business name，没有时显示 legal name |
| `business_name` | registered business name；没有就留空 |
| `legal_name` | 法定主体名称，sole trader 可能是登记个人姓名 |
| `name_type` | `registered_business_name` 或 `legal_name_fallback` |
| `abn` | 企业 ABN；同一 ABN 可能多行 |
| `asic_number` | 官方 ASICNumber 字段，可能是 ACN/ARBN/ARSN/ARFN |
| `entity_type_code` / `entity_type` | 主体类型代码和说明 |
| `abn_status` | 筛选后应为 Active |
| `abn_status_from` | 当前 ABN 状态的起始日；不是公司首次成立日 |
| `gst_registered` | Yes / No / Unknown；不确定或被隐去时不猜测 |
| `state` / `postcode` | 官方主营业地点的州和邮编 |
| `requested_suburbs` | 你输入并映射到该邮编的地区标签，**不是核实后的企业 suburb** |
| `location_precision` | 明确标注仅有州和邮编 |
| `street_address` / `website` / `public_phone` | 预留空列，等待后续核实 |
| `google_search_url` | 按名称 + 州 + 邮编生成的 Google 搜索链接 |
| `google_maps_search_url` | 同样条件的地图搜索链接，需核实是否同一主体 |
| `abn_lookup_url` | 官方 ABN 详情页 |
| `data_source` | 官方 API、官方 bulk，或者明确标记的虚构示例 |
| `source_updated_at` | 数据源提供的主体更新日期 |
| `retrieved_at` | API 响应时间，或 bulk 文件本次读取时间；不是 bulk 快照发布日期 |
| `source_file` | bulk 来源文件及 ZIP 成员路径，便于追查 |

CSV 使用 UTF-8 BOM，适合用 Excel 打开。对于以公式标记开头的外部文本，程序会加文本前缀，避免 Excel 把商家名称执行为公式。

同时生成 `文件名.meta.json`，记录筛选条件、扫描数量、输出 ABN 数、名称行数、是否被 `--limit` 截断、API 请求信息、数据覆盖限制。

中断或失败时，只保留 `文件名.partial.csv` 和 `文件名.partial.meta.json`，退出码非零。已有正式输出不会被失败任务替换。只查看这次运行的 metadata，避免把上一次完整结果和这次部分结果混淆。

## 6. 建议的拓客用法

先选你方便接触的两三个邮编，导出一批 business names，然后通过搜索链接核对官网、店铺地址、公开联系方式和经营内容。对 Eltonex 的网站、预约系统、Shopify、业务自动化服务，可以增加“当前网站情况”“可能需要的服务”“优先级”“跟进状态”等手工字段。

当前脚本负责生成可追溯的注册主体候选名单。它不自动判断有没有网站，不给同名商家自动合并地址，也不自动发送任何营销信息。

## 7. 验证与已知限制

```bash
python -m unittest discover -v
```

测试使用明确标注的合成 XML 和模拟 API 响应，覆盖正常导出、多个名称、IND、取消状态、跨州、命名空间、ZIP/GZIP、文件损坏、API 返回截断、缓存和错误恢复。详见 `VERIFICATION.md`。

本次没有用户 GUID，**未做真实认证 API 查询**；本环境访问 data.gov.au 数据集下载端返回 403，**未下载或验证当期全量快照**。官方 ABN 方法、字段和当前 API XSD 已核对，交付的 demo 只展示格式，不构成真实潜客名单。运行时若官方接口或文件格式变化，程序会尽量明确失败，而不是返回一个看似成功的空表。

## 官方来源

[1] ABN Lookup — Bulk extract：每周发布、字段范围。  
https://abr.business.gov.au/Tools/BulkExtract

[2] ABR — Your ABN details on the ABR：公开与非公开字段的区别。  
https://www.abr.gov.au/business-super-funds-charities/applying-abn/your-abn-details-abr

[3] ABN Lookup — Entity types。  
https://abr.business.gov.au/Help/EntityTypeList

[4] ABN Lookup — Web services response：多名称、隐藏字段及 legacy trading names。  
https://abr.business.gov.au/Documentation/WebServiceResponse

[5] ABN Lookup — Web services：免费服务及 GUID 注册。  
https://abr.business.gov.au/Tools/WebServices

[6] ABN Lookup — Web services methods：SearchByPostcode 及 SearchByABNv202001。  
https://abr.business.gov.au/Documentation/WebServiceMethods  
https://abr.business.gov.au/abrxmlsearch/Forms/SearchByPostcode.aspx  
https://abr.business.gov.au/abrxmlsearch/Forms/SearchByABNv202001.aspx

[7] Australia Post — postcode finder；详细示例来源见 `suburbs_example.csv`。  
https://auspost.com.au/postcode

[8] 官方 bulk 数据集及 README：  
https://data.gov.au/data/dataset/abn-bulk-extract  
https://data.gov.au/data/dataset/5bd7fcab-e315-42cb-8daf-50b7efc2027e/resource/3b975e4f-af2f-4de7-a20e-6dcb3f67805c/download/ABNLookupBulkExtractReadme.pdf

[9] 当前官方 API schema 下载：  
https://abr.business.gov.au/Documentation/DownloadCompressedFile?downloadAs=AbnLookupSchema.zip&filename=AbnLookupSchema.zip

源数据由 Australian Business Register / ABN Lookup 提供；脚本为独立工具，不代表政府认可。


## ABN 官方名单入口（2026-11-17）

在「名单来源」选择「ABN 官方公开数据（Active 主体）」，输入州、邮编及数量即可查询。本机必须先准备索引；不需要 ABN Lookup GUID。地图入口保持可用。

首次或每周更新，在项目根目录运行：

```powershell
python sydney_business_leads/update_abn_data.py
```

脚本从官方 data.gov.au 元数据定位两个 ZIP，核对下载大小，完整解析 20 个 XML 后原子替换 SQLite 索引。更新失败保留旧索引；页面展示快照日期。约 1 GB 下载，本机索引另外占用磁盘空间。`output/abn_data` 为本地数据，不进入代码仓库。

名单规则：仅保留 ACT ABN，按主要经营地点的州和邮编精确筛选。法定名称与 OtherEntity 中 BN 类型的 Business Name 分开；TRD 旧交易名称不冒充商号。同一个 ABN 的多个商号各占一行，数量上限按主体计，所以 20 个主体可能多于 20 行。没有商号的主体保留并跳过 ASIC 续期查询。

ASIC 编号单独保留原值和类型。明确 ACN 直接提取；`undetermined` 仅在澳大利亚私人/公众公司、编号为九位、校验位有效且与 ABN 后九位一致时归类为 ACN，并在备注说明依据。ARBN 等其他类型不放入 ACN。

连续任务沿用上方名单来源。先连接 ASIC 插件并勾选等待连续队列，再在网页勾选「每批等待 ASIC 插件核对后保存并推进邮编」，设置起止邮编后启动。ABN 名单不读取商家网站或重新模糊搜索持有人；商号及 ABN 直接进入插件。每批最多 20 行；完成核对后保存 CSV，当前邮编全部分页处理后推进下一个邮编。Cancelled 及异常处理沿用现有插件逻辑。重启本机服务后需使用网页当前配对码重新连接插件。

官方快照 Active 是 ABN 状态，不证明商号状态仍为 Registered，也不是实际门店清单。它没有行业分类、街道地址、电话或网站，不提供 Renewal Date。续期日期仍由 ASIC 查询；遇到真正人机验证仍需要手动完成。

官方来源： https://abr.business.gov.au/Tools/BulkExtract
