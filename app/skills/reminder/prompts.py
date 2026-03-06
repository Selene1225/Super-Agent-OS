"""LLM prompt templates for the Reminder skill."""

# ─── Time extraction prompt (for "set" action) ──────────────────────────

EXTRACT_TIME_PROMPT = """\
你是一个时间解析助手。用户会用自然语言描述一个提醒需求。
请从中提取出 **提醒时间** 和 **提醒内容**，以JSON格式输出。

规则:
1. 当前时间: {now}
2. 输出严格JSON格式，不要包含其他文字。
3. 时间字段 "remind_at" 为 ISO 8601 格式: "YYYY-MM-DDTHH:MM"
4. 内容字段 "content" 为提醒文本
5. 如果用户没有明确说年份，默认为当前年份；如果时间已过，推到明年
6. "明天" = 当前日期+1天, "后天" = +2天, "下周一" = 下一个周一, etc.
7. 如果无法解析出合理的时间，返回 {{"error": "无法解析时间"}}

示例:
用户: "3月10号下午3点提醒我开会"
输出: {{"remind_at": "2026-03-10T15:00", "content": "开会"}}

用户: "明天早上9点提醒我给老板打电话"
输出: {{"remind_at": "2026-03-07T09:00", "content": "给老板打电话"}}

用户: "半小时后提醒我吃药"
输出: {{"remind_at": "2026-03-06T16:30", "content": "吃药"}}
"""


# ─── Match existing reminder prompt (for "update" / "cancel") ────────────

MATCH_REMINDER_PROMPT = """\
你是一个提醒管理助手。用户想要修改或删除一个已有的提醒。
下面是用户当前的待执行提醒列表：

{reminders_block}

用户说："{user_message}"

请判断用户想操作哪个提醒，并提取出修改信息。以严格JSON格式输出，不要包含其他文字。

规则:
1. 当前时间: {now}
2. "index" 是上面列表中的序号（从1开始）
3. 如果用户想修改时间，提供 "new_remind_at"（ISO 8601: "YYYY-MM-DDTHH:MM"）
4. 如果用户想修改内容，提供 "new_content"
5. 如果用户想删除/取消提醒，设置 "delete": true
6. 如果无法确定是哪个提醒，返回 {{"error": "无法确定要操作的提醒"}}
7. 如果只说了"改成几点"而没说日期，保持原来的日期，只改时间

输出格式：
- 修改时间: {{"index": 1, "new_remind_at": "2026-03-06T20:00"}}
- 修改内容: {{"index": 1, "new_content": "新的内容"}}
- 同时修改: {{"index": 1, "new_remind_at": "2026-03-06T20:00", "new_content": "新内容"}}
- 删除提醒: {{"index": 1, "delete": true}}

示例:
提醒列表: 1. 2026-03-06 19:10 — 报名考试和一个考试时间确认
用户: "改成8点提醒我"
输出: {{"index": 1, "new_remind_at": "2026-03-06T20:00"}}

用户: "把报名考试那个取消掉"
输出: {{"index": 1, "delete": true}}

用户: "第二个提醒改成明天"
输出: {{"index": 2, "new_remind_at": "2026-03-07T09:00"}}
"""
