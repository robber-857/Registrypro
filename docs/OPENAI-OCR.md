# ASIC 截图识别配置

本机配置文件：`output/openai_ocr.json`。文件只在本机读取，不提交仓库。不要把 API key 发送到聊天、插件配对码框或 GitHub。

```json
{
  "enabled": false,
  "model": "填写支持图像和结构化输出的模型名称",
  "api_key": "填写你的 OpenAI API key"
}
```

填好后将 enabled 改为 true，再开始新的插件队列。可用服务进程的 OPENAI_API_KEY 环境变量替代文件内 api_key。无需为配置变化重启服务；队列创建时选择截图优先模式。

开启后只把当前任务的 ASIC 页截图发送到 OpenAI 官方 Responses API（https://api.openai.com/v1/responses），store=false。使用 input_image 和严格 JSON schema，仍校验官方 URL、名称、登记状态及日期。API 失败不猜测字段、不无限付费重试，也不静默切换供应商。图像处理会产生 API 用量。

在 ASIC 标签页打开插件，点击“启用本次截图 OCR 备用”，插件会复用该标签页执行队列，避免新建标签丢失临时截图授权。保持查询页可见。截图只覆盖当前视口；未看见的字段不推断，未激活标签或无权限会明确说明。截图观察不是完整页面爬取，不能据一张部分截图认定所有分页均注销。页面内已读取的列表继续负责分页及链接定位。

未开启 OpenAI 时保留本机 RapidOCR/Windows OCR。列表读取异常时尝试截图，140 秒尚未完成时后台也尝试一次截图兜底；180 秒期限后记录最后异常和恢复次数并继续。真正验证码仍暂停，不通过 OCR 解验证码。

Excel 入口为“最近查询续期日 Excel”，包含“已确认续期日”和“注销候选及未完成”两张表；后者保留失败原因和观察记录。CSV 保留全部名单及状态。Cancelled 商号不等同于公司主体注销，Next review date 不能代替 Renewal date。

参考官方文档：https://developers.openai.com/api/docs/guides/images-vision 与 https://developers.openai.com/api/docs/guides/structured-outputs 。
