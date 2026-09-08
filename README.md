# tavern_preset_regex 酒馆兼容插件

插件只做两件事：

1. 直接读取、编辑并注入 `data/tavern_preset_regex/setvar.json` 中的 SillyTavern 变量提示词。
2. 直接读取、编辑并应用 `data/tavern_preset_regex/regex.json` 中的 SillyTavern 正则脚本。

两者都只在**发送给主回复模型**的 LLM 请求和主回复模型返回结果上生效，不修改平台层
的原始消息对象。

## WebUI

插件会挂载独立编辑页：

```text
/plugins/tavern-preset-regex/
```

页面包含“预设”和“正则”两个标签，可直接查看、新增、编辑、删除并保存
`data/tavern_preset_regex/setvar.json` 与 `data/tavern_preset_regex/regex.json` 中的条目。
两个表格的每行都有 ↑/↓ 按钮，可调整条目顺序：预设顺序决定注入主回复请求时
的系统提示词拼接顺序，正则顺序决定规则的先后执行顺序（前一条的替换结果会被
后一条继续处理）。

预设标签顶部始终包含三条 MoFox 固定条目：系统提示词、Tool 和用户上下文。
它们只读、不可删除或编辑，但可以和其他酒馆预设条目一起用 ↑/↓ 调整顺序。
酒馆预设条目的角色可通过下拉框选择 `system`、`user` 或 `assistant`。

- 角色为 `user` 的酒馆预设会作为临时的 `USER` payload 追加到本次请求，不会写入
  聊天流历史或进入 MoFox 的上下文记忆。
- 角色为 `assistant` 的酒馆预设会作为真正的 `ASSISTANT` payload 发送，可用于
  卡原生思维链预填充（如 `<think>think is over...</think>`、`<｜end▁of▁thinking｜>`）。
- MoFox 要求 assistant 不能出现在对话开头，也不能紧跟在另一条 assistant 之后。
  如果把 assistant 条目排到了非法位置，插件会把它降级为 `system` 并在
  `debug_log` 打开时记录一条提示，保证请求结构始终合法。
- 固定条目和酒馆预设的最终顺序完全由 WebUI 的 ↑/↓ 决定，即
  `setvar.json` 中的 `mofox_order`。

页面顶部支持“从文件导入并自动识别”，可以导入：

- SillyTavern 对话补全预设（`prompts` + `prompt_order`）
- 正则脚本列表
- 单条正则对象
- 角色卡中的 `extensions.regex_scripts`

导入时按 `identifier` / `id` 合并，不会清空现有条目。

导入 SillyTavern 预设时会自动转换适配 MoFox 请求：

- 跳过酒馆标记位提示词（`chatHistory`、`dialogueExamples`、`worldInfoBefore/After`、
  `charDescription`、`charPersonality`、`scenario`、`personaDescription`），这些位置
  在 MoFox 中由框架自身构建（人设注入 `<personality>`、历史消息在 user payload 中）；
- 采用导入预设自带的 `prompt_order` 顺序与启用状态；
- 首次导入时默认停用与 MoFox 主回复指令重复的内置提示词（`main` / `nsfw`），
  需要时可在 WebUI 或通过 `/setvar enable` 手动启用；
- `{{char}}` / `{{user}}` / setvar 等宏在注入时渲染，`injection_position` 的聊天内
  深度注入不单独支持，统一作为系统提示词注入。

## 作用范围

- `before_llm_request`：按 `mofox_order` 把启用的 setvar 预设条目注入请求，条目
  按自身角色分别成为 `SYSTEM` / `USER` / `ASSISTANT` payload，并对请求 payload
  执行 `input` 正则。
- `after_llm_request`：对主回复模型返回的 `message`，或 `tool_calls` 中可见文本
  字段执行 `output` 正则。
- 默认只处理 `request_name` 为 `default_chatter` 或 `neo_default_chatter` 的请求，
  可在配置中通过 `main_request_names` 调整。
- 可通过 `filter_mode`、`user_whitelist`、`user_blacklist`、`group_whitelist`、
  `group_blacklist` 按用户或群聊做白名单 / 黑名单过滤。
- `data_dir` 可配置 setvar.json 与 regex.json 的存放目录。

## 调试

`scripts/` 下有两个只用于排查的脚本，需要 neo-mofox 的虚拟环境：

```bash
# 查看注入后的角色序列与结构校验结果（在 neo-mofox 根目录执行）
neo-mofox/.venv/Scripts/python.exe ../tavern_preset_regex/scripts/diagnose_role.py

# 直接跑一遍 TavernRequestHandler，统计各角色 payload 数量
neo-mofox/.venv/Scripts/python.exe ../tavern_preset_regex/scripts/diagnose_handler.py
```

角色注入的回归测试：

```bash
neo-mofox/.venv/Scripts/python.exe -m pytest ../tavern_preset_regex/tests
```

## 命令

```text
/setvar
/setvar list
/setvar get <名称>
/setvar set <名称> <值>
/setvar clear <名称>
/setvar apply
/setvar prompts
/setvar enable <编号|名称|id>
/setvar disable <编号|名称|id>

/regex
/regex list
/regex show <编号|名称|id>
/regex enable <编号|名称|id>
/regex disable <编号|名称|id>
/regex set <编号|名称|id> <字段> <值>
```

`/regex set` 支持 `name`、`pattern`、`replacement`、`markdown`、`prompt`、
`placement` 字段。

`regex.json` 中的 `/pattern/flags`、`$1`、`${name}` 会自动转换为 Python 正则语法。

WebUI 正则表可以直接编辑以下酒馆字段：

- `仅输出显示` / `仅发送模型`：对应 SillyTavern 的 `markdownOnly` / `promptOnly`；
  前者只改显示文本，后者只改发送给模型的文本，两项同时勾选则两端都处理。
- `Placement`：酒馆 placement 数组，多个值用逗号分隔。
- `正则替换（不生效）`：`substituteRegex`，当前仅保存兼容，不改变实际执行行为。
- `Trim Strings`：执行正则前先移除的字符串，多个用逗号分隔。
