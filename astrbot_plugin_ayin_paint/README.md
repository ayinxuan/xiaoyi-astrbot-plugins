# astrbot_plugin_ayin_paint

按技能把中文描述转成 Danbooru tag，再叠当前画师串请求自定义生图接口。

## 指令
- `画 一只白发的猫娘` / `画 16:9 一只白发的猫娘`：转 tag 后出图
- `画师串`：列出画师串（★ 为当前启用）
- `画师串 用 写实`：切换画师串
- `画师串 设 名称=内容`：新增并启用
- `画师串 删 名称`：删除自定义画师串
- `画图状态`：查看请求模式、地址、模型、密钥（打码）、技能文件、当前画师串

## 配置要点
- `request_mode`：`auto` / `images`（OpenAI 图片接口）/ `chat`（网关式 chat/completions）
- `api_url`：填 `https://host/v1` 或完整端点，插件会自动补路径
- `model` / `api_key`：生图模型与密钥
- `stream`：chat 模式的 SSE 流式开关
- `extra_body`：需要额外请求参数时填 JSON
- `enable_tag` / `skill_path`：是否转 tag 以及使用哪个技能文件
- `enable_lookup`：出图前先联网核对角色，锁定 Danbooru 官方角色标签（避免画错人或乱补外貌）
- `lookup_min_posts` / `lookup_max_names`：角色标签最低投稿数、最多核对几个角色
- `danbooru_base` / `search_api_key`：Danbooru 接口地址、可选 Tavily 密钥（查不到时联网补参考）
- `artist_presets` / `active_preset`：画师串预设与默认启用项

## LLM 工具
注册 `paint_image(prompt, aspect_ratio)`，供 Bot 在对话里直接调画出图。
